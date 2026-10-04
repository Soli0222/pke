#!/usr/bin/env python3
"""Probe の定義と欠測監視が一致し、消失・復帰・別クラスタを区別することを確認する。"""

import copy
import json
import tempfile
from pathlib import Path

import yaml

from monitoring_rule_tools import PROMETHEUS, ROOT, run, scope


def render(values, out):
    path = out / "blackbox-values.yaml"
    path.write_text(yaml.safe_dump(values))
    return [
        d
        for d in yaml.safe_load_all(
            run(
                "helm",
                "template",
                "blackbox-exporter-probes",
                "charts/blackbox-exporter-probes",
                "-f",
                str(path),
            )
        )
        if d
    ]


def contract(docs):
    targets = {
        (d["spec"]["jobName"], t)
        for d in docs
        if d["kind"] == "Probe"
        for t in d["spec"]["targets"]["staticConfig"]["static"]
    }
    groups = [
        g for d in docs if d["kind"] == "PrometheusRule" for g in d["spec"]["groups"]
    ]
    records = [
        r for g in groups for r in g["rules"] if r.get("record") == "pke_probe_expected"
    ]
    assert {(r["labels"]["job"], r["labels"]["instance"]) for r in records} == targets
    assert len(records) == len(targets), "duplicate probe identity"
    return groups


def main():
    with tempfile.TemporaryDirectory(prefix="pke-probe-tests-") as temp:
        out = Path(temp)
        values = yaml.safe_load(
            (
                ROOT
                / "flux/clusters/meruto/apps/blackbox-exporter-probes/helmrelease-blackbox-exporter-probes.yaml"
            ).read_text()
        )["spec"]["values"]
        groups = contract(render(values, out))
        print(
            run(
                "helm",
                "lint",
                "charts/blackbox-exporter-probes",
                "-f",
                str(out / "blackbox-values.yaml"),
            )
        )
        # A new application inherits the global job; another has an explicit custom job.
        values = {
            "applications": [
                {"name": "new", "targets": ["https://new.example"]},
                {
                    "name": "custom",
                    "jobName": "custom",
                    "targets": ["https://custom.example"],
                },
            ]
        }
        testgroups = contract(render(values, out))
        removed = copy.deepcopy(values)
        removed["applications"].pop()
        remaining = contract(render(removed, out))
        assert "https://custom.example" not in str(remaining)
        scoped = scope({"natsume": testgroups, "meruto": groups}, out / "scope")
        mount = [
            "--network",
            "none",
            "-v",
            f"{out}:/work:ro",
            "--entrypoint",
            "/bin/promtool",
            PROMETHEUS,
        ]
        allrules = out / "all.yaml"
        allrules.write_text(
            yaml.safe_dump(
                {
                    "groups": [
                        dict(g, name=f"{c}.{g['name']}")
                        for c, gs in scoped.items()
                        for g in gs
                    ]
                }
            )
        )
        print(run("docker", "run", "--rm", *mount, "check", "rules", "/work/all.yaml"))
        rules = [
            {
                "name": "blackbox-test",
                "interval": "1m",
                "rules": [
                    r
                    for g in scoped["natsume"]
                    for r in g["rules"]
                    if "record" in r or r.get("alert") == "ProbeMetricsAbsent"
                ],
            }
        ]
        (out / "rules.yaml").write_text(yaml.safe_dump({"groups": rules}))
        tests = []
        for name, values_, checks in [
            (
                "target disappears and recovers",
                "1x4 stale _x20 1x20",
                [("4m", False), ("9m", False), ("16m", True), ("30m", False)],
            ),
            ("short gap", "1x4 stale _x2 1x40", [("16m", False)]),
            ("never scraped", "_x50", [("16m", True)]),
            ("failed probe still present", "0x50", [("16m", False)]),
        ]:
            series = [
                {
                    "series": 'probe_success{cluster="natsume",job="http-get",instance="https://new.example"}',
                    "values": values_,
                },
                {
                    "series": 'probe_success{cluster="natsume",job="custom",instance="https://custom.example"}',
                    "values": "1x50",
                },
                # Neither another cluster nor another job may hide the missing target.
                {
                    "series": 'probe_success{cluster="meruto",job="http-get",instance="https://new.example"}',
                    "values": "1x50",
                },
                {
                    "series": 'probe_success{cluster="natsume",job="other",instance="https://new.example"}',
                    "values": "1x50",
                },
            ]
            labels = {
                "__name__": "ALERTS",
                "alertname": "ProbeMetricsAbsent",
                "alertstate": "firing",
                "severity": "critical",
                "cluster": "natsume",
                "job": "http-get",
                "instance": "https://new.example",
            }
            sample = {
                "labels": "{"
                + ",".join(f"{k}={json.dumps(v)}" for k, v in sorted(labels.items()))
                + "}",
                "value": 1,
            }
            tests.append(
                {
                    "name": name,
                    "interval": "1m",
                    "input_series": series,
                    "promql_expr_test": [
                        {
                            "expr": 'ALERTS{alertname="ProbeMetricsAbsent",alertstate="firing"}',
                            "eval_time": when,
                            "exp_samples": [sample] if firing else [],
                        }
                        for when, firing in checks
                    ],
                }
            )
        (out / "tests.yaml").write_text(
            yaml.safe_dump(
                {
                    "rule_files": ["/work/rules.yaml"],
                    "evaluation_interval": "1m",
                    "tests": tests,
                }
            )
        )
        print(run("docker", "run", "--rm", *mount, "test", "rules", "/work/tests.yaml"))
        print(
            "Probe inventory / add-remove / custom job / missing-recovered / cross-cluster: OK"
        )


if __name__ == "__main__":
    main()
