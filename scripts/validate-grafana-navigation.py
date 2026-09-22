#!/usr/bin/env python3
"""Dashboard/alertのUID・panel・変数・時刻とRuler URL展開を検証する。"""

import json
import re
import subprocess
from pathlib import Path
import tempfile
from urllib.parse import parse_qs, quote_plus, urlsplit
import yaml
from monitoring_rule_tools import production_values, render

ROOT = Path(__file__).resolve().parents[1]
catalog = json.loads((ROOT / "grafana/catalog.json").read_text())
dashboards = {
    uid: json.loads((ROOT / "grafana" / entry["path"]).read_text())["spec"]
    for uid, entry in catalog["dashboards"].items()
}


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def verify(url):
    # Replace template fragments only for structural checking. Actual Ruler
    # evaluation and URL escaping are tested independently below.
    url = re.sub(r"\{\{.*?\}\}", "sample", url)
    parsed = urlsplit(url)
    if parsed.netloc != "grafana.str08.net" or not parsed.path.startswith("/d/"):
        return
    uid = parsed.path.split("/")[2]
    assert uid in dashboards, (uid, "unknown dashboard")
    spec = dashboards[uid]
    variables = {v["name"] for v in spec["templating"]["list"]}
    params = parse_qs(parsed.query)
    for key in params:
        if key.startswith("var-"):
            assert key[4:] in variables, (uid, key)
    if "viewPanel" in params:
        assert int(params["viewPanel"][0]) in {
            p["id"] for p in walk(spec["panels"]) if "id" in p and "type" in p
        }, url
    assert "from" in params and "to" in params, url


for dashboard in dashboards.values():
    # This task owns new Overview navigation; legacy external links are retained.
    if dashboard["title"] == "PKE / Overview":
        for obj in walk(dashboard):
            if isinstance(obj.get("url"), str):
                verify(obj["url"])

for cluster in ["natsume", "meruto"]:
    for group in render(production_values(cluster)):
        for rule in group["rules"]:
            annotations = rule["annotations"]
            assert {"dashboard_url", "panel_url", "runbook_url"} <= annotations.keys()
            verify(annotations["dashboard_url"])
            verify(annotations["panel_url"])
            assert "{{ $labels.cluster | urlquery }}" in annotations["dashboard_url"]

blackbox_path = (
    ROOT
    / "flux/clusters/meruto/apps/blackbox-exporter-probes/helmrelease-blackbox-exporter-probes.yaml"
)
blackbox_values = yaml.safe_load(blackbox_path.read_text())["spec"]["values"]
with tempfile.TemporaryDirectory(prefix="pke-navigation-") as tmp:
    values_path = Path(tmp) / "values.yaml"
    values_path.write_text(yaml.safe_dump(blackbox_values))
    output = subprocess.check_output(
        [
            "helm",
            "template",
            "blackbox-exporter-probes",
            str(ROOT / "charts/blackbox-exporter-probes"),
            "-f",
            str(values_path),
        ],
        text=True,
    )
    for doc in yaml.safe_load_all(output):
        if doc and doc.get("kind") == "PrometheusRule":
            for group in doc["spec"]["groups"]:
                for rule in group["rules"]:
                    verify(rule["annotations"]["dashboard_url"])
                    verify(rule["annotations"]["panel_url"])

# Actual Prometheus text/template execution: namespace/host escaping, missing
# Pod fallback, and cluster selection must survive Helm and ruler rendering.
links = yaml.safe_load(
    (ROOT / "charts/monitoring-rules/dashboard-links.yaml").read_text()
)
labels = {
    "cluster": "meruto",
    "namespace": "team a",
    "instance": "host:123",
    "node": "node a",
    "hostname": "host a",
    "pod": "pod+a",
    "volume": "pvc/a",
    "cnpg_cluster": "db-a",
}
expected = {
    "collection": {"cluster": "meruto"},
    "host": {"cluster": "meruto", "hostname": "host a"},
    "kubernetes": {
        "Cluster": "meruto",
        "Node": "node a",
        "NameSpace": "team a",
        "Pod": "$__all",
        "Container": "$__all",
    },
    "workload": {
        "Cluster": "meruto",
        "Node": "$__all",
        "NameSpace": "team a",
        "Pod": "pod+a",
        "Container": "$__all",
    },
    "flux": {"cluster": "meruto", "namespace": "flux-system"},
    "longhorn": {"cluster": "meruto", "volume": "pvc/a"},
    "etcd": {"cluster": "meruto", "instance": "host:123"},
    "certificates": {"cluster": "meruto", "namespace": "team a"},
    "cnpg": {
        "cluster": "meruto",
        "namespace": "team a",
        "cnpg_cluster": "db-a",
        "pod": "pod+a",
    },
}
blackbox_url = blackbox_values["prometheusRule"]["endpointDown"]["annotations"][
    "dashboard_url"
]
links["groups"]["blackbox"] = {
    "uid": "NEzutrbMk",
    "panel": 2,
    "query": blackbox_url.split("?", 1)[1].split("&from=", 1)[0],
}
expected["blackbox"] = {"cluster": "meruto", "job": "$__all", "instance": "host:123"}
rules = []
checks = []
inputs = []
for name, link in links["groups"].items():
    for fallback in [False, True]:
        tag = name + ("_fallback" if fallback else "")
        used = labels.copy()
        params = expected[name].copy()
        if fallback:
            for key in ["hostname", "pod", "node", "volume"]:
                used.pop(key, None)
            if name == "host":
                params["hostname"] = "host:123"
            if name == "kubernetes":
                params["Node"] = "$__all"
            if name in ["workload", "cnpg"]:
                params["Pod" if name == "workload" else "pod"] = "$__all"
            if name == "longhorn":
                params["volume"] = "$__all"
        url = (
            "https://grafana.str08.net/d/"
            + link["uid"]
            + "?"
            + link["query"]
            + "&from=now-6h&to=now"
        )
        want = (
            "https://grafana.str08.net/d/"
            + link["uid"]
            + "?"
            + "&".join("var-" + k + "=" + quote_plus(v) for k, v in params.items())
            + "&from=now-6h&to=now"
        )
        rules.append(
            {
                "alert": tag,
                "expr": f'sum without (navigation_case) (navigation_input{{navigation_case="{tag}"}})',
                "annotations": {
                    "dashboard_url": url,
                    "panel_url": url + "&viewPanel=" + str(link["panel"]),
                },
            }
        )
        inputs.append(
            {
                "series": "navigation_input{"
                + ",".join(
                    k + "=" + json.dumps(v)
                    for k, v in {**used, "navigation_case": tag}.items()
                )
                + "}",
                "values": "1+0x2",
            }
        )
        checks.append(
            {
                "alertname": tag,
                "eval_time": "1m",
                "exp_alerts": [
                    {
                        "exp_labels": used,
                        "exp_annotations": {
                            "dashboard_url": want,
                            "panel_url": want + "&viewPanel=" + str(link["panel"]),
                        },
                    }
                ],
            }
        )
rule_doc = yaml.safe_dump({"groups": [{"name": "navigation", "rules": rules}]})
test_doc = yaml.safe_dump(
    {
        "rule_files": ["/tmp/rules.yaml"],
        "evaluation_interval": "1m",
        "tests": [
            {"interval": "1m", "input_series": inputs, "alert_rule_test": checks}
        ],
    }
)
# JSON carries exact newlines without shell interpolation of Go templates.
# The Prometheus image has no Python. Deliver two heredocs through stdin.
shell = (
    "cat > /tmp/rules.yaml <<'PKE_RULES'\n"
    + rule_doc
    + "PKE_RULES\ncat > /tmp/tests.yaml <<'PKE_TESTS'\n"
    + test_doc
    + "PKE_TESTS\npromtool test rules /tmp/tests.yaml\n"
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
    ],
    input=shell,
    text=True,
    check=True,
)
print(
    "Grafana navigation: Overview links, both alert catalogs and 20 Ruler URL cases passed"
)
