#!/usr/bin/env python3
"""Ansible / CNPG の定義から監視の期待対象を生成する。"""

import argparse
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
START = "    # BEGIN generated monitoring inventory (scripts/sync-monitoring-inventory.py)\n"
END = "    # END generated monitoring inventory\n"


def backup_max_age(schedule):
    override = (
        schedule["metadata"]
        .get("annotations", {})
        .get("monitoring.pke.soli0222.com/backup-max-age-seconds")
    )
    if override is not None:
        age = int(override)
        if age <= 0:
            raise ValueError("backup-max-age-seconds must be positive")
        return age
    # CNPG の秒を含む cron。時刻固定の毎日 / 毎週以外は明示的な期限が必要。
    fields = schedule["spec"]["schedule"].split()
    if (
        len(fields) != 6
        or not all(x.isdigit() for x in fields[:3])
        or fields[3:5] != ["*", "*"]
    ):
        raise ValueError(
            f"{schedule['metadata']['name']}: set backup-max-age-seconds annotation for this schedule"
        )
    if fields[5] == "*":
        return 86400 + 21600
    if fields[5].isdigit() and 0 <= int(fields[5]) <= 6:
        return 7 * 86400 + 43200
    raise ValueError(
        f"{schedule['metadata']['name']}: set backup-max-age-seconds annotation for this schedule"
    )


def inventories(root=ROOT):
    groups = {}

    def register(name, value):
        groups[name] = value or groups.get(name, {})
        for child, data in (value or {}).get("children", {}).items():
            register(child, data)

    def members(name):
        group = groups[name]
        return set(group.get("hosts", {})).union(
            *(members(c) for c in group.get("children", {}))
        )

    register(
        "all",
        yaml.safe_load((root / "ansible/inventories/hosts.yaml").read_text())["all"],
    )
    result = {}
    for host in sorted(members("k3s_cluster")):
        values = yaml.safe_load(
            (root / f"ansible/inventories/host_vars/{host}.yaml").read_text()
        )
        devices = sorted(
            {v["device"] for v in values["network_netplan"].values() if v.get("device")}
        )
        result.setdefault(values["cluster"], {"hosts": [], "databases": []})[
            "hosts"
        ].append(
            {
                "name": host,
                "units": values["alloy_systemd_units"],
                "networkDevices": devices,
            }
        )
    for cluster, values in result.items():
        databases, schedules = {}, {}
        for path in sorted((root / f"flux/clusters/{cluster}/apps").rglob("*.yaml")):
            for doc in yaml.safe_load_all(path.read_text()):
                if not doc or doc.get("apiVersion") != "postgresql.cnpg.io/v1":
                    continue
                meta, spec = doc["metadata"], doc["spec"]
                if doc["kind"] == "Cluster":
                    databases[(meta["namespace"], meta["name"])] = spec
                if doc["kind"] == "ScheduledBackup" and not spec.get("suspend", False):
                    key = (meta["namespace"], spec["cluster"]["name"])
                    schedules.setdefault(key, []).append(backup_max_age(doc))
        for (namespace, name), spec in sorted(databases.items()):
            db = {
                "namespace": namespace,
                "name": name,
                "archive": any(
                    p.get("isWALArchiver", False) for p in spec.get("plugins", [])
                ),
                "replication": spec["instances"] > 1,
            }
            if (namespace, name) in schedules:
                db["baseBackupMaxAgeSeconds"] = min(schedules[(namespace, name)])
            values["databases"].append(db)
    return result


def synchronize(check=False, root=ROOT):
    for cluster, values in inventories(root).items():
        path = (
            root
            / f"flux/clusters/{cluster}/apps/monitoring-rules/helmrelease-monitoring-rules.yaml"
        )
        source = path.read_text()
        block = (
            START
            + "".join(
                "    " + line + "\n"
                for line in yaml.safe_dump(values, sort_keys=False).splitlines()
            )
            + END
        )
        if START in source:
            rendered = (
                source[: source.index(START)]
                + block
                + source[source.index(END) + len(END) :]
            )
        else:
            # Migration from the previous hand-maintained values, retaining all other overrides.
            rendered = re.sub(
                r"^    hosts:.*?(?=^    [a-zA-Z]|\Z)", "", source, flags=re.S | re.M
            )
            rendered = re.sub(
                r"^    databases:.*?(?=^    [a-zA-Z]|\Z)",
                "",
                rendered,
                flags=re.S | re.M,
            )
            rendered = rendered.replace("  values:\n", "  values:\n" + block)
        if check and rendered != source:
            raise SystemExit(
                f"{path}: run python3 scripts/sync-monitoring-inventory.py"
            )
        if not check:
            path.write_text(rendered)
    print("Monitoring inventory agrees with Ansible hosts, netplan and CNPG manifests.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    synchronize(parser.parse_args().check)
