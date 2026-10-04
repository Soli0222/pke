#!/usr/bin/env python3
"""本番への障害注入なしでOverviewの欠測・対象外・分離を検証する。"""

import importlib.util
import json
import subprocess
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "overview", ROOT / "scripts/sync-grafana-overview.py"
)
overview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overview)
hosts, queries = overview.expected_queries()


def q(panel, ref, cluster):
    return queries[panel][ref].replace("$cluster", cluster)


def check(panel, bad, unknown, cluster="natsume", at="5m"):
    return [
        {
            "expr": q(panel, i, cluster),
            "eval_time": at,
            "exp_samples": [{"labels": "{}", "value": value}],
        }
        for i, value in enumerate([bad, unknown])
    ]


def metric(name, labels, values="1+0x5"):
    return {
        "series": name
        + "{"
        + ",".join(f"{k}={json.dumps(v)}" for k, v in labels.items())
        + "}",
        "values": values,
    }


def ksm(c="natsume", values="1+0x5"):
    return metric("up", {"cluster": c, "job": "kube-state-metrics"}, values)


tests = []


def case(name, series, checks):
    tests.append(
        {
            "name": name,
            "interval": "1m",
            "input_series": series,
            "promql_expr_test": checks,
        }
    )


case(
    "absent nodes, DBs and probes survive in expectation",
    [],
    sum([check(1, 0, len(nodes), c) for c, nodes in hosts.items()], [])
    + check(7, 0, 10)
    + check(12, 0, 5)
    + check(8, 0, 4, "meruto")
    + check(7, -2, -2, "meruto")
    + check(12, -2, -2, "meruto")
    + check(8, -2, -2, "natsume"),
)
# A stale archive timestamp is only actionable with queued WAL on the same Pod.
wal_base = []
values = yaml.safe_load(
    (
        ROOT
        / "flux/clusters/natsume/apps/monitoring-rules/helmrelease-monitoring-rules.yaml"
    ).read_text()
)["spec"]["values"]
for db in values["databases"]:
    labels = {
        "cluster": "natsume",
        "namespace": db["namespace"],
        "cnpg_cluster": db["name"],
        "pod": db["name"] + "-1",
    }
    wal_base += [
        metric("cnpg_collector_up", labels),
        metric("cnpg_pg_stat_archiver_seconds_since_last_archival", labels, "3600x5"),
        metric(
            "cnpg_collector_pg_wal_archive_status", dict(labels, value="ready"), "0x5"
        ),
    ]
case("idle WAL archive is healthy", wal_base, check(7, 0, 0))
queued = json.loads(json.dumps(wal_base))
queued[2]["values"] = "2x5"
case("queued WAL and stale archive need attention", queued, check(7, 1, 0))
fresh_archive = json.loads(json.dumps(queued))
fresh_archive[1]["values"] = "60x5"
case("queued WAL with recent archive is healthy", fresh_archive, check(7, 0, 0))
missing_queue = wal_base[:2] + wal_base[3:]
case("missing WAL queue remains unknown", missing_queue, check(7, 0, 1))
other_pod = json.loads(json.dumps(queued))
other_pod[2]["series"] = other_pod[2]["series"].replace(
    f'pod="{values["databases"][0]["name"]}-1"', 'pod="other-pod"'
)
case("a different Pod cannot supply the WAL backlog", other_pod, check(7, 0, 1))
node = []
for c, nodes in hosts.items():
    for h in nodes:
        for condition in ["Ready", "MemoryPressure", "DiskPressure", "PIDPressure"]:
            node.append(
                metric(
                    "kube_node_status_condition",
                    {"cluster": c, "node": h, "condition": condition, "status": "true"},
                    "1+0x5" if condition == "Ready" else "0+0x5",
                )
            )
case("healthy nodes", node, check(1, 0, 0) + check(1, 0, 0, "meruto"))
changed = json.loads(json.dumps(node))
changed[0]["values"] = "0+0x5"
case(
    "one node not ready stays in its cluster",
    changed,
    check(1, 0, 0) + check(1, 1, 0, "meruto"),
)
stale = json.loads(json.dumps(node))
stale[0]["values"] = "1 _ _ _ _ _"
case("stale Ready is unknown", stale, check(1, 0, 0) + check(1, 0, 1, "meruto"))
base = {"cluster": "natsume", "namespace": "app", "deployment": "web"}
case(
    "desired exists but available disappears",
    [ksm(), metric("kube_deployment_spec_replicas", base)],
    check(2, 0, 1),
)
case(
    "available shortfall",
    [
        ksm(),
        metric("kube_deployment_spec_replicas", base, "2+0x5"),
        metric("kube_deployment_status_replicas_available", base),
    ],
    check(2, 1, 0),
)
case("collector failed is distinct from absent", [ksm(values="0+0x5")], check(2, 1, 0))
case("collector absent", [], check(2, 0, 1))
claim = {"cluster": "natsume", "namespace": "app", "persistentvolumeclaim": "data"}
case(
    "known PVC loses phase",
    [ksm(), metric("kube_persistentvolumeclaim_info", claim)],
    check(4, 0, 1),
)
pod = {"cluster": "natsume", "namespace": "app", "pod": "web", "uid": "u"}
case(
    "unready active pod",
    [
        ksm(),
        metric("kube_pod_status_phase", {**pod, "phase": "Running"}),
        metric("kube_pod_status_ready", {**pod, "condition": "true"}, "0+0x5"),
    ],
    check(3, 1, 0),
)
case(
    "completed unready pod is excluded",
    [
        ksm(),
        metric("kube_pod_status_phase", {**pod, "phase": "Succeeded"}),
        metric("kube_pod_status_ready", {**pod, "condition": "true"}, "0+0x5"),
    ],
    check(3, 0, 0),
)
case(
    "PV persists while Longhorn status disappears",
    [
        ksm(),
        metric(
            "kube_persistentvolume_info",
            {
                "cluster": "natsume",
                "persistentvolume": "v",
                "csi_driver": "driver.longhorn.io",
                "csi_volume_handle": "v",
            },
        ),
    ],
    check(6, 0, 1),
)
case(
    "misskey base backup zero is not a success",
    [
        metric(
            "barman_cloud_cloudnative_pg_io_last_available_backup_timestamp",
            {"cluster": "natsume", "namespace": ns, "cnpg_cluster": name},
            "1+60x5",
        )
        for ns, name in [
            ("grafana", "grafana-cluster"),
            ("sui", "sui-cluster"),
            ("spotify-reblend", "reblend-cluster"),
            ("spotify-nowplaying", "spn-cluster"),
        ]
    ]
    + [
        metric(
            "barman_cloud_cloudnative_pg_io_last_available_backup_timestamp",
            {
                "cluster": "natsume",
                "namespace": "misskey",
                "cnpg_cluster": "misskey-cluster",
            },
            "0+0x5",
        )
    ],
    check(12, 0, 1),
)
case(
    "expired certificate remains actionable",
    [
        metric("up", {"cluster": "natsume", "job": "cert-manager"}),
        metric(
            "certmanager_certificate_ready_status",
            {
                "cluster": "natsume",
                "job": "cert-manager",
                "exported_namespace": "app",
                "name": "tls",
                "condition": "True",
            },
        ),
        metric(
            "certmanager_certificate_expiration_timestamp_seconds",
            {
                "cluster": "natsume",
                "job": "cert-manager",
                "exported_namespace": "app",
                "name": "tls",
            },
            "1+0x5",
        ),
    ],
    check(11, 1, 0),
)
case(
    "31h backup is stale for daily DB but valid for weekly DBs",
    [
        metric(
            "barman_cloud_cloudnative_pg_io_last_available_backup_timestamp",
            {"cluster": "natsume", "namespace": ns, "cnpg_cluster": name},
            "1+0x1860",
        )
        for ns, name in [
            ("grafana", "grafana-cluster"),
            ("sui", "sui-cluster"),
            ("spotify-reblend", "reblend-cluster"),
            ("spotify-nowplaying", "spn-cluster"),
        ]
    ]
    + [
        metric(
            "barman_cloud_cloudnative_pg_io_last_available_backup_timestamp",
            {
                "cluster": "natsume",
                "namespace": "misskey",
                "cnpg_cluster": "misskey-cluster",
            },
            "1+0x1860",
        )
    ],
    check(12, 1, 0, at="31h"),
)
# Compile/evaluate every panel including the generated Pipeline query.
case("all panels parse under both cluster selections", [], [])
for c in hosts:
    for p in queries:
        for i in range(2):
            tests[-1]["promql_expr_test"].append(
                {
                    "expr": "count(" + q(p, i, c) + ")",
                    "eval_time": "5m",
                    "exp_samples": [{"labels": "{}", "value": 1}],
                }
            )
payload = yaml.safe_dump(
    {"rule_files": [], "evaluation_interval": "1m", "tests": tests}
)
subprocess.run(
    [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--entrypoint",
        "/bin/sh",
        "prom/prometheus:v3.13.0",
        "-c",
        "cat > /tmp/test.yaml && promtool test rules /tmp/test.yaml",
    ],
    input=payload,
    text=True,
    check=True,
)
print(f"Overview: {len(tests)} semantic cases passed")
