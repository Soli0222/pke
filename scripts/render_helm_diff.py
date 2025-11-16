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
from typing import Dict, Iterable, List, Optional, Sequence, Set

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS_ROOT = Path("argocd")
TARGET_PREFIX = "$values/"
MAX_COMMENT_LENGTH = 64000  # Safety margin under GitHub's 65,536 character limit
PREVIEW_CHAR_LIMIT = 4000
DEFAULT_HELM_API_VERSIONS = ["monitoring.coreos.com/v1"]
VALUE_REF_PATTERN = re.compile(r"^\$(?P<ref>[^/]+)/(?P<path>.+)$")


@dataclass
class MaterializedRepo:
    kind: str  # "local" or "remote"
    commit: Optional[str]
    root: Optional[Path]
    repo_url: Optional[str]
    target_revision: Optional[str]
    cleanup_path: Optional[Path] = None

    def is_local(self) -> bool:
        return self.kind == "local"


LOCAL_REPO_URLS: Optional[Set[str]] = None


def _normalise_repo_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    value = url.strip()
    if value.startswith("git@") and ":" in value:
        host, path = value.split(":", 1)
        value = host.split("@", 1)[1] + "/" + path
    else:
        value = re.sub(r"^[a-zA-Z0-9+.-]+://", "", value)
    if value.endswith(".git"):
        value = value[:-4]
    return value.rstrip("/")


def _get_local_repo_urls() -> Set[str]:
    global LOCAL_REPO_URLS
    if LOCAL_REPO_URLS is not None:
        return LOCAL_REPO_URLS
    urls: Set[str] = set()
    result = run(["git", "remote", "-v"], check=False)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                normalised = _normalise_repo_url(parts[1])
                if normalised:
                    urls.add(normalised)
    LOCAL_REPO_URLS = urls
    return LOCAL_REPO_URLS


def is_local_repo(repo_url: Optional[str]) -> bool:
    if not repo_url:
        return True
    normalised = _normalise_repo_url(repo_url)
    return normalised in _get_local_repo_urls()


def git_list_files(commit: str, rel_path: str) -> List[str]:
    path_arg = rel_path.rstrip("/") if rel_path else ""
    args = ["git", "ls-tree", "-r", "--name-only", commit]
    if path_arg:
        args.extend(["--", path_arg])
    result = run(args, check=False)
    if result.returncode not in (0, 129):  # 129 when path missing
        raise HelmDiffError(result.stderr or result.stdout or "git ls-tree failed")
    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return sorted(files)


def clone_repo(repo_url: str, revision: Optional[str]) -> tuple[Path, Path]:
    if not repo_url:
        raise HelmDiffError("repoURL is required when cloning a repository")
    checkout_root = Path(tempfile.mkdtemp(prefix="helm-repo-"))
    repo_dir = checkout_root / "repo"
    run(["git", "clone", "--depth", "1", repo_url, str(repo_dir)])
    if revision and revision not in {"", "HEAD"}:
        fetch_cmd = ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", revision]
        fetch_result = run(fetch_cmd, check=False)
        if fetch_result.returncode != 0:
            run(["git", "-C", str(repo_dir), "fetch", "origin", revision])
        run(["git", "-C", str(repo_dir), "checkout", revision])
    return repo_dir, checkout_root


def export_local_repo_subpath(commit: str, subpath: str) -> tuple[Path, Path]:
    clean_subpath = (subpath or "").strip().lstrip("/")
    if not clean_subpath:
        raise HelmDiffError("path is required when exporting from the local repository")
    files = git_list_files(commit, clean_subpath)
    if not files:
        raise HelmDiffError(f"Path '{clean_subpath}' not found in local repository")
    checkout_root = Path(tempfile.mkdtemp(prefix="helm-local-"))
    chart_dir = checkout_root / "chart"
    for file_path in files:
        rel = file_path
        prefix = clean_subpath.rstrip("/") + "/"
        if file_path == clean_subpath:
            rel = Path(file_path).name
        elif file_path.startswith(prefix):
            rel = file_path[len(prefix) :]
        target = chart_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        content = git_read(commit, Path(file_path))
        if content is None:
            continue
        target.write_text(content)
    return chart_dir, checkout_root


def materialize_repo_for_source(
    source: dict,
    commit: str,
    cleanup_paths: List[Path],
) -> MaterializedRepo:
    repo_url = source.get("repoURL")
    target_revision = source.get("targetRevision")
    if is_local_repo(repo_url):
        return MaterializedRepo(
            kind="local",
            commit=commit,
            root=None,
            repo_url=repo_url,
            target_revision=target_revision,
        )
    repo_dir, checkout_root = clone_repo(repo_url or "", target_revision)
    cleanup_paths.append(checkout_root)
    return MaterializedRepo(
        kind="remote",
        commit=None,
        root=repo_dir,
        repo_url=repo_url,
        target_revision=target_revision,
        cleanup_path=checkout_root,
    )


def read_materialized_file(materialized: MaterializedRepo, rel_path: str) -> Optional[str]:
    relative = rel_path.lstrip("/")
    if not relative:
        return None
    if materialized.is_local():
        return git_read(materialized.commit or "HEAD", Path(relative))
    if not materialized.root:
        return None
    target = (materialized.root / relative).resolve()
    try:
        target.relative_to(materialized.root)
    except ValueError:
        raise HelmDiffError(f"Value file path '{rel_path}' escapes repository root")
    if not target.is_file():
        return None
    return target.read_text()


def ensure_alias_repo(
    alias: str,
    sources: List[dict],
    commit: str,
    cleanup_paths: List[Path],
    alias_cache: Dict[str, MaterializedRepo],
) -> MaterializedRepo:
    if alias in alias_cache:
        return alias_cache[alias]
    for source in sources:
        if isinstance(source, dict) and source.get("ref") == alias:
            repo = materialize_repo_for_source(source, commit, cleanup_paths)
            alias_cache[alias] = repo
            return repo
    raise HelmDiffError(f"Value file alias '{alias}' is not defined in sources")


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


def materialise_values(
    commit: str,
    references: List[str],
    *,
    alias_cache: Dict[str, MaterializedRepo],
    sources: List[dict],
    cleanup_paths: List[Path],
) -> tuple[List[Path], Optional[Path]]:
    if not references:
        return [], None
    base_path = Path(tempfile.mkdtemp(prefix="helm-values-"))
    files: List[Path] = []
    for idx, ref in enumerate(references):
        if not isinstance(ref, str):
            continue
        content: Optional[str]
        match = VALUE_REF_PATTERN.match(ref)
        if match:
            alias = match.group("ref")
            rel_path = match.group("path")
            materialized = ensure_alias_repo(alias, sources, commit, cleanup_paths, alias_cache)
            content = read_materialized_file(materialized, rel_path)
        else:
            rel_path = ref.replace(TARGET_PREFIX, "") if ref.startswith(TARGET_PREFIX) else ref
            content = git_read(commit, Path(rel_path))
        if content is None:
            continue
        target = base_path / f"values-{idx}.yaml"
        target.write_text(content)
        files.append(target)
    return files, base_path


def materialise_chart_from_repo(
    repo_url: str,
    chart_subpath: str,
    revision: Optional[str],
    *,
    local_commit: Optional[str] = None,
) -> tuple[Path, Path]:
    chart_subpath = (chart_subpath or "").strip()
    if not chart_subpath:
        raise HelmDiffError("path is required when using repoURL-based Helm sources")
    if is_local_repo(repo_url):
        if not local_commit:
            raise HelmDiffError("Local chart sources require a commit reference")
        return export_local_repo_subpath(local_commit, chart_subpath)

    repo_dir, checkout_root = clone_repo(repo_url, revision)
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


def _format_chart_label(chart: Optional[str], version: Optional[str]) -> str:
    if chart and version:
        return f"{chart}@{version}"
    if chart:
        return chart
    if version:
        return f"n/a@{version}"
    return "n/a"


def format_chart_suffix(base_state: RenderedState, head_state: RenderedState) -> str:
    base_label = _format_chart_label(base_state.chart, base_state.version)
    head_label = _format_chart_label(head_state.chart, head_state.version)
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


def classify_source_kind(source: dict) -> str:
    if not isinstance(source, dict):
        return "unknown"
    if source.get("helm") or source.get("chart"):
        return "helm"
    if source.get("path"):
        return "directory"
    if source.get("ref"):
        return "value-only"
    return "unknown"


def _relative_path_within_base(path: str, base: str) -> str:
    clean_base = base.strip().strip("/")
    candidate = path.strip().lstrip("/")
    if not clean_base:
        return candidate
    if path == clean_base:
        return Path(path).name
    prefix = clean_base + "/"
    if candidate.startswith(prefix):
        return candidate[len(prefix) :]
    if path.startswith(prefix):
        return path[len(prefix) :]
    return candidate


def _format_directory_doc(base_path: str, repo_relative: str, content: Optional[str]) -> Optional[str]:
    if not content:
        return None
    body = content.strip()
    if not body:
        return None
    clean_base = base_path.strip().strip("/")
    rel = _relative_path_within_base(repo_relative, clean_base)
    if clean_base and rel:
        source_label = f"{clean_base}/{rel}".strip("/")
    else:
        source_label = clean_base or rel or repo_relative
    source_label = source_label or "manifest"
    return f"# Source: {source_label}\n{body}"


def render_directory_source(
    commit: str,
    source: dict,
    *,
    alias_cache: Dict[str, MaterializedRepo],
    cleanup_paths: List[Path],
    label: str,
) -> tuple[Optional[str], Optional[str]]:
    directory_path = (source.get("path") or "").strip()
    if not directory_path:
        raise HelmDiffError("Directory source is missing a path")
    materialized = materialize_repo_for_source(source, commit, cleanup_paths)
    ref_name = source.get("ref")
    if ref_name and ref_name not in alias_cache:
        alias_cache[ref_name] = materialized

    docs: List[str] = []
    suffixes = {".yaml", ".yml", ".json"}
    if materialized.is_local():
        repo_commit = materialized.commit or commit
        file_paths = git_list_files(repo_commit, directory_path)
        if not file_paths:
            raise HelmDiffError(f"Directory path '{directory_path}' not found in local repository")
        for repo_rel in file_paths:
            if Path(repo_rel).suffix.lower() not in suffixes:
                continue
            content = git_read(repo_commit, Path(repo_rel))
            doc = _format_directory_doc(directory_path, repo_rel, content)
            if doc:
                docs.append(doc)
    else:
        if not materialized.root:
            raise HelmDiffError("Remote directory source did not provide a checkout root")
        base_dir = (materialized.root / directory_path.lstrip("/")).resolve()
        try:
            base_dir.relative_to(materialized.root)
        except ValueError:
            raise HelmDiffError(f"Directory path '{directory_path}' escapes repository root")
        if not base_dir.exists():
            raise HelmDiffError(f"Directory path '{directory_path}' not found in repository {source.get('repoURL')}")
        if base_dir.is_file():
            candidates = [base_dir]
        else:
            candidates = sorted(p for p in base_dir.rglob("*") if p.is_file())
        for file_path in candidates:
            if file_path.suffix.lower() not in suffixes:
                continue
            rel = str(file_path.relative_to(materialized.root))
            content = file_path.read_text()
            doc = _format_directory_doc(directory_path, rel, content)
            if doc:
                docs.append(doc)

    if not docs:
        return None, label
    manifest = "\n---\n".join(doc.strip() for doc in docs if doc.strip())
    return (manifest or None), label


def render_helm_source(
    commit: str,
    app: str,
    metadata: dict,
    namespace: str,
    source: dict,
    *,
    sources: List[dict],
    alias_cache: Dict[str, MaterializedRepo],
    cleanup_paths: List[Path],
    label: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    helm_cfg = source.get("helm") or {}
    release_name = helm_cfg.get("releaseName") or metadata.get("name", app)
    chart = source.get("chart")
    chart_path = source.get("path")
    repo = source.get("repoURL")
    version = source.get("targetRevision")
    skip_crds = helm_cfg.get("skipCrds", False)
    value_refs = helm_cfg.get("valueFiles") or []
    if isinstance(value_refs, str):
        value_refs = [value_refs]

    ref_name = source.get("ref")
    if ref_name and ref_name not in alias_cache:
        alias_cache[ref_name] = materialize_repo_for_source(source, commit, cleanup_paths)

    value_files, temp_dir = materialise_values(
        commit,
        list(value_refs),
        alias_cache=alias_cache,
        sources=sources,
        cleanup_paths=cleanup_paths,
    )
    chart_temp_dir: Optional[Path] = None
    descriptor = chart or chart_path or label
    try:
        chart_arg = chart
        repo_arg = repo
        allow_version_flag = True
        if not chart_arg:
            if chart_path:
                chart_dir, chart_temp_dir = materialise_chart_from_repo(
                    repo or "",
                    chart_path,
                    version,
                    local_commit=commit if is_local_repo(repo) else None,
                )
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
    except HelmDiffError:
        raise
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if chart_temp_dir:
            shutil.rmtree(chart_temp_dir, ignore_errors=True)
    return result.stdout, descriptor, version


def render_state(commit: str, app: str) -> RenderedState:
    app_path = APPS_ROOT / app / "application.yaml"
    raw_app = git_read(commit, app_path)
    if raw_app is None:
        return RenderedState(None, None, None, None)
    data = parse_application(raw_app)
    metadata = data.get("metadata", {})
    spec = data.get("spec", {})
    namespace = (spec.get("destination") or {}).get("namespace", "default")

    sources = spec.get("sources")
    if isinstance(sources, dict):
        sources = [sources]
    if not sources:
        legacy = spec.get("source")
        sources = [legacy] if isinstance(legacy, dict) else []
    if not sources:
        return RenderedState(None, None, None, "No sources defined in application.yaml")

    cleanup_paths: List[Path] = []
    alias_cache: Dict[str, MaterializedRepo] = {}
    manifests: List[str] = []
    chart_entries: List[tuple[Optional[str], Optional[str]]] = []

    try:
        for idx, source in enumerate(sources):
            if not isinstance(source, dict):
                continue
            kind = classify_source_kind(source)
            label = source.get("ref") or source.get("name") or source.get("chart") or source.get("path") or f"source-{idx + 1}"
            if kind == "value-only":
                ref_name = source.get("ref")
                if ref_name and ref_name not in alias_cache:
                    alias_cache[ref_name] = materialize_repo_for_source(source, commit, cleanup_paths)
                continue
            if kind == "helm":
                manifest, descriptor, version = render_helm_source(
                    commit,
                    app,
                    metadata,
                    namespace,
                    source,
                    sources=sources,
                    alias_cache=alias_cache,
                    cleanup_paths=cleanup_paths,
                    label=label,
                )
                if manifest:
                    manifests.append(manifest.strip())
                chart_entries.append((descriptor, version))
                continue
            if kind == "directory":
                manifest, descriptor = render_directory_source(
                    commit,
                    source,
                    alias_cache=alias_cache,
                    cleanup_paths=cleanup_paths,
                    label=label,
                )
                if manifest:
                    manifests.append(manifest.strip())
                chart_entries.append((descriptor, None))
                continue
    except HelmDiffError as exc:
        chart_label = []
        for name, version in chart_entries:
            if name and version:
                chart_label.append(f"{name}@{version}")
            elif name:
                chart_label.append(name)
            elif version:
                chart_label.append(f"version {version}")
        label_text = " + ".join(chart_label) if chart_label else "multi-source"
        return RenderedState(None, label_text or None, None, str(exc))
    finally:
        for path in cleanup_paths:
            shutil.rmtree(path, ignore_errors=True)

    combined_manifest = "\n---\n".join(doc for doc in manifests if doc)
    if not chart_entries:
        chart_label = None
        version_label = None
    elif len(chart_entries) == 1:
        chart_label = chart_entries[0][0]
        version_label = chart_entries[0][1]
    else:
        parts = []
        for name, version in chart_entries:
            if name and version:
                parts.append(f"{name}@{version}")
            elif name:
                parts.append(name)
            elif version:
                parts.append(f"version {version}")
        chart_label = " + ".join(parts) if parts else "multi-source"
        version_label = None
    return RenderedState(combined_manifest or None, chart_label, version_label, None)


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
