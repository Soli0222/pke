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
    + check(7, 0, 6)
    + check(12, 0, 5)
    + check(8, 0, 4, "meruto")
    + check(7, -2, -2, "meruto")
    + check(12, -2, -2, "meruto")
    + check(8, -2, -2, "natsume"),
)
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
            "kube_cronjob_status_last_successful_time",
            {"cluster": "natsume", "namespace": ns, "cronjob": name + "-pg-dump"},
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
            "cnpg_collector_last_available_backup_timestamp",
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
    "old base backup is not a current success",
    [
        metric(
            "kube_cronjob_status_last_successful_time",
            {"cluster": "natsume", "namespace": ns, "cronjob": name + "-pg-dump"},
            "1+60x1860",
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
            "cnpg_collector_last_available_backup_timestamp",
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
