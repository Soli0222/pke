#!/usr/bin/env python3
"""Overviewの判断クエリを既存inventoryと収集設定から生成する。"""

import argparse
import importlib.util
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = Path("grafana/dashboards/overview.json")
spec = importlib.util.spec_from_file_location(
    "pipeline", ROOT / "scripts/sync-grafana-pipeline-inventory.py"
)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def metric(name, **labels):
    labels = {"cluster": "$cluster", **labels}
    return (
        name + "{" + ",".join(f"{k}={json.dumps(v)}" for k, v in labels.items()) + "}"
    )


def fresh(series):
    return f"({series} and (timestamp({series}) > time() - 120))"


def m(name, **labels):
    return fresh(metric(name, **labels))


def joined(parts):
    return "(" + " or ".join(parts) + ")"


def fixed(expr, name):
    return pipeline.label(f"({expr})", {"check": name})


def observed(expr, anchor, keys):
    # 0=healthy, 1=needs attention, -1=unknown. Retain an observed entity
    # when its status disappears, without substituting a healthy zero.
    return f"(({expr}) or on({keys}) (({anchor}) * 0 - 1))"


def scalar(expr, name):
    return fixed(f"({expr}) or vector(-1)", name)


def provider(job):
    return scalar(f"1 - min({m('up', job=job)})", "収集 / " + job)


def counts(states):
    return [f"count(({states}) == {value}) or vector(0)" for value in [1, -1]]


def expected_queries(root=ROOT):
    hosts, targets, paths, groups = pipeline.expected(root)
    all_queries = {}
    for cluster, nodes in sorted(hosts.items()):
        values = yaml.safe_load(
            (
                root
                / f"flux/clusters/{cluster}/apps/monitoring-rules/helmrelease-monitoring-rules.yaml"
            ).read_text()
        )["spec"]["values"]
        defaults = yaml.safe_load(
            (root / "charts/monitoring-rules/values.yaml").read_text()
        )
        queries = {}
        parts = []
        for node in sorted(nodes):
            ready = f"1 - min({m('kube_node_status_condition', node=node, condition='Ready', status='true')})"
            pressure = [
                f"max({m('kube_node_status_condition', node=node, condition=c, status='true')})"
                for c in ["MemoryPressure", "DiskPressure", "PIDPressure"]
            ]
            # Unknown condition remains unknown even if another condition is healthy.
            bad = f"max({joined([fixed(e, str(i)) for i, e in enumerate([ready] + pressure)])})"
            missing = joined(
                [
                    f"absent({m('kube_node_status_condition', node=node, condition=c, status='true')})"
                    for c in ["Ready", "MemoryPressure", "DiskPressure", "PIDPressure"]
                ]
            )
            parts.append(
                scalar(f"({bad} == 1) or (({bad}) unless on() ({missing}))", node)
            )
        queries[1] = counts(joined(parts))
        parts = [provider("kube-state-metrics")]
        for kind, desired, available in [
            (
                "deployment",
                "kube_deployment_spec_replicas",
                "kube_deployment_status_replicas_available",
            ),
            (
                "statefulset",
                "kube_statefulset_replicas",
                "kube_statefulset_status_replicas_ready",
            ),
            (
                "daemonset",
                "kube_daemonset_status_desired_number_scheduled",
                "kube_daemonset_status_number_available",
            ),
        ]:
            keys = "cluster,namespace," + kind
            a = f"max by({keys}) ({m(desired)})"
            b = f"max by({keys}) ({m(available)})"
            parts.append(observed(f"({a}) > bool ({b})", a, keys))
        queries[2] = counts(joined(parts))
        phase = m("kube_pod_status_phase")
        active = f"max by(cluster,namespace,pod,uid) ({phase} and {metric('kube_pod_status_phase').replace('}', ',phase=~"Pending|Running|Unknown"}')} == 1)"
        # Completed/terminating Pods do not require Ready. Failed Pods stay visible.
        active = f"({active}) unless on(cluster,namespace,pod,uid) ({m('kube_pod_deletion_timestamp')} > 0)"
        ready = f"max by(cluster,namespace,pod,uid) ({m('kube_pod_status_ready', condition='true')})"
        failed = f"max by(cluster,namespace,pod,uid) ({m('kube_pod_status_phase', phase='Failed')} == 1)"
        podstate = observed(
            f"(1 - ({ready})) and on(cluster,namespace,pod,uid) ({active})",
            active,
            "cluster,namespace,pod,uid",
        )
        queries[3] = counts(joined([provider("kube-state-metrics"), podstate, failed]))
        anchor = f"max by(cluster,namespace,persistentvolumeclaim) ({m('kube_persistentvolumeclaim_info')})"
        bound = f"max by(cluster,namespace,persistentvolumeclaim) ({m('kube_persistentvolumeclaim_status_phase', phase='Bound')})"
        queries[4] = counts(
            joined(
                [
                    provider("kube-state-metrics"),
                    observed(
                        f"1 - ({bound})",
                        anchor,
                        "cluster,namespace,persistentvolumeclaim",
                    ),
                ]
            )
        )
        flux = metric("flux_resource_info")
        bad = flux.replace("}", ',ready!="True"}')
        suspended = flux.replace("}", ',suspended="True"}')
        states = f"max by(cluster,kind,exported_namespace,name) (({fresh(bad)} == 1) or ({fresh(suspended)} == 1))"
        queries[5] = [
            f"count({states}) or vector(0)",
            f"absent({fresh(flux)}) or vector(0)",
        ]
        pv = f'max by(cluster,volume) (label_replace({m("kube_persistentvolume_info", csi_driver="driver.longhorn.io")}, "volume", "$1", "csi_volume_handle", "(.+)"))'
        cap = f"max by(cluster,volume) ({m('longhorn_volume_capacity_bytes')})"
        anchor = joined([pv, f"({cap}) > bool 0"])
        health = f"max by(cluster,volume) ({m('longhorn_volume_robustness', state='healthy')})"
        fault = f"max by(cluster,volume) ({m('longhorn_volume_robustness', state='faulted')})"
        degraded = f"max by(cluster,volume) ({m('longhorn_volume_robustness', state='degraded')})"
        attached = (
            f"max by(cluster,volume) ({m('longhorn_volume_state', state='attached')})"
        )
        known = f"(({fault}) == 1) or ((({degraded}) == 1) and on(cluster,volume) (({attached}) == 1)) or (((({health}) == 1) and on(cluster,volume) (({attached}) == 1)) * 0)"
        queries[6] = counts(
            joined(
                [
                    provider("kube-state-metrics"),
                    observed(known, anchor, "cluster,volume"),
                ]
            )
        )
        dbs = values.get("databases", [])
        dbparts = []
        backups = []
        for db in dbs:
            labels = {"namespace": db["namespace"], "cnpg_cluster": db["name"]}
            dbparts.append(
                scalar(
                    f"1 - min({m('cnpg_collector_up', **labels)})",
                    db["name"] + " / collector",
                )
            )
            if db.get("archive"):
                threshold = (
                    values.get("groups", {})
                    .get("cnpg", {})
                    .get("rules", {})
                    .get("CNPGWALArchiveStalled", {})
                    .get(
                        "threshold",
                        defaults["groups"]["cnpg"]["rules"]["CNPGWALArchiveStalled"][
                            "threshold"
                        ],
                    )
                )
                dbparts.append(
                    scalar(
                        f"max({m('cnpg_pg_stat_archiver_seconds_since_last_archival', **labels)}) > bool {threshold}",
                        db["name"] + " / WAL",
                    )
                )
            threshold = (
                values.get("groups", {})
                .get("cnpg", {})
                .get("rules", {})
                .get("CNPGDumpBackupStale", {})
                .get(
                    "threshold",
                    defaults["groups"]["cnpg"]["rules"]["CNPGDumpBackupStale"][
                        "threshold"
                    ],
                )
            )
            if db.get("dumpCronJob"):
                last = f"max({m('kube_cronjob_status_last_successful_time', namespace=db['namespace'], cronjob=db['dumpCronJob'])}) > 0"
                backups.append(
                    scalar(
                        f"(time() - ({last})) > bool {threshold}",
                        db["name"] + " / pg_dump",
                    )
                )
            else:
                last = f"max({m('cnpg_collector_last_available_backup_timestamp', **labels)}) > 0"
                # A missing/zero timestamp is unverified, never a 1970 success.
                backups.append(
                    scalar(
                        f"(time() - ({last})) > bool {threshold}",
                        db["name"] + " / base backup",
                    )
                )
        queries[7] = counts(joined(dbparts)) if dbparts else ["vector(-2)"] * 2
        queries[12] = counts(joined(backups)) if backups else ["vector(-2)"] * 2
        probe_path = (
            root
            / f"flux/clusters/{cluster}/apps/blackbox-exporter-probes/helmrelease-blackbox-exporter-probes.yaml"
        )
        probes = []
        if probe_path.exists():
            pvls = yaml.safe_load(probe_path.read_text())["spec"]["values"]
            for app in pvls["applications"]:
                for url in app["targets"]:
                    probes.append(
                        scalar(
                            f"1 - min({m('probe_success', job='http-get', instance=url)})",
                            url,
                        )
                    )
        queries[8] = counts(joined(probes)) if probes else ["vector(-2)"] * 2
        p = pipeline.expressions(targets, paths, groups)
        queries[9] = [
            f"({p[3]}) + (count(({p[12]}) > 300) or vector(0)) + (count(({p[21]}) > 300) or vector(0))",
            f"({p[2]}) + (count(({p[12]}) == -1) or vector(0)) + (count(({p[21]}) == -1) or vector(0))",
        ]
        members = [
            scalar(f"1 - min({m('etcd_server_has_leader', job='etcd', instance=h)})", h)
            for h, units in nodes.items()
            if "etcd.service" in units
        ]
        queries[10] = counts(joined(members)) if members else ["vector(-2)"] * 2
        keys = "cluster,exported_namespace,name"
        ready = f"max by({keys}) ({m('certmanager_certificate_ready_status', job='cert-manager', condition='True')})"
        expiry = f"max by({keys}) ({m('certmanager_certificate_expiration_timestamp_seconds', job='cert-manager')})"
        anchor = f"max by({keys}) ({m('certmanager_certificate_ready_status', job='cert-manager')})"
        known = f"(({ready}) == 0) * 0 + 1 or (({expiry}) < time() + 14*86400) * 0 + 1 or ((({ready}) == 1) * 0 and on({keys}) (({expiry}) >= time() + 14*86400))"
        queries[11] = counts(
            joined([provider("cert-manager"), observed(known, anchor, keys)])
        )
        all_queries[cluster] = queries
    # Select a branch using a cluster-labelled constant; no other cluster can
    # satisfy a missing target, including the not-applicable branches.
    result = {}
    for panel in range(1, 13):
        result[panel] = []
        for i in range(2):
            result[panel].append(
                joined(
                    [
                        f"({queries[panel][i]}) and on() ({pipeline.label('vector(1)', {'cluster': c})} and on(cluster) {pipeline.label('vector(1)', {'cluster': '$cluster'})})"
                        for c, queries in all_queries.items()
                    ]
                )
            )
    return hosts, result


def synchronize(document, root=ROOT):
    hosts, queries = expected_queries(root)
    v = document["spec"]["templating"]["list"][0]
    v["query"] = ",".join(sorted(hosts))
    v["options"] = [
        {"text": c, "value": c, "selected": c == v["current"]["value"]}
        for c in sorted(hosts)
    ]
    for p in document["spec"]["panels"]:
        if p["id"] in queries:
            for t, e in zip(p["targets"], queries[p["id"]]):
                t["expr"] = e
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / DASHBOARD
    original = path.read_text()
    rendered = (
        json.dumps(synchronize(json.loads(original)), ensure_ascii=False, indent=2)
        + "\n"
    )
    if args.check and rendered != original:
        raise SystemExit("Overview stale: run scripts/sync-grafana-overview.py")
    if not args.check:
        path.write_text(rendered)
    print("Overview: 12 domains; expectations synchronized")


if __name__ == "__main__":
    main()
