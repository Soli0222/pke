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
    # コマンド末尾・WAL パスの境界。Falco の regex は full match / POSIX ERE。
    # Python はこの共通構文の回帰試験にのみ使い、ルールのコンパイルは Falco 本体に任せる。
    pattern = re.search(r'proc.cmdline regex "([^"]+)"', rules[0]["condition"])[1]
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
