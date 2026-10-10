"""GitHub inventory validation and deterministic Terraform configuration.

Generate individual resource blocks because Terraform lifecycle rules cannot
depend on for_each values. Stable inventory IDs survive archive and rename.
"""

import copy
import hashlib
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "repositories.yaml"
GENERATED = ROOT / "repositories.generated.tf.json"
BOOLS = {
    "allow_auto_merge", "allow_forking", "allow_merge_commit", "allow_rebase_merge",
    "allow_squash_merge", "allow_update_branch", "auto_init", "delete_branch_on_merge",
    "has_discussions", "has_issues", "has_projects", "has_wiki", "is_template",
    "web_commit_signoff_required",
}
STRINGS = {"description", "homepage_url", "merge_commit_message", "merge_commit_title",
           "squash_merge_commit_message", "squash_merge_commit_title", "visibility"}
SETTINGS = BOOLS | STRINGS | {"topics", "security_and_analysis", "default_branch"}
SECURITY = {"advanced_security", "code_security", "secret_scanning",
            "secret_scanning_ai_detection", "secret_scanning_non_provider_patterns",
            "secret_scanning_push_protection"}
ENUMS = {
    "visibility": {"public", "private"},
    "merge_commit_message": {"PR_BODY", "PR_TITLE", "BLANK"},
    "merge_commit_title": {"PR_TITLE", "MERGE_MESSAGE"},
    "squash_merge_commit_message": {"PR_BODY", "COMMIT_MESSAGES", "BLANK"},
    "squash_merge_commit_title": {"PR_TITLE", "COMMIT_OR_PR_TITLE"},
}
INITIAL_ONLY = ["auto_init", "gitignore_template", "license_template", "has_downloads",
                "vulnerability_alerts", "ignore_vulnerability_alerts_during_read"]


class ConfigError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ConfigError(f"line {key_node.start_mark.line + 1}: duplicate or non-string key")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def mapping(value, where, allowed=None, required=()):
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: mapping required")
    if allowed is not None and set(value) - allowed:
        raise ConfigError(f"{where}: unknown keys: {', '.join(sorted(set(value) - allowed))}")
    if set(required) - set(value):
        raise ConfigError(f"{where}: missing keys: {', '.join(sorted(set(required) - set(value)))}")


def text(value, where, pattern=None, empty=False):
    if not isinstance(value, str) or (not empty and not value) or (pattern and not re.fullmatch(pattern, value)):
        raise ConfigError(f"{where}: invalid string")


def secret_specs(specs, where, allow_delete=False):
    mapping(specs, where)
    for name, spec in specs.items():
        text(name, where, r"[A-Z_][A-Z0-9_]*")
        if name.startswith("GITHUB_"):
            raise ConfigError(f"{where}: GITHUB_ is reserved")
        if allow_delete and spec is None:
            continue
        mapping(spec, where, {"onepassword"}, {"onepassword"})
        source = spec["onepassword"]
        mapping(source, where)
        if set(source) not in ({"reference"}, {"item", "field"}, {"vault", "item", "field"}, {"vault", "item", "file"}):
            raise ConfigError(f"{where}: use reference, item+field, or vault+item+file")
        for value in source.values():
            text(value, where)
        if "reference" in source and not source["reference"].startswith("op://"):
            raise ConfigError(f"{where}: op:// reference required")


def settings(value, where):
    for key in BOOLS & value.keys():
        if type(value[key]) is not bool:
            raise ConfigError(f"{where}.{key}: boolean required")
    for key in STRINGS & value.keys():
        text(value[key], f"{where}.{key}", empty=key in {"description", "homepage_url"})
    for key, choices in ENUMS.items():
        if key in value and value[key] not in choices:
            raise ConfigError(f"{where}.{key}: unsupported value")
    if "default_branch" in value and value["default_branch"] is not None:
        text(value["default_branch"], f"{where}.default_branch")
    if "topics" in value:
        topics = value["topics"]
        if not isinstance(topics, list) or any(not isinstance(t, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", t) for t in topics):
            raise ConfigError(f"{where}.topics: invalid topics")
        if len(topics) > 20 or len(set(topics)) != len(topics):
            raise ConfigError(f"{where}.topics: duplicate or too many topics")
    if "security_and_analysis" in value:
        mapping(value["security_and_analysis"], where, SECURITY)
        if any(v not in ("enabled", "disabled") for v in value["security_and_analysis"].values()):
            raise ConfigError(f"{where}: security status must be enabled or disabled")


def load(path=MANIFEST):
    try:
        c = yaml.load(Path(path).read_text(), Loader=UniqueLoader)
    except yaml.YAMLError as error:
        # Do not echo YAML source lines, which may accidentally contain secrets.
        raise ConfigError("invalid YAML") from error
    validate(c)
    return c


def validate(c):
    root_keys = {"owner", "defaults", "secret_sets", "repositories", "retired", "excluded", "exclude_forks"}
    mapping(c, "inventory", root_keys, root_keys)
    text(c["owner"], "owner", r"[A-Za-z0-9][A-Za-z0-9-]*")
    mapping(c["defaults"], "defaults", SETTINGS, SETTINGS - {"security_and_analysis"})
    settings(c["defaults"], "defaults")
    mapping(c["secret_sets"], "secret_sets")
    for name, specs in c["secret_sets"].items():
        text(name, "secret set", r"[A-Za-z0-9_-]+")
        secret_specs(specs, f"secret_sets.{name}")
    for section in ("repositories", "retired", "excluded"):
        mapping(c[section], section)
    if type(c["exclude_forks"]) is not bool:
        raise ConfigError("exclude_forks: boolean required")
    if set(c["repositories"]) & set(c["retired"]):
        raise ConfigError("repository IDs cannot also be retired")
    names = set()
    for repo_id, repo in c["repositories"].items():
        text(repo_id, "repository ID", r"[A-Za-z0-9_-]+")
        mapping(repo, repo_id, SETTINGS | {"state", "name", "secret_sets", "actions_secrets", "rename_default_branch"}, {"state"})
        if repo["state"] not in ("active", "archived"):
            raise ConfigError(f"{repo_id}: state must be active or archived")
        name = repo.get("name", repo_id)
        text(name, f"{repo_id}.name", r"[A-Za-z0-9_.-]+")
        if name.lower() in names:
            raise ConfigError("repository names must be unique, ignoring case")
        names.add(name.lower())
        settings(repo, repo_id)
        selected = repo.get("secret_sets", [])
        if not isinstance(selected, list) or any(not isinstance(s, str) or s not in c["secret_sets"] for s in selected):
            raise ConfigError(f"{repo_id}: unknown secret set")
        if len(set(selected)) != len(selected):
            raise ConfigError(f"{repo_id}: duplicate secret set")
        if type(repo.get("rename_default_branch", False)) is not bool:
            raise ConfigError(f"{repo_id}: rename_default_branch must be boolean")
        secret_specs(repo.get("actions_secrets", {}), repo_id, allow_delete=True)
        combined = {}
        for secret_set in selected:
            for key, spec in c["secret_sets"][secret_set].items():
                if key in combined and combined[key] != spec:
                    raise ConfigError(f"{repo_id}: conflicting secret sets for {key}")
                combined[key] = spec
    for repo_id, spec in c["retired"].items():
        text(repo_id, "retired ID", r"[A-Za-z0-9_-]+")
        mapping(spec, repo_id, {"name", "action"}, {"name", "action"})
        text(spec["name"], f"{repo_id}.name", r"[A-Za-z0-9_.-]+")
        if spec["action"] not in ("delete", "forget"):
            raise ConfigError(f"{repo_id}: action must be delete or forget")
        if spec["name"].lower() in names and spec["action"] == "delete":
            raise ConfigError(f"{repo_id}: name awaiting deletion cannot also be managed")
        names.add(spec["name"].lower())
    for name, reason in c["excluded"].items():
        text(name, "excluded name", r"[A-Za-z0-9_.-]+")
        text(reason, "exclusion reason")
        if name.lower() in names:
            raise ConfigError("excluded name is also managed or retired")


def resolved(c):
    result = {}
    for repo_id, repo in c["repositories"].items():
        value = copy.deepcopy(c["defaults"])
        value.update(repo)
        value["name"] = repo.get("name", repo_id)
        value["security_and_analysis"] = {
            **c["defaults"].get("security_and_analysis", {}), **repo.get("security_and_analysis", {})}
        if value["visibility"] == "private":
            value["security_and_analysis"] = {}
        result[repo_id] = value
    return result


def secrets_for(c, repo):
    result = {}
    for name in repo.get("secret_sets", []):
        result.update(c["secret_sets"][name])
    result.update(repo.get("actions_secrets", {}))
    return result


def address(repo_id, branch=False):
    return f'github_{"branch_default" if branch else "repository"}.repo_{repo_id}'


def literal(value):
    """Terraform JSON strings are templates; inventory strings must stay literal."""
    if isinstance(value, str):
        return value.replace("${", "$${").replace("%{", "%%{")
    if isinstance(value, dict):
        return {k: literal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [literal(v) for v in value]
    return value


def render(c, manifest_bytes):
    repositories, branches, removed = {}, {}, []
    for repo_id, repo in sorted(resolved(c).items()):
        archived = repo["state"] == "archived"
        attrs = {k: literal(repo[k]) for k in BOOLS | STRINGS | {"topics"}}
        attrs.update(name=repo["name"], archived=archived, archive_on_destroy=False)
        if repo["security_and_analysis"]:
            attrs["security_and_analysis"] = [{k: [{"status": v}] for k, v in repo["security_and_analysis"].items()}]
        ignored = set(INITIAL_ONLY)
        if archived:
            ignored |= (BOOLS | STRINGS | {"topics", "security_and_analysis"})
        attrs["lifecycle"] = {"prevent_destroy": True, "ignore_changes": sorted(ignored)}
        repositories[f"repo_{repo_id}"] = attrs
        if not archived and repo["default_branch"] is not None:
            branches[f"repo_{repo_id}"] = {
                "repository": "${" + address(repo_id) + ".name}",
                "branch": literal(repo["default_branch"]),
                "rename": repo.get("rename_default_branch", False),
                "wait_for_rename": repo.get("rename_default_branch", False),
            }
        else:
            removed.append({"from": address(repo_id, branch=True), "lifecycle": {"destroy": False}})
    for repo_id, spec in sorted(c["retired"].items()):
        removed.extend([
            {"from": address(repo_id), "lifecycle": {"destroy": spec["action"] == "delete"}},
            {"from": address(repo_id, branch=True), "lifecycle": {"destroy": False}},
        ])
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    config = {
        "locals": {"github_owner": c["owner"]},
        "removed": removed,
        "output": {
            "inventory_sha256": {
                "value": digest,
                "precondition": [{
                    "condition": '${filesha256("${path.module}/repositories.yaml") == "' + digest + '"}',
                    "error_message": "Inventory changed: run python3 manage.py generate before planning.",
                }],
            },
            "managed_repositories": {"value": sorted(r["name"] for r in resolved(c).values())},
        },
    }
    resources = {kind: entries for kind, entries in
                 (("github_repository", repositories), ("github_branch_default", branches)) if entries}
    if resources:
        config["resource"] = resources
    return json.dumps(config, indent=2, sort_keys=True) + "\n"


def generate(check=False):
    c = load()
    content = render(c, MANIFEST.read_bytes())
    if check:
        if not GENERATED.exists() or GENERATED.read_text() != content:
            raise ConfigError("generated Terraform is stale: run python3 manage.py generate")
    else:
        GENERATED.write_text(content)
    return c


def validate_transition(base_path, current):
    """Require a declared disposition for every removed ID, including in CI."""
    try:
        base = yaml.load(Path(base_path).read_text(), Loader=UniqueLoader)
    except yaml.YAMLError as error:
        raise ConfigError("invalid base inventory YAML") from error
    mapping(base, "base inventory")
    missing = set(base.get("repositories", {})) - (current["repositories"].keys() | current["retired"].keys())
    if missing:
        raise ConfigError(f"removed IDs need retired entries: {', '.join(sorted(missing))}")
    for key, spec in base.get("retired", {}).items():
        if current["retired"].get(key) != spec:
            raise ConfigError(f"{key}: retired IDs and their disposition must be retained")
    for key, spec in current["retired"].items():
        previous = base.get("repositories", {}).get(key)
        if previous is not None and previous.get("name", key).lower() != spec["name"].lower():
            raise ConfigError(f"{key}: retired name must match the previous managed name")
