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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS_ROOT = Path("argocd")
TARGET_PREFIX = "$values/"


@dataclass
class RenderedState:
    manifest: Optional[str]
    chart: Optional[str]
    version: Optional[str]
    error: Optional[str]


class HelmDiffError(Exception):
    pass


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
    for source in sources:
        if isinstance(source, dict) and source.get("chart"):
            return source
    legacy = spec.get("source")
    if isinstance(legacy, dict) and legacy.get("chart"):
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


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "app"


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
    repo = chart_source.get("repoURL")
    version = chart_source.get("targetRevision")
    helm_cfg = chart_source.get("helm", {})
    value_refs = helm_cfg.get("valueFiles") or []
    if isinstance(value_refs, str):
        value_refs = [value_refs]
    skip_crds = helm_cfg.get("skipCrds", False)
    value_files, temp_dir = materialise_values(commit, list(value_refs))
    try:
        if not chart:
            raise HelmDiffError("Chart name is missing in application.yaml")
        args = [
            "helm",
            "template",
            release_name,
            chart,
            "--namespace",
            namespace,
        ]
        if repo:
            args.extend(["--repo", repo])
        if version:
            args.extend(["--version", str(version)])
        if skip_crds:
            args.append("--skip-crds")
        for vf in value_files:
            if vf.is_file():
                args.extend(["-f", str(vf)])
        result = run(args)
    except HelmDiffError as exc:
        return RenderedState(None, chart, version, str(exc))
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
    return RenderedState(result.stdout, chart, version, None)


def build_section(app: str, base_state: RenderedState, head_state: RenderedState) -> str:
    base_label = f"{base_state.chart or 'n/a'}@{base_state.version or 'n/a'}"
    head_label = f"{head_state.chart or 'n/a'}@{head_state.version or 'n/a'}"
    if base_state.chart is None and head_state.chart is None:
        title_suffix = ""
    elif base_state.chart is None:
        title_suffix = f" (new: {head_label})"
    elif head_state.chart is None:
        title_suffix = f" (removed: {base_label})"
    elif base_label == head_label:
        title_suffix = f" ({head_label})"
    else:
        title_suffix = f" ({base_label} → {head_label})"
    lines = [f"### {app}{title_suffix}"]
    if base_state.error or head_state.error:
        if base_state.error:
            lines.append(f"Base render failed:\n```text\n{base_state.error}\n```")
        if head_state.error:
            lines.append(f"PR render failed:\n```text\n{head_state.error}\n```")
        return "\n".join(lines)
    base_text = base_state.manifest or ""
    head_text = head_state.manifest or ""
    diff = list(
        difflib.unified_diff(
            base_text.splitlines(),
            head_text.splitlines(),
            fromfile=f"base/{app}",
            tofile=f"pr/{app}",
            lineterm="",
        )
    )
    if not diff:
        lines.append("No changes detected in rendered manifests.")
        return "\n".join(lines)
    lines.append("```diff")
    lines.append("\n".join(diff))
    lines.append("```")
    return "\n".join(lines)


def write_comments(entries: List[Tuple[str, str]], comment_dir: Path) -> List[dict]:
    comment_dir.mkdir(parents=True, exist_ok=True)
    for existing in comment_dir.glob("*.md"):
        try:
            existing.unlink()
        except OSError:
            pass
    manifest: List[dict] = []
    for app, body in entries:
        slug = slugify(app)
        path = comment_dir / f"{slug}.md"
        content = f"<!-- helm-diff:{slug} -->\n{body}\n"
        path.write_text(content)
        manifest.append({
            "app": app,
            "slug": slug,
            "path": str(path.relative_to(REPO_ROOT)),
        })
    manifest_path = comment_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def set_outputs(has_changes: bool, manifest: List[dict]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"has_changes={'true' if has_changes else 'false'}\n")
            fh.write(f"comment_manifest={json.dumps(manifest)}\n")


def main() -> int:
    base = os.environ.get("BASE_SHA") or os.environ.get("BASE_REF") or "origin/main"
    head = os.environ.get("HEAD_SHA") or os.environ.get("HEAD_REF") or "HEAD"
    comment_dir = Path(os.environ.get("COMMENT_DIR", "helm-diff-comments"))
    try:
        changed_files = git_diff_files(base, head)
        apps = detect_changed_apps(base, head, changed_files)
        entries: List[Tuple[str, str]] = []
        for app in apps:
            base_state = render_state(base, app)
            head_state = render_state(head, app)
            entries.append((app, build_section(app, base_state, head_state)))
        manifest = write_comments(entries, comment_dir)
        set_outputs(bool(manifest), manifest)
    except HelmDiffError as exc:
        message = "Helm template diff failed:\n```text\n" + str(exc) + "\n```"
        manifest = write_comments([("error", message)], comment_dir)
        set_outputs(True, manifest)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
