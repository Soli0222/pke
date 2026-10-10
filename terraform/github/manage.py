#!/usr/bin/env python3
"""Generate Terraform inputs, inspect inventory, and synchronize selected secrets."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from config import BOOLS, STRINGS, ConfigError, ROOT, address, generate, load, resolved, secrets_for, validate_transition


def tool_environment():
    # TRACE logs and injected Terraform flags could bypass locks or write the
    # legacy plaintext state to disk even when stdout itself is redacted.
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("TF_LOG", "TF_CLI_ARGS"))}


def run(command, *, stdin=None):
    result = subprocess.run(command, cwd=ROOT, input=stdin, text=True, env=tool_environment(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        # Diagnostics can contain secret values. Do not forward raw tool output.
        raise ConfigError(f"{command[0]} {command[1]} failed (exit {result.returncode}); output withheld")
    return result.stdout


def gh_api(path):
    return json.loads(run(["gh", "api", path]))


def remote_repositories(c):
    if gh_api("user")["login"].lower() != c["owner"].lower():
        raise ConfigError("GitHub CLI must authenticate as inventory owner to discover private repositories")
    pages = json.loads(run(["gh", "api", "--paginate", "--slurp", "user/repos?affiliation=owner&per_page=100"]))
    return {r["name"].lower(): r for page in pages for r in page
            if r["owner"]["login"].lower() == c["owner"].lower()}


def imports(c, remote):
    result = []
    for repo_id, repo in sorted(resolved(c).items()):
        actual = remote.get(repo["name"].lower())
        if actual is None:
            continue
        result.append({"to": address(repo_id), "id": actual["name"]})
        if repo["state"] == "active" and repo["default_branch"] is not None:
            result.append({"to": address(repo_id, branch=True), "id": actual["name"]})
    return {"import": result}


def inspect_plan(plan, c):
    """Reject implicit destruction/replacement and return only safe metadata.

    The first migration plan may still contain legacy plaintext secret values.
    Never print the raw JSON plan, including data source changes or diagnostics.
    """
    retired = {address(key): spec for key, spec in c["retired"].items()}
    managed = {address(key): repo for key, repo in resolved(c).items()}
    branches = {address(key, branch=True) for key in c["repositories"] | c["retired"]}
    summary = []
    for item in plan.get("resource_changes", []):
        addr, kind, change = item["address"], item["type"], item["change"]
        actions = change["actions"]
        before, after = change.get("before") or {}, change.get("after") or {}
        if kind == "github_repository":
            if "delete" in actions or "forget" in actions:
                spec = retired.get(addr)
                expected = "delete" if spec and spec["action"] == "delete" else "forget"
                if not spec or actions != [expected] or before.get("name", "").lower() != spec["name"].lower():
                    raise ConfigError(f"{addr}: implicit deletion/replacement; declare the stable ID and current name in retired")
                if expected == "delete" and before.get("archive_on_destroy"):
                    raise ConfigError(f"{addr}: disable archive_on_destroy before deletion")
            if before.get("archived") and before.get("name") != after.get("name") and after:
                raise ConfigError(f"{addr}: unarchive and apply before renaming")
            if "create" in actions:
                repo = managed.get(addr)
                if repo and not repo["auto_init"] and repo["default_branch"] is not None:
                    raise ConfigError(f"{addr}: new empty repository needs default_branch: null or auto_init: true")
        elif "delete" in actions or "forget" in actions:
            secret_handoff = (kind == "github_actions_secret" and
                              addr.startswith('github_actions_secret.repository[') and actions == ["forget"])
            branch_handoff = kind == "github_branch_default" and addr in branches and actions == ["forget"]
            if not (secret_handoff or branch_handoff):
                raise ConfigError(f"{addr}: unexpected resource removal")
        if actions != ["no-op"] or item.get("previous_address") or change.get("importing"):
            fields = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)) if kind in {"github_repository", "github_branch_default"} else []
            entry = {"address": addr, "actions": actions, "changed_fields": fields}
            if item.get("previous_address"):
                entry["previous_address"] = item["previous_address"]
            if change.get("importing"):
                entry["import"] = change["importing"]["id"]
            if kind in {"github_repository", "github_branch_default"}:
                safe_fields = BOOLS | STRINGS | {"name", "archived", "topics", "security_and_analysis", "branch", "rename"}
                entry["settings"] = {k: {"before": before.get(k), "after": after.get(k)}
                                     for k in sorted(safe_fields & set(fields))}
            summary.append(entry)
    return summary


@contextlib.contextmanager
def local_lock():
    (ROOT / ".terraform").mkdir(exist_ok=True)
    with (ROOT / ".terraform/management.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ConfigError("another management command is running in this checkout") from error
        yield


def imports_command():
    c = generate(check=True)
    path = ROOT / "imports.generated.tf.json"
    if path.exists():
        raise ConfigError("imports.generated.tf.json already exists; review or remove it before generating imports")
    content = imports(c, remote_repositories(c))
    with path.open("x") as output:
        output.write(json.dumps(content, indent=2) + "\n")
    print(f"Generated {len(content['import'])} import blocks in {path.name}.")
    print("Review with terraform plan, then terraform apply. Remove the import file after successful adoption.")


def check_plan_command(args):
    c = generate(check=True)
    path = Path(args.plan).resolve()
    summary = inspect_plan(json.loads(run(["terraform", "show", "-json", str(path)])), c)
    for item in summary:
        print(json.dumps(item, ensure_ascii=False))
    print(f"Checked {len(summary)} changes/moves/imports.")


def read_source(source):
    if "reference" in source:
        return run(["op", "read", source["reference"]])
    if "file" in source:
        return run(["op", "read", f'op://{source["vault"]}/{source["item"]}/{source["file"]}'])
    command = ["op", "item", "get", source["item"], "--fields", f'label={source["field"]}', "--format", "json"]
    if "vault" in source:
        command += ["--vault", source["vault"]]
    field = json.loads(run(command))
    if isinstance(field, list):
        field = next((f for f in field if f.get("label") == source["field"] or f.get("id") == source["field"]), {})
    if not isinstance(field, dict) or not isinstance(field.get("value"), str) or not field["value"]:
        raise ConfigError("1Password field is missing or empty")
    return field["value"]


def secret_command(args):
    c = load()
    selected = sorted(c["repositories"]) if args.all else sorted(set(args.repo))
    if set(selected) - c["repositories"].keys():
        raise ConfigError("unknown repository ID")
    work = []
    for repo_id in selected:
        repo = c["repositories"][repo_id]
        if repo["state"] == "archived":
            print(f"{repo_id}: archived; secrets retained without changes")
            continue
        name = repo.get("name", repo_id)
        actual = gh_api(f'repos/{c["owner"]}/{name}')
        if actual["archived"] or actual["name"].lower() != name.lower():
            raise ConfigError(f"{repo_id}: apply repository state/name changes before secret sync")
        pages = json.loads(run(["gh", "api", "--paginate", "--slurp", f'repos/{c["owner"]}/{name}/actions/secrets?per_page=100']))
        current = {s["name"] for page in pages for s in page["secrets"]}
        for key, spec in sorted(secrets_for(c, repo).items()):
            work.append((name, key, spec, key in current))
    if not args.apply:
        missing = False
        for name, key, spec, exists in work:
            status = ("delete-pending" if exists else "absent") if spec is None else ("present; value unchecked" if exists else "missing")
            print(f"{name}/{key}: {status}")
            missing |= exists if spec is None else not exists
        return 1 if missing else 0
    # Resolve all sources before the first write. Plaintext stays in memory/stdin.
    values = {}
    for _, _, spec, _ in work:
        if spec is not None:
            source_key = json.dumps(spec["onepassword"], sort_keys=True)
            if source_key not in values:
                values[source_key] = read_source(spec["onepassword"])
                if not values[source_key]:
                    raise ConfigError("1Password returned an empty secret")
    wrote = False
    for name, key, spec, exists in work:
        if spec is None and not exists:
            continue
        if wrote:
            time.sleep(1)  # GitHub mutation pacing, separate from Terraform.
        destination = f'{c["owner"]}/{name}'
        if spec is None:
            run(["gh", "secret", "delete", key, "--repo", destination, "--app", "actions"])
        else:
            run(["gh", "secret", "set", key, "--repo", destination, "--app", "actions"],
                stdin=values[json.dumps(spec["onepassword"], sort_keys=True)])
        wrote = True
        print(f'{name}/{key}: {"deleted" if spec is None else "updated"}', flush=True)
    return 0


def inventory_command():
    c = load()
    remote = remote_repositories(c)
    problems = []
    configured = {r["name"].lower(): r for r in resolved(c).values()}
    for name, repo in configured.items():
        if name not in remote:
            problems.append(f"missing: {repo['name']}")
        elif remote[name]["archived"] != (repo["state"] == "archived"):
            problems.append(f"archive drift: {repo['name']}")
    excluded = {name.lower() for name in c["excluded"]}
    retired = {r["name"].lower(): r for r in c["retired"].values()}
    for name, repo in remote.items():
        if name in retired:
            if retired[name]["action"] == "delete":
                problems.append(f"deletion pending: {repo['name']}")
        elif name not in configured and name not in excluded and not (c["exclude_forks"] and repo["fork"]):
            problems.append(f"unclassified: {repo['name']}")
    for problem in problems:
        print(problem)
    print(f"GitHub: {len(remote)}; configured: {len(configured)}; discrepancies: {len(problems)}")
    return bool(problems)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="validate YAML and render Terraform")
    gen.add_argument("--check", action="store_true")
    transition = sub.add_parser("check-transition", help="check removed IDs against a base inventory")
    transition.add_argument("--base", required=True)
    sub.add_parser("inventory", help="compare declared/excluded repositories with GitHub")
    sub.add_parser("imports", help="generate import blocks for existing declared repositories and branches")
    check = sub.add_parser("check-plan", help="optionally check a saved Terraform plan without applying it")
    check.add_argument("plan")
    secrets = sub.add_parser("secrets", help="check presence, or explicitly sync secret values")
    selection = secrets.add_mutually_exclusive_group(required=True)
    selection.add_argument("--repo", action="append", help="stable inventory ID; repeat for multiple repositories")
    selection.add_argument("--all", action="store_true")
    secrets.add_argument("--apply", action="store_true", help="write selected secrets; null entries delete explicitly")
    args = parser.parse_args()
    os.umask(0o077)
    with local_lock():
        if args.command == "generate":
            generate(args.check)
        elif args.command == "check-transition":
            validate_transition(args.base, load())
        elif args.command == "inventory":
            return inventory_command()
        elif args.command == "imports":
            imports_command()
        elif args.command == "check-plan":
            check_plan_command(args)
        elif args.command == "secrets":
            return secret_command(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ConfigError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
