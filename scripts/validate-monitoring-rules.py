#!/usr/bin/env python3
"""実 Alloy が scope した chart を promtool で検証する。本番 API / Secret は使わない。"""

import copy
import importlib.util
import json
import re
import tempfile
from pathlib import Path

import yaml

from monitoring_rule_tools import (
    ROOT,
    PROMETHEUS,
    production_values,
    render,
    run,
    scope,
)


def inventory_contract():
    hosts = {}

    def inventory_hosts(group):
        result = set(group.get("hosts", {}))
        for child in group.get("children", {}).values():
            result.update(inventory_hosts(child or {}))
        return result

    active = inventory_hosts(
        yaml.safe_load((ROOT / "ansible/inventories/hosts.yaml").read_text())["all"][
            "children"
        ]["k3s_cluster"]
    )
    for path in (ROOT / "ansible/inventories/host_vars").glob("*.yaml"):
        if path.stem not in active:
            continue
        data = yaml.safe_load(path.read_text())
        if data.get("cluster"):
            hosts.setdefault(data["cluster"], []).append(
                {"name": path.stem, "units": data["alloy_systemd_units"]}
            )
    for cluster in ("natsume", "meruto"):
        values = production_values(cluster)
        assert sorted(
            [{k: h[k] for k in ("name", "units")} for h in values["hosts"]],
            key=lambda x: x["name"],
        ) == sorted(hosts[cluster], key=lambda x: x["name"])
        databases = {}
        stores = {}
        schedules = {}
        monitors = {}
        for path in (ROOT / f"flux/clusters/{cluster}/apps").rglob("*.yaml"):
            for doc in yaml.safe_load_all(path.read_text()):
                if not doc:
                    continue
                if (
                    doc.get("kind") == "Cluster"
                    and doc.get("apiVersion") == "postgresql.cnpg.io/v1"
                ):
                    databases[
                        (doc["metadata"]["namespace"], doc["metadata"]["name"])
                    ] = doc["spec"]
                key = (
                    doc.get("metadata", {}).get("namespace"),
                    doc.get("metadata", {}).get("name"),
                )
                if doc.get("kind") == "ObjectStore":
                    stores[key] = doc["spec"]
                if doc.get("kind") == "ScheduledBackup":
                    schedules[(key[0], doc["spec"]["cluster"]["name"])] = doc["spec"]
                if doc.get("kind") == "PodMonitor":
                    monitors[key] = doc["spec"]
        assert {(d["namespace"], d["name"]) for d in values["databases"]} == set(
            databases
        )
        for db in values["databases"]:
            spec = databases[(db["namespace"], db["name"])]
            assert db["replication"] == (spec["instances"] > 1)
            assert db["archive"] == any(
                p.get("isWALArchiver") for p in spec.get("plugins", [])
            )
            if db.get("baseBackupMaxAgeSeconds"):
                key = (db["namespace"], db["name"])
                schedule = schedules[key]
                assert schedule["method"] == "plugin"
                plugin = next(p for p in spec["plugins"] if p.get("isWALArchiver"))
                assert (
                    plugin["name"]
                    == schedule["pluginConfiguration"]["name"]
                    == "barman-cloud.cloudnative-pg.io"
                )
                store = stores[
                    (db["namespace"], plugin["parameters"]["barmanObjectName"])
                ]
                assert store["retentionPolicy"] == "7d"
                assert (
                    store["configuration"]["endpointURL"]
                    == "${CNPG_BACKUP_ENDPOINT_URL}"
                )
                assert db["baseBackupMaxAgeSeconds"] > 0
                assert any(
                    r.get("targetLabel") == "cnpg_cluster"
                    for endpoint in monitors[key]["podMetricsEndpoints"]
                    for r in endpoint.get("relabelings", [])
                )
                flux = yaml.safe_load(
                    (
                        ROOT
                        / f"flux/clusters/{cluster}/kustomizations/{db['namespace']}.yaml"
                    ).read_text()
                )["spec"]
                assert {"cnpg", "cnpg-backup-config"} <= {
                    d["name"] for d in flux["dependsOn"]
                }
                assert {"kind": "Secret", "name": "cnpg-backup-flux-vars"} in flux[
                    "postBuild"
                ]["substituteFrom"]
        if not databases:
            assert values["groups"]["cnpg"]["enabled"] is False


def main():
    run("python3", "scripts/sync-monitoring-inventory.py", "--check")
    inventory_contract()
    defaults = yaml.safe_load(
        (ROOT / "charts/monitoring-rules/values.yaml").read_text()
    )
    test_values = {
        "hosts": [
            {
                "name": "node-a",
                "units": ["alloy.service", "etcd.service"],
                "networkDevices": ["ens3", "eno1", "bond0"],
            }
        ],
        "databases": [
            {
                "name": "db",
                "namespace": "app",
                "archive": True,
                "replication": True,
                "baseBackupMaxAgeSeconds": 648000,
            },
            {
                "name": "disabled-db",
                "namespace": "app",
                "archive": False,
                "replication": False,
            },
        ],
    }
    # Mimir rejects groups above 20 rules. Growing inventory must create more groups.
    expanded = copy.deepcopy(test_values)
    expanded["hosts"] = [
        {
            "name": f"node-{i}",
            "units": ["alloy.service", "k3s.service"],
            "networkDevices": ["bond0"],
        }
        for i in range(30)
    ]
    expanded_groups = render(expanded)
    assert all(len(g["rules"]) <= 20 for g in expanded_groups)
    assert len({g["name"] for g in expanded_groups}) == len(expanded_groups)
    assert (
        sum(
            r.get("record") == "pke_host_expected"
            for g in expanded_groups
            for r in g["rules"]
        )
        == 30
    )
    for c in ("natsume", "meruto"):
        assert all(len(g["rules"]) <= 20 for g in render(production_values(c)))
    groups = render(test_values)
    rules = {r["alert"]: r for g in groups for r in g["rules"] if "alert" in r}
    for name, rule in rules.items():
        assert not re.search(r"(?<![a-z_])cluster\s*=", rule["expr"]), name
        assert {"summary", "description", "runbook_url"} <= set(rule["annotations"]), (
            name
        )
    disabled = copy.deepcopy(test_values)
    disabled["groups"] = {name: {"enabled": False} for name in defaults["groups"]}
    assert render(disabled) == []
    override = copy.deepcopy(test_values)
    override.update(
        additionalAnnotations={"team": "platform"},
        groups={
            "host": {
                "rules": {
                    "HostMemoryLow": {
                        "threshold": 0.123,
                        "for": "7m",
                        "severity": "critical",
                        "annotations": {"summary": "override"},
                    },
                    "HostCPUHigh": {"enabled": False},
                }
            }
        },
    )
    overridden = {
        r["alert"]: r for g in render(override) for r in g["rules"] if "alert" in r
    }
    assert "HostCPUHigh" not in overridden
    memory = overridden["HostMemoryLow"]
    assert (
        "< 0.123" in memory["expr"]
        and memory["for"] == "7m"
        and memory["labels"]["severity"] == "critical"
    )
    assert (
        memory["annotations"]["summary"] == "override"
        and memory["annotations"]["team"] == "platform"
    )
    # Expected host removal is reflected in absence and unit rules; unrelated units are never included.
    assert "node-a" not in str(
        render({"hosts": [], "groups": {"cnpg": {"enabled": False}}})
    )

    added = copy.deepcopy(test_values)
    added["hosts"].append({"name": "node-b", "units": ["k3s-agent.service"]})
    added_rules = {
        r["alert"]: r for g in render(added) for r in g["rules"] if "alert" in r
    }
    assert (
        added_rules["NodeExporterAbsent"]["expr"] == rules["NodeExporterAbsent"]["expr"]
    )
    assert any(
        r.get("labels", {}).get("instance") == "node-b"
        for g in render(added)
        for r in g["rules"]
        if "record" in r
    )
    assert (
        added_rules["HostSystemdUnitInactive"]["expr"]
        == rules["HostSystemdUnitInactive"]["expr"]
    )

    module_path = ROOT / "charts/monitoring-rules/tests/scenarios.py"
    spec = importlib.util.spec_from_file_location("scenarios", module_path)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    cases = fixtures.scenarios()
    covered = {c["alert"] for c in cases}
    assert covered == set(rules), (
        "missing fixture",
        set(rules) - covered,
        covered - set(rules),
    )
    with tempfile.TemporaryDirectory(prefix="pke-monitoring-tests-") as temp:
        out = Path(temp)
        out.chmod(0o755)
        production = scope(
            {c: render(production_values(c)) for c in ("natsume", "meruto")},
            out / "production",
        )
        scoped = scope({c: groups for c in ("natsume", "meruto")}, out / "test-scope")
        mount = [
            "--network",
            "none",
            "-v",
            f"{out}:/work:ro",
            "--entrypoint",
            "/bin/promtool",
            PROMETHEUS,
        ]
        for cluster, gs in production.items():
            values_path = out / f"values-{cluster}.yaml"
            values_path.write_text(yaml.safe_dump(production_values(cluster)))
            print(
                run("helm", "lint", "charts/monitoring-rules", "-f", str(values_path))
            )
            path = out / f"production-{cluster}.yaml"
            path.write_text(yaml.safe_dump({"groups": gs}, allow_unicode=True))
            print(
                run(
                    "docker",
                    "run",
                    "--rm",
                    *mount,
                    "check",
                    "rules",
                    f"/work/{path.name}",
                )
            )
        paths = []
        for alert in sorted(rules):
            rule_path = out / f"{alert}-rules.yaml"
            rule_path.write_text(
                yaml.safe_dump(
                    {
                        "groups": [
                            dict(
                                g,
                                name=f"{cluster}.{g['name']}",
                                rules=[
                                    r
                                    for r in g["rules"]
                                    if r.get("alert") == alert or "record" in r
                                ],
                            )
                            for cluster, gs in scoped.items()
                            for g in gs
                            if any(
                                r.get("alert") == alert or "record" in r
                                for r in g["rules"]
                            )
                        ]
                    },
                    allow_unicode=True,
                )
            )
            tests = []
            for case in (c for c in cases if c["alert"] == alert):
                checks = []
                for when, expected in case["checks"]:
                    samples = []
                    for labels in expected:
                        full = {
                            "__name__": "ALERTS",
                            "alertname": alert,
                            "alertstate": "firing",
                            **rules[alert]["labels"],
                            **labels,
                        }
                        samples.append(
                            {
                                "labels": "{"
                                + ",".join(
                                    f"{k}={json.dumps(v)}"
                                    for k, v in sorted(full.items())
                                )
                                + "}",
                                "value": 1,
                            }
                        )
                    checks.append(
                        {
                            "expr": f'ALERTS{{alertname="{alert}",alertstate="firing"}}',
                            "eval_time": when,
                            "exp_samples": samples,
                        }
                    )
                tests.append(
                    {
                        "name": case["name"],
                        "interval": case.get("interval", "1m"),
                        "input_series": case["series"],
                        "promql_expr_test": checks,
                    }
                )
            path = out / f"{alert}-test.yaml"
            paths.append("/work/" + path.name)
            path.write_text(
                yaml.safe_dump(
                    {
                        "rule_files": ["/work/" + rule_path.name],
                        "evaluation_interval": "1m",
                        "group_eval_order": [f"{c}.pke.expectations" for c in scoped],
                        "tests": tests,
                    },
                    allow_unicode=True,
                )
            )
        print(run("docker", "run", "--rm", *mount, "test", "rules", *paths))
        print(
            f"{len(rules)} alert rules / {len(cases)} semantic scenarios: OK; inventory / chart overrides / real Alloy scope / both production catalogs: OK"
        )


if __name__ == "__main__":
    main()
