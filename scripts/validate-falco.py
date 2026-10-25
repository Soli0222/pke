#!/usr/bin/env python3
"""Falco 本体で設定とルールを検証する。イベントを生成せず、本番へ接続しない。"""

import re
import subprocess
import tempfile
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/install-falco"
IMAGE = "falcosecurity/falco:0.45.0"


def validate_upgrade_independence(rules):
    """全 custom rules にリリース・実体 ID の固定が戻ることを防ぐ。"""
    forbidden_fields = (
        "container.image.tag", "container.image.digest", "container.image.id",
        "container.image", "container.name_hash", "k8s.pod.name", "k8s.pod.id",
    )

    def check(value):
        if isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, str):
            for field in forbidden_fields:
                assert not re.search(r"(?<![\w.])" + re.escape(field) + r"(?![\w.])", value), value
            # repository 内の semver や実行パス内の PG major も対象。
            assert not re.search(r'''/[0-9]+(?:[.][0-9]+)*(?:-[A-Za-z]+)?(?=[/:\s"')\],]|$)''', value), value
            if "container.id" in value and value != "container.id":
                raise AssertionError("container.id は host を照合する例外表でのみ使用する")

    check(rules)
    for rule in rules:
        for exception in rule.get("exceptions", []):
            if "container.id" in exception["fields"]:
                index = exception["fields"].index("container.id")
                assert exception["comps"][index] == "="
                assert all(row[index] == "host" for row in exception["values"])
    print("All custom rules: no release pins / image tags / digests / Pod or container IDs OK")


def validate_cnpg_version_boundaries(condition):
    """数値部分だけを可変にし、配布元・パス構造の境界を維持する。"""
    patterns = dict(re.findall(r'([\w.]+) regex "([^"]+)"', condition))
    image_pattern = patterns["container.image.repository"]
    parent_pattern = patterns["proc.pexepath"]
    repository = "ghcr.io/soli0222/pgroonga-cnpg/"
    for version in ("4.0.6", "4.1.0", "5.0.0", "99.12.345"):
        assert re.fullmatch(image_pattern, repository + version + "-alpine")
    for image in (
        "evil.example/" + repository + "4.1.0-alpine",
        repository.replace("soli0222", "other") + "4.1.0-alpine",
        repository.replace("ghcr.io", "ghcrXio") + "4.1.0-alpine",
        repository + "4x1x0-alpine", repository + "4.1.0-alpine/other",
        repository + "4.1.0-alpine-evil", repository + "4.1.0-debian",
        repository + "4.1.0-rc1-alpine", repository + "../4.1.0-alpine",
    ):
        assert not re.fullmatch(image_pattern, image), image
    for major in ("16", "18", "19", "99"):
        assert re.fullmatch(parent_pattern, f"/usr/lib/postgresql/{major}/bin/postgres")
    for path in (
        "/tmp/usr/lib/postgresql/18/bin/postgres",
        "/usr/lib/postgresql/18/bin/postgres-evil",
        "/usr/lib/postgresql/18/../bin/postgres",
        "/usr/lib/postgresql/latest/bin/postgres",
        "/usr/lib/postgresql/18/bin/sh",
    ):
        assert not re.fullmatch(parent_pattern, path), path
    print("CNPG image / PostgreSQL major: upgrades allowed, foreign images / paths denied OK")


def exception_matches(exception, event):
    """今回の例外表で使う = / in を評価する。型・構文は Falco 本体でも検証する。"""
    def matches(field, comparison, value):
        if field not in event:
            return False
        if comparison == "=":
            return event[field] == value
        if comparison == "in":
            return event[field] in value
        raise ValueError(f"Unsupported exception comparison: {comparison}")

    return any(
        all(matches(field, comparison, value) for field, comparison, value in
            zip(exception["fields"], exception["comps"], values, strict=True))
        for values in exception["values"]
    )


def validate_exception_boundaries(rules):
    exceptions = {
        e["name"]: e for rule in rules for e in rule.get("exceptions", [])
    }
    dracut = exceptions["pke_dracut_root_shadow"]
    reader = {
        "container.id": "host", "user.uid": 0, "proc.name": "grep",
        "proc.exepath": "/usr/bin/grep", "proc.pname": "dracut",
        "proc.cmdline": "grep ^root: /etc/shadow", "fd.name": "/etc/shadow",
        "proc.aname[2]": "update-initramf", "proc.aname[3]": "dracut.postinst",
        "proc.aname[4]": "dpkg",
    }
    assert exception_matches(dracut, reader)
    kernel_hook = reader | {
        "proc.aname[2]": "dracut", "proc.aname[3]": "run-parts",
        "proc.aname[4]": "sh",
    }
    assert exception_matches(dracut, kernel_hook)
    for sample in (reader, kernel_hook):
        for field, value in (
            ("container.id", "some-container"), ("user.uid", 1000),
            ("proc.exepath", "/tmp/grep"), ("proc.pname", "bash"),
            ("proc.cmdline", "cat /etc/shadow"),
            ("proc.cmdline", "grep .* /etc/shadow"),
            ("proc.cmdline", "grep ^root: /etc/shadow; id"),
            ("fd.name", "/etc/gshadow"), ("proc.aname[3]", "bash"),
        ):
            assert not exception_matches(dracut, sample | {field: value}), (field, value)

    rpc = exceptions["pke_longhorn_instance_manager_rpc"]
    daemon = {
        "k8s.ns.name": "longhorn-system", "container.name": "instance-manager",
        "container.image.repository": "docker.io/longhornio/longhorn-instance-manager",
        "proc.name": "longhorn-instan", "proc.pname": "longhorn-instan",
        "proc.exepath": "/usr/local/bin/longhorn-instance-manager",
        "proc.cmdline": "longhorn-instan --debug daemon --listen :8500",
        "user.uid": 0, "proc.tty": 0, "evt.type": "dup3",
        "fd.l4proto": "tcp", "fd.is_server": True, "fd.lport": 8501,
    }
    for executable in ("/tini", "/usr/local/bin/longhorn-instance-manager"):
        for port in (8500, 8501, 8503):
            sample = daemon | {"proc.exepath": executable, "fd.lport": port}
            assert exception_matches(rpc, sample)
            for field, value in (
                ("fd.is_server", False), ("fd.lport", 4444), ("fd.l4proto", "udp"),
                ("evt.type", "connect"), ("proc.exepath", "/bin/sh"),
                ("proc.cmdline", daemon["proc.cmdline"] + "; sh"),
                ("proc.pname", "bash"), ("proc.tty", 34816), ("user.uid", 1000),
                ("container.name", "untrusted"), ("k8s.ns.name", "default"),
                ("container.image.repository", "example.com/untrusted"),
            ):
                assert not exception_matches(rpc, sample | {field: value}), (field, value)
    for exception, sample in ((dracut, reader), (rpc, daemon)):
        for field in exception["fields"]:
            assert not exception_matches(exception, {k: v for k, v in sample.items() if k != field}), field
    print("dracut / Longhorn exception tables: allowed cases / neighboring denials / missing fields OK")


def main():
    defaults = yaml.safe_load((ROLE / "defaults/main.yaml").read_text())
    env = Environment(undefined=StrictUndefined)
    env.filters["bool"] = bool
    config = env.from_string((ROLE / "templates/pke.yaml.j2").read_text()).render(
        defaults
    )
    settings = yaml.safe_load(config)
    assert settings["json_output"] and settings["metrics"]["include_empty_values"]
    assert (
        settings["stdout_output"]["enabled"]
        and not settings["syslog_output"]["enabled"]
    )
    rules = yaml.safe_load((ROLE / "files/pke-rules.yaml").read_text())
    validate_upgrade_independence(rules)
    validate_exception_boundaries(rules)
    cnpg = next(rule["condition"] for rule in rules if rule.get("macro") == "postgres_running_cnpg")
    validate_cnpg_version_boundaries(cnpg)
    # コマンド末尾・WAL パスの境界。Falco の regex は full match / POSIX ERE。
    # Python はこの共通構文の回帰試験にのみ使い、ルールのコンパイルは Falco 本体に任せる。
    pattern = re.search(r'proc.cmdline regex "([^"]+)"', cnpg)[1]
    prefix = "sh -c -- /controller/manager wal-archive --log-destination /controller/log/postgres.json pg_wal/"
    for wal in (
        "000000040000000000000086",
        "00000004.history",
        "000000040000000000000086.partial",
        "000000040000000000000086.00000028.backup",
    ):
        command = prefix + wal
        assert re.fullmatch(pattern, command)
        assert re.fullmatch(pattern, command.replace("-c --", "-c"))
        for suffix in ("; cat /etc/shadow", " && id", "\nwhoami", "$(id)", " garbage"):
            assert not re.fullmatch(pattern, command + suffix)
    for wal in ("../../etc/shadow", "00000004", "x" * 24, "0" * 25, "$(id)"):
        assert not re.fullmatch(pattern, prefix + wal)
    with tempfile.TemporaryDirectory(prefix="pke-falco-") as temp:
        out = Path(temp)
        out.chmod(0o755)
        (out / "pke.yaml").write_text(config)
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "/usr/bin/falco",
                "-v",
                f"{out / 'pke.yaml'}:/etc/falco/config.d/pke.yaml:ro",
                "-v",
                f"{ROLE / 'files/pke-rules.yaml'}:/etc/falco/rules.d/pke-rules.yaml:ro",
                IMAGE,
                "--dry-run",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        print(result.stdout)
        result.check_returncode()
        assert "schema validation: failed" not in result.stdout, result.stdout
        assert "/etc/falco/rules.d/pke-rules.yaml" in result.stdout, result.stdout
    print(
        "Falco config / plugin schema / upstream + PKE rules / WAL command boundaries: OK"
    )


if __name__ == "__main__":
    main()
