#!/usr/bin/env python3
"""Render Helm templates for changed Argo CD apps and emit a PR comment."""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS_ROOT = Path("argocd")
TARGET_PREFIX = "$values/"
MAX_COMMENT_LENGTH = 64000  # Safety margin under GitHub's 65,536 character limit
PREVIEW_CHAR_LIMIT = 4000
DEFAULT_HELM_API_VERSIONS = ["monitoring.coreos.com/v1"]


@dataclass
class RenderedState:
    manifest: Optional[str]
    chart: Optional[str]
    version: Optional[str]
    error: Optional[str]


class HelmDiffError(Exception):
    pass


@dataclass
class CommentEntry:
    app: str
    source: str
    body: str


def run(cmd: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise HelmDiffError(result.stderr.strip() or result.stdout.strip())
    return result


def git_read(commit: str, path: Path) -> Optional[str]:
    target = f"{commit}:{path.as_posix()}"
    try:
        result = run(["git", "show", target], check=False)
    except HelmDiffError:  # pragma: no cover - defensive
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def git_diff_files(base: str, head: str) -> List[Path]:
    result = run(["git", "diff", "--name-only", base, head, "--", APPS_ROOT.as_posix()], check=False)
    if result.returncode not in (0, 1):
        raise HelmDiffError(result.stderr or result.stdout)
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def git_file_status(base: str, head: str, path: Path) -> Optional[str]:
    result = run(["git", "diff", "--name-status", base, head, "--", path.as_posix()], check=False)
    if result.returncode not in (0, 1):
        raise HelmDiffError(result.stderr or result.stdout)
    line = result.stdout.strip()
    return line.split()[0] if line else None


def target_revision_changed(base: str, head: str, path: Path) -> bool:
    result = run(["git", "diff", base, head, "--", path.as_posix()], check=False)
    if result.returncode not in (0, 1):
        raise HelmDiffError(result.stderr or result.stdout)
    if re.search(r"targetRevision", result.stdout):
        return True
    status = git_file_status(base, head, path)
    return status in {"A", "D", "R", "C"}


def detect_changed_apps(base: str, head: str, files: Iterable[Path]) -> List[str]:
    apps = set()
    for path in files:
        if path.parts[0] != APPS_ROOT.name or len(path.parts) < 3:
            continue
        app = path.parts[1]
        leaf = path.parts[2]
        if leaf == "values.yaml":
            apps.add(app)
        elif leaf == "application.yaml" and target_revision_changed(base, head, path):
            apps.add(app)
    return sorted(apps)


def parse_application(raw: str) -> dict:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:  # pragma: no cover - pass-through
        raise HelmDiffError(f"Failed to parse application.yaml: {exc}")
    if not isinstance(data, dict):
        raise HelmDiffError("application.yaml did not parse to a mapping")
    return data


def find_chart_source(spec: dict) -> Optional[dict]:
    sources = spec.get("sources") or []
    if isinstance(sources, dict):  # defensive for legacy single source layout
        sources = [sources]
    helm_candidates: List[dict] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        if source.get("chart"):
            return source
        if source.get("helm"):
            helm_candidates.append(source)
    if helm_candidates:
        return helm_candidates[0]
    legacy = spec.get("source")
    if isinstance(legacy, dict) and (legacy.get("chart") or legacy.get("helm")):
        return legacy
    return None


def materialise_values(commit: str, references: List[str]) -> tuple[List[Path], Optional[Path]]:
    if not references:
        return [], None
    base_path = Path(tempfile.mkdtemp(prefix="helm-values-"))
    files: List[Path] = []
    for idx, ref in enumerate(references):
        rel_path = ref.replace(TARGET_PREFIX, "") if ref.startswith(TARGET_PREFIX) else ref
        content = git_read(commit, Path(rel_path))
        if content is None:
            continue
        target = base_path / f"values-{idx}.yaml"
        target.write_text(content)
        files.append(target)
    return files, base_path


def materialise_chart_from_repo(repo_url: str, chart_subpath: str, revision: Optional[str]) -> tuple[Path, Path]:
    if not repo_url:
        raise HelmDiffError("repoURL is required when using path-based Helm sources")
    chart_subpath = (chart_subpath or "").strip()
    if not chart_subpath:
        raise HelmDiffError("path is required when using repoURL-based Helm sources")

    checkout_root = Path(tempfile.mkdtemp(prefix="helm-chart-"))
    repo_dir = checkout_root / "repo"
    run(["git", "clone", "--depth", "1", repo_url, str(repo_dir)])
    if revision:
        fetch_cmd = ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", revision]
        fetch_result = run(fetch_cmd, check=False)
        if fetch_result.returncode != 0:
            run(["git", "-C", str(repo_dir), "fetch", "origin", revision])
        run(["git", "-C", str(repo_dir), "checkout", revision])

    repo_root = repo_dir.resolve()
    chart_rel = Path(chart_subpath.lstrip("/"))
    chart_dir = (repo_root / chart_rel).resolve()
    try:
        chart_dir.relative_to(repo_root)
    except ValueError as exc:  # pragma: no cover - guard against path traversal
        raise HelmDiffError(f"Chart path '{chart_subpath}' escapes the repository checkout") from exc
    if not chart_dir.is_dir():
        revision_label = revision or "default"
        raise HelmDiffError(
            f"Chart path '{chart_subpath}' not found in repository {repo_url} (revision {revision_label})"
        )
    return chart_dir, checkout_root


def ensure_chart_dependencies(chart_dir: Path) -> None:
    if not chart_dir.is_dir():
        raise HelmDiffError(f"Chart directory does not exist: {chart_dir}")
    result = run(["helm", "dependency", "build", str(chart_dir)], check=False)
    if result.returncode != 0:
        raise HelmDiffError(
            "Failed to build chart dependencies:\n" + (result.stderr or result.stdout or "unknown error")
        )


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "app"


def extract_diff_preview(body: str, limit: int) -> str:
    if limit <= 0:
        return ""
    marker = "```diff"
    start = body.find(marker)
    if start == -1:
        return body[:limit]
    newline = body.find("\n", start)
    if newline == -1:
        return ""
    remainder = body[newline + 1 :]
    end = remainder.find("```")
    diff_section = remainder if end == -1 else remainder[:end]
    return diff_section[:limit]


def build_truncated_body(
    entry: CommentEntry,
    slug: str,
    *,
    preview_limit: int,
    artifact_path: str,
    original_length: int,
) -> str:
    lines = entry.body.splitlines()
    title = lines[0] if lines else f"## {entry.app}"
    preview = extract_diff_preview(entry.body, preview_limit).rstrip()
    message_lines = [
        title,
        "",
        f"WARNING: Rendered diff is too large for a GitHub comment ({original_length} characters).",
        "Download the workflow artifact from this run to review the full output.",
        f"- Full diff file: `{artifact_path}`",
    ]
    if preview:
        message_lines.extend(
            [
                "",
                "Preview:",
                "```diff",
                preview,
                "```",
                "",
                "_Preview truncated._",
            ]
        )
    return "\n".join(message_lines).strip()


def format_chart_suffix(base_state: RenderedState, head_state: RenderedState) -> str:
    base_label = f"{base_state.chart or 'n/a'}@{base_state.version or 'n/a'}"
    head_label = f"{head_state.chart or 'n/a'}@{head_state.version or 'n/a'}"
    if base_state.chart is None and head_state.chart is None:
        return ""
    if base_state.chart is None:
        return f" (new: {head_label})"
    if head_state.chart is None:
        return f" (removed: {base_label})"
    if base_label == head_label:
        return f" ({head_label})"
    return f" ({base_label} → {head_label})"


def split_manifest(manifest: Optional[str]) -> tuple[dict[str, List[str]], List[str]]:
    if not manifest:
        return {}, []
    docs: dict[str, List[str]] = defaultdict(list)
    order: List[str] = []
    for raw_doc in manifest.split("\n---\n"):
        doc = raw_doc.strip()
        if not doc:
            continue
        lines = doc.splitlines()
        source_line = next((line for line in lines if line.startswith("# Source: ")), None)
        source = source_line[len("# Source: ") :].strip() if source_line else "manifest"
        docs[source].append("\n".join(lines))
        order.append(source)
    return dict(docs), order


def render_state(commit: str, app: str) -> RenderedState:
    app_path = APPS_ROOT / app / "application.yaml"
    raw_app = git_read(commit, app_path)
    if raw_app is None:
        return RenderedState(None, None, None, None)
    data = parse_application(raw_app)
    metadata = data.get("metadata", {})
    spec = data.get("spec", {})
    chart_source = find_chart_source(spec)
    if not chart_source:
        return RenderedState(None, None, None, "No Helm chart source found")
    release_name = metadata.get("name", app)
    namespace = (spec.get("destination") or {}).get("namespace", "default")
    chart = chart_source.get("chart")
    chart_path = chart_source.get("path")
    repo = chart_source.get("repoURL")
    version = chart_source.get("targetRevision")
    helm_cfg = chart_source.get("helm", {})
    value_refs = helm_cfg.get("valueFiles") or []
    if isinstance(value_refs, str):
        value_refs = [value_refs]
    skip_crds = helm_cfg.get("skipCrds", False)
    value_files, temp_dir = materialise_values(commit, list(value_refs))
    chart_temp_dir: Optional[Path] = None
    chart_label = chart or chart_path
    try:
        chart_arg = chart
        repo_arg = repo
        allow_version_flag = True
        if not chart_arg:
            if chart_path:
                chart_dir, chart_temp_dir = materialise_chart_from_repo(repo or "", chart_path, version)
                ensure_chart_dependencies(chart_dir)
                chart_arg = str(chart_dir)
                repo_arg = None
                allow_version_flag = False
            else:
                raise HelmDiffError("Chart name is missing in application.yaml")
        args = [
            "helm",
            "template",
            release_name,
            chart_arg,
            "--namespace",
            namespace,
        ]
        if repo_arg:
            args.extend(["--repo", repo_arg])
        if version and allow_version_flag:
            args.extend(["--version", str(version)])
        if skip_crds:
            args.append("--skip-crds")
        for api_version in DEFAULT_HELM_API_VERSIONS:
            args.extend(["--api-versions", api_version])
        for vf in value_files:
            if vf.is_file():
                args.extend(["-f", str(vf)])
        result = run(args)
    except HelmDiffError as exc:
        return RenderedState(None, chart_label, version, str(exc))
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if chart_temp_dir:
            shutil.rmtree(chart_temp_dir, ignore_errors=True)
    return RenderedState(result.stdout, chart_label, version, None)


def build_entries(app: str, base_state: RenderedState, head_state: RenderedState) -> List[CommentEntry]:
    chart_suffix = format_chart_suffix(base_state, head_state)
    if base_state.error or head_state.error:
        lines = [f"### {app}{chart_suffix}"]
        if base_state.error:
            lines.append(f"Base render failed:\n```text\n{base_state.error}\n```")
        if head_state.error:
            lines.append(f"PR render failed:\n```text\n{head_state.error}\n```")
        return [CommentEntry(app=app, source="render-error", body="\n".join(lines))]

    base_docs, base_order = split_manifest(base_state.manifest)
    head_docs, head_order = split_manifest(head_state.manifest)
    seen_sources = set()
    ordered_sources: List[str] = []
    for source in base_order + head_order:
        if source not in seen_sources:
            seen_sources.add(source)
            ordered_sources.append(source)
    for source in list(base_docs.keys()) + list(head_docs.keys()):
        if source not in seen_sources:
            seen_sources.add(source)
            ordered_sources.append(source)

    entries: List[CommentEntry] = []
    for source in ordered_sources:
        base_list = base_docs.get(source, [])
        head_list = head_docs.get(source, [])
        max_len = max(len(base_list), len(head_list))
        if max_len == 0:
            continue
        for idx in range(max_len):
            base_doc = base_list[idx] if idx < len(base_list) else None
            head_doc = head_list[idx] if idx < len(head_list) else None
            if base_doc == head_doc:
                continue
            source_display = source if max_len == 1 else f"{source} [{idx + 1}]"
            status = ""
            if base_doc is None and head_doc is not None:
                status = " (added)"
            elif head_doc is None and base_doc is not None:
                status = " (removed)"
            title = f"### {app} — {source_display}{chart_suffix}{status}"
            diff = list(
                difflib.unified_diff(
                    base_doc.splitlines() if base_doc else [],
                    head_doc.splitlines() if head_doc else [],
                    fromfile=f"base/{source_display}",
                    tofile=f"pr/{source_display}",
                    lineterm="",
                )
            )
            body_lines = [title]
            if diff:
                body_lines.append("```diff")
                body_lines.append("\n".join(diff))
                body_lines.append("```")
            else:
                body_lines.append("No changes detected in rendered manifests.")
            entries.append(CommentEntry(app=app, source=source_display, body="\n".join(body_lines)))
    return entries


def write_comments(entries: List[CommentEntry], comment_dir: Path) -> List[dict]:
    comment_dir.mkdir(parents=True, exist_ok=True)
    for existing in comment_dir.glob("*.md"):
        try:
            existing.unlink()
        except OSError:
            pass
    manifest: List[dict] = []
    for entry in entries:
        slug_base = entry.app if entry.source == "app" else f"{entry.app}-{entry.source}"
        slug = slugify(slug_base)
        path = comment_dir / f"{slug}.md"
        raw_body = entry.body.rstrip()
        comment_content = f"<!-- helm-diff:{slug} -->\n{raw_body}\n"
        original_length = len(comment_content)
        metadata: dict[str, object] = {}
        if original_length > MAX_COMMENT_LENGTH:
            full_path = comment_dir / f"{slug}-full.md"
            full_path.write_text(comment_content)
            artifact_rel = str(full_path.relative_to(REPO_ROOT))
            preview_limit = PREVIEW_CHAR_LIMIT
            truncated_body = build_truncated_body(
                entry,
                slug,
                preview_limit=preview_limit,
                artifact_path=artifact_rel,
                original_length=original_length,
            )
            comment_content = f"<!-- helm-diff:{slug} -->\n{truncated_body}\n"
            while len(comment_content) > MAX_COMMENT_LENGTH and preview_limit > 0:
                preview_limit = max(preview_limit // 2, 0)
                truncated_body = build_truncated_body(
                    entry,
                    slug,
                    preview_limit=preview_limit,
                    artifact_path=artifact_rel,
                    original_length=original_length,
                )
                comment_content = f"<!-- helm-diff:{slug} -->\n{truncated_body}\n"
            if len(comment_content) > MAX_COMMENT_LENGTH:
                truncated_body = build_truncated_body(
                    entry,
                    slug,
                    preview_limit=0,
                    artifact_path=artifact_rel,
                    original_length=original_length,
                )
                comment_content = f"<!-- helm-diff:{slug} -->\n{truncated_body}\n"
            if len(comment_content) > MAX_COMMENT_LENGTH:
                raise HelmDiffError(
                    "Truncated comment still exceeds GitHub's limit; reduce rendered output."
                )
            metadata.update({
                "truncated": True,
                "full_path": artifact_rel,
                "original_length": original_length,
            })
        path.write_text(comment_content)
        manifest.append({
            "app": entry.app,
            "source": entry.source,
            "slug": slug,
            "path": str(path.relative_to(REPO_ROOT)),
            **metadata,
        })
    manifest_path = comment_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def group_entries(entries: List[CommentEntry]) -> List[CommentEntry]:
    if not entries:
        return []
    bodies_by_app: dict[str, List[str]] = {}
    order: List[str] = []
    for entry in entries:
        if entry.app not in bodies_by_app:
            bodies_by_app[entry.app] = []
            order.append(entry.app)
        bodies_by_app[entry.app].append(entry.body)
    grouped: List[CommentEntry] = []
    for app in order:
        combined_sections = "\n\n".join(bodies_by_app[app])
        combined = f"## {app}\n\n{combined_sections}" if combined_sections else f"## {app}"
        grouped.append(CommentEntry(app=app, source="app", body=combined))
    return grouped


def set_outputs(has_changes: bool, manifest: List[dict]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"has_changes={'true' if has_changes else 'false'}\n")
            fh.write(f"comment_manifest={json.dumps(manifest)}\n")


def main() -> int:
    base = os.environ.get("BASE_SHA") or os.environ.get("BASE_REF") or "origin/main"
    head = os.environ.get("HEAD_SHA") or os.environ.get("HEAD_REF") or "HEAD"
    raw_comment_dir = Path(os.environ.get("COMMENT_DIR", "helm-diff-comments"))
    comment_dir = raw_comment_dir if raw_comment_dir.is_absolute() else (REPO_ROOT / raw_comment_dir)
    comment_dir = comment_dir.resolve()
    try:
        comment_dir.relative_to(REPO_ROOT)
    except ValueError as exc:  # Defensive guard to keep outputs inside repo checkout
        raise HelmDiffError(f"Comment directory must reside within the repository: {comment_dir}") from exc
    try:
        changed_files = git_diff_files(base, head)
        apps = detect_changed_apps(base, head, changed_files)
        entries: List[CommentEntry] = []
        for app in apps:
            base_state = render_state(base, app)
            head_state = render_state(head, app)
            entries.extend(build_entries(app, base_state, head_state))
        entries = group_entries(entries)
        manifest = write_comments(entries, comment_dir)
        set_outputs(bool(entries), manifest)
    except HelmDiffError as exc:
        message = "Helm template diff failed:\n```text\n" + str(exc) + "\n```"
        manifest = write_comments([CommentEntry(app="error", source="render", body=message)], comment_dir)
        set_outputs(True, manifest)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
