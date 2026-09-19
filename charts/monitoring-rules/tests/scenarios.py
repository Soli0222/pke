"""Prometheus の時系列 fixture。期待 entity と時刻は query から生成せず明示する。"""

import json

N = {"cluster": "natsume"}
P = {**N, "namespace": "app", "pod": "pod-a", "uid": "uid-a"}
V = {**N, "namespace": "app", "persistentvolumeclaim": "data"}
H = {**N, "job": "integrations/unix", "instance": "node-a"}
F = {**H, "device": "/dev/vda1", "mountpoint": "/", "fstype": "ext4"}
D = {**N, "namespace": "app", "cnpg_cluster": "db", "pod": "db-1"}
# 正常 0..4m / 障害 5..25m / 回復 26m..。5m range を使う rule も36mには解消する。
BAD = "0x4 1x20 0x15"
ZERO = "1x4 0x20 1x15"


def s(metric, values, labels=None, **extra):
    labels = {**(labels if labels is not None else N), **extra}
    return {
        "series": metric
        + "{"
        + ",".join(f"{k}={json.dumps(v)}" for k, v in sorted(labels.items()))
        + "}",
        "values": values,
    }


def scenarios():
    cases = []

    def add(
        alert,
        series,
        labels,
        name="normal, sustained failure, recovery",
        checks=None,
        interval="1m",
    ):
        checks = (
            checks
            if checks is not None
            else [("4m", []), ("24m", [labels]), ("36m", [])]
        )
        cases.append(
            {
                "alert": alert,
                "name": name,
                "series": series,
                "checks": checks,
                "interval": interval,
            }
        )

    def quiet(alert, series, name, when="36m"):
        add(alert, series, {}, name, [(when, [])])

    def absent(alert, metric, labels, output=None):
        # 同名対象が別クラスタに残っていても natsume だけ欠測。短い欠測からは復帰する。
        add(
            alert,
            [
                s(metric, "1x4 stale _x20 1x20", labels),
                s(metric, "1x50", {**labels, "cluster": "meruto"}),
            ],
            output or labels,
            "one cluster disappears and recovers",
            [("4m", []), ("9m", []), ("16m", [output or labels]), ("30m", [])],
        )
        quiet(
            alert,
            [
                s(metric, "1x4 stale _x2 1x40", labels),
                s(metric, "1x50", {**labels, "cluster": "meruto"}),
            ],
            "short gap recovers",
            "16m",
        )

    add(
        "TargetDown",
        [
            s("up", ZERO, {**N, "job": "exporter", "instance": "same"}),
            s(
                "up",
                "1x45",
                {"cluster": "meruto", "job": "exporter", "instance": "same"},
            ),
        ],
        {**N, "job": "exporter", "instance": "same"},
    )
    absent(
        "KubeStateMetricsAbsent", "kube_node_info", {**N, "job": "kube-state-metrics"}
    )
    absent("NodeExporterAbsent", "node_uname_info", H)
    # absent_over_time retains job and instance. Stable healthy paths for both clusters.
    base = [
        s(
            "alloy_build_info",
            "1x50",
            {
                "cluster": c,
                "job": j,
                **({"instance": "node-a"} if j == "alloy-host" else {}),
            },
        )
        for c in ("natsume", "meruto")
        for j in ("alloy", "alloy-host")
    ]
    base[1] = s(
        "alloy_build_info",
        "1x4 stale _x20 1x20",
        {**N, "job": "alloy-host", "instance": "node-a"},
    )
    add(
        "MetricsStale",
        base,
        {**N, "job": "alloy-host", "instance": "node-a", "telemetry_path": "host"},
        checks=[
            ("4m", []),
            ("9m", []),
            (
                "16m",
                [
                    {
                        **N,
                        "job": "alloy-host",
                        "instance": "node-a",
                        "telemetry_path": "host",
                    }
                ],
            ),
            ("30m", []),
        ],
    )
    for alert, metric, values in [
        ("AlloyRemoteWriteFailing", "samples_failed_total", "0x4 10+10x20 210x15"),
        ("AlloyRemoteWriteRetrying", "samples_retried_total", "0x4 120+120x20 2520x15"),
        ("AlloyRemoteWriteBacklog", "samples_pending", "0x4 20000x20 0x15"),
        (
            "AlloyRemoteWriteStalled",
            "queue_highest_sent_timestamp_seconds",
            "0+60x4 0x20 1560+60x15",
        ),
    ]:
        labels = {
            **N,
            "job": "alloy-host",
            "instance": "node-a",
            "hostname": "node-a",
            "component_id": "prometheus.remote_write.default",
        }
        add(alert, [s("prometheus_remote_storage_" + metric, values, labels)], labels)
        if metric.endswith("_total"):
            quiet(
                alert,
                [s("prometheus_remote_storage_" + metric, "100x4 0x40", labels)],
                "counter reset is not a failure",
            )
    alive = s("kube_pod_status_phase", "1x45", P, phase="Running")
    container = {**P, "container": "app"}
    for alert, metric, extra in [
        (
            "KubePodCrashLooping",
            "kube_pod_container_status_waiting_reason",
            {"reason": "CrashLoopBackOff"},
        ),
        ("KubePodNotReady", "kube_pod_status_ready", {"condition": "false"}),
        (
            "KubeContainerWaiting",
            "kube_pod_container_status_waiting_reason",
            {"reason": "ImagePullBackOff"},
        ),
    ]:
        entity = P if alert == "KubePodNotReady" else container
        add(alert, [s(metric, BAD, entity, **extra), alive], entity)
        quiet(
            alert,
            [
                s(metric, "1x45", entity, **extra),
                s("kube_pod_status_phase", "0x45", P, phase="Running"),
                s("kube_pod_status_phase", "1x45", P, phase="Succeeded"),
            ],
            "completed pod",
        )
        quiet(
            alert,
            [
                s(metric, "1x45", entity, **extra),
                alive,
                s("kube_pod_deletion_timestamp", "1x45", P),
            ],
            "terminating pod",
        )
        quiet(
            alert, [s(metric, "0x4 1x3 0x40", entity, **extra), alive], "short rollout"
        )
    add(
        "KubePodCrashLooping",
        [
            s(
                "kube_pod_init_container_status_waiting_reason",
                BAD,
                container,
                reason="CrashLoopBackOff",
            ),
            alive,
        ],
        container,
        "init container crashloop",
    )
    add(
        "KubeContainerOOMKilled",
        [
            s(
                "kube_pod_container_status_last_terminated_reason",
                "0x4 1x40",
                container,
                reason="OOMKilled",
            ),
            s(
                "kube_pod_container_status_last_terminated_timestamp",
                "300x45",
                container,
            ),
        ],
        container,
        checks=[("4m", []), ("6m", [container]), ("16m", [])],
    )
    quiet(
        "KubeContainerOOMKilled",
        [
            s(
                "kube_pod_container_status_last_terminated_reason",
                "1x45",
                container,
                reason="OOMKilled",
            ),
            s("kube_pod_container_status_last_terminated_timestamp", "0x45", container),
        ],
        "old OOM is not current",
    )
    for alert, key, wanted, actual in [
        (
            "KubeDeploymentReplicasMismatch",
            "deployment",
            "kube_deployment_spec_replicas",
            "kube_deployment_status_replicas_available",
        ),
        (
            "KubeStatefulSetReplicasMismatch",
            "statefulset",
            "kube_statefulset_replicas",
            "kube_statefulset_status_replicas_ready",
        ),
        (
            "KubeDaemonSetNotScheduled",
            "daemonset",
            "kube_daemonset_status_desired_number_scheduled",
            "kube_daemonset_status_current_number_scheduled",
        ),
    ]:
        entity = {**N, "namespace": "app", key: "workload"}
        add(
            alert,
            [
                s(wanted, "1x45", entity, instance="ksm-a"),
                s(wanted, "1x45", entity, instance="ksm-b"),
                s(actual, ZERO, entity, instance="ksm-a"),
                s(actual, ZERO, entity, instance="ksm-b"),
            ],
            entity,
            "duplicate KSM targets, failure and recovery",
        )
    job = {**N, "namespace": "app", "job_name": "backup-old"}
    job_series = [
        s("kube_job_failed", "1x45", job, condition="true"),
        s("kube_job_status_start_time", "0x45", job),
        s("kube_job_owner", "1x45", job, owner_kind="CronJob", owner_name="backup"),
        s(
            "kube_cronjob_status_last_successful_time",
            "0x25 1560x15",
            {**N, "namespace": "app", "cronjob": "backup"},
        ),
    ]
    add(
        "KubeJobFailed",
        job_series,
        job,
        checks=[("4m", []), ("24m", [job]), ("36m", [])],
        name="failed CronJob superseded by later success",
    )
    quiet(
        "KubeJobFailed",
        [
            s("kube_job_failed", "1x1600", job, condition="true"),
            s("kube_job_status_start_time", "0x1600", job),
        ],
        "old failed Job",
        when="25h",
    )
    for alert, condition, values in [
        ("KubeNodeNotReady", "Ready", ZERO),
        ("KubeNodeMemoryPressure", "MemoryPressure", BAD),
        ("KubeNodeDiskPressure", "DiskPressure", BAD),
    ]:
        add(
            alert,
            [
                s(
                    "kube_node_status_condition",
                    values,
                    {**N, "node": "node-a"},
                    condition=condition,
                    status="true",
                )
            ],
            {**N, "node": "node-a"},
        )
    add(
        "KubeletTooManyPods",
        [
            s("kubelet_running_pods", "5x4 99x20 5x15", {**N, "node": "node-a"}),
            s(
                "kube_node_status_allocatable",
                "100x45",
                {**N, "node": "node-a"},
                resource="pods",
            ),
        ],
        {**N, "node": "node-a"},
    )
    bound = s("kube_persistentvolumeclaim_status_phase", "1x500", V, phase="Bound")
    writable = s(
        "kube_persistentvolumeclaim_access_mode",
        "1x500",
        V,
        access_mode="ReadWriteOnce",
    )
    volume = [bound, writable]
    for alert in ("KubePersistentVolumeSpaceLow", "KubePersistentVolumeSpaceCritical"):
        add(
            alert,
            volume
            + [
                s(
                    "kubelet_volume_stats_available_bytes",
                    "50x4 3x20 50x15",
                    V,
                    instance="kubelet-a",
                ),
                s(
                    "kubelet_volume_stats_available_bytes",
                    "50x4 3x20 50x15",
                    V,
                    instance="kubelet-b",
                ),
                s("kubelet_volume_stats_capacity_bytes", "100x45", V),
            ],
            V,
            "duplicate volume stats and overlapping thresholds",
        )
        quiet(
            alert,
            volume
            + [
                s("kubelet_volume_stats_available_bytes", "0x45", V),
                s("kubelet_volume_stats_capacity_bytes", "0x45", V),
            ],
            "zero capacity is not normal space",
        )
        quiet(
            alert,
            volume
            + [
                s("kubelet_volume_stats_available_bytes", "1x45", V),
                s("kubelet_volume_stats_capacity_bytes", "100x45", V),
                s(
                    "kube_persistentvolumeclaim_access_mode",
                    "1x45",
                    V,
                    access_mode="ReadOnlyMany",
                ),
            ],
            "read-only volume",
        )
        quiet(
            alert,
            [
                s("kubelet_volume_stats_available_bytes", "1x45", V),
                s("kubelet_volume_stats_capacity_bytes", "100x45", V),
            ],
            "not Bound",
        )
    mounted = [
        s(
            "kube_pod_spec_volumes_persistentvolumeclaims_info",
            "1x100",
            {**P, "persistentvolumeclaim": "data", "volume": "data"},
        ),
        s("kube_pod_status_phase", "1x100", P, phase="Running"),
    ]
    add(
        "KubePersistentVolumeStatsMissing",
        volume
        + mounted
        + [s("kubelet_volume_stats_capacity_bytes", "100x4 stale _x34 100x20", V)],
        V,
        checks=[("10m", []), ("37m", [V]), ("42m", [])],
    )
    quiet(
        "KubePersistentVolumeStatsMissing",
        volume,
        "unmounted PVC has no kubelet stats",
        when="40m",
    )
    add(
        "KubePersistentVolumeStatsInvalid",
        volume + [s("kubelet_volume_stats_capacity_bytes", "100x4 0x35 100x10", V)],
        V,
        checks=[("10m", []), ("36m", [V]), ("45m", [])],
    )
    # 6h scrape history, 300 samples, 30m for; a resize resets the eligibility window.
    predict = [
        s("kubelet_volume_stats_available_bytes", "500-1x400", V),
        s("kubelet_volume_stats_capacity_bytes", "1000x400", V),
        bound,
        writable,
    ]
    add(
        "KubePersistentVolumeFillingUp",
        predict,
        V,
        "4h exhaustion forecast",
        checks=[("200m", []), ("390m", [V])],
    )
    quiet(
        "KubePersistentVolumeFillingUp",
        [
            predict[0],
            s("kubelet_volume_stats_capacity_bytes", "1000x349 2000x50", V),
            bound,
            writable,
        ],
        "resize invalidates history",
        when="390m",
    )
    quiet(
        "KubePersistentVolumeFillingUp",
        [
            s("kubelet_volume_stats_available_bytes", "100-1x100", V),
            s("kubelet_volume_stats_capacity_bytes", "1000x100", V),
            bound,
            writable,
        ],
        "insufficient history",
        when="90m",
    )
    for alert, metric, denom, bad in [
        (
            "HostMemoryLow",
            "node_memory_MemAvailable_bytes",
            "node_memory_MemTotal_bytes",
            3,
        ),
        (
            "HostConntrackHigh",
            "node_nf_conntrack_entries",
            "node_nf_conntrack_entries_limit",
            99,
        ),
    ]:
        add(alert, [s(metric, f"50x4 {bad}x20 50x15", H), s(denom, "100x45", H)], H)
    for alert, mode, values in [
        ("HostCPUHigh", "idle", "0+60x4 240+0x20 300+60x15"),
        ("HostCPUStealHigh", "steal", "0x4 60+60x20 1260x15"),
    ]:
        add(
            alert,
            [s("node_cpu_seconds_total", values, H, cpu="0", mode=mode)],
            {**N, "instance": "node-a"},
        )
    add(
        "HostLoadHigh",
        [
            s("node_load15", "0x4 10x20 0x15", H),
            s("node_cpu_seconds_total", "0+60x45", H, cpu="0", mode="idle"),
        ],
        {**N, "instance": "node-a"},
    )
    fsbase = [
        s("node_filesystem_size_bytes", "1000x500", F),
        s("node_filesystem_readonly", "0x500", F),
    ]
    for alert in ("HostFilesystemSpaceLow", "HostFilesystemSpaceCritical"):
        add(
            alert,
            fsbase + [s("node_filesystem_avail_bytes", "500x4 30x20 500x15", F)],
            F,
        )
        quiet(
            alert,
            [
                s("node_filesystem_size_bytes", "1000x45", F),
                s("node_filesystem_readonly", "1x45", F),
                s("node_filesystem_avail_bytes", "1x45", F),
            ],
            "read-only filesystem",
        )
        quiet(
            alert,
            [
                s("node_filesystem_size_bytes", "1000x45", {**F, "fstype": "tmpfs"}),
                s("node_filesystem_readonly", "0x45", {**F, "fstype": "tmpfs"}),
                s("node_filesystem_avail_bytes", "1x45", {**F, "fstype": "tmpfs"}),
            ],
            "virtual filesystem",
        )
    add(
        "HostFilesystemFillingUp",
        fsbase + [s("node_filesystem_avail_bytes", "500-1x400", F)],
        F,
        "24h exhaustion forecast",
        checks=[("200m", []), ("390m", [F])],
    )
    quiet(
        "HostFilesystemFillingUp",
        [
            s("node_filesystem_size_bytes", "1000x349 2000x50", F),
            fsbase[1],
            s("node_filesystem_avail_bytes", "500-1x400", F),
        ],
        "resize invalidates history",
        when="390m",
    )
    quiet(
        "HostFilesystemFillingUp",
        fsbase + [s("node_filesystem_avail_bytes", "100-1x100", F)],
        "insufficient history",
        when="90m",
    )
    add(
        "HostInodesLow",
        [
            s("node_filesystem_files", "1000x45", F),
            s("node_filesystem_files_free", "500x4 3x20 500x15", F),
            fsbase[1],
        ],
        F,
    )
    add(
        "HostDiskIOSaturation",
        [s("node_disk_io_time_seconds_total", "0x4 60+60x20 1260x15", H, device="vda")],
        {**H, "device": "vda"},
    )
    quiet(
        "HostDiskIOSaturation",
        [s("node_disk_io_time_seconds_total", "0+60x45", H, device="dm-0")],
        "logical disk excluded",
    )
    add(
        "HostOOMKill",
        [s("node_vmstat_oom_kill", "0x4 1x40", H)],
        H,
        checks=[("4m", []), ("6m", [H]), ("12m", [])],
    )
    quiet("HostOOMKill", [s("node_vmstat_oom_kill", "100x4 0x40", H)], "counter reset")
    add(
        "HostRebooted",
        [s("node_boot_time_seconds", "0x4 300x40", H)],
        H,
        checks=[("4m", []), ("6m", [H]), ("16m", [])],
    )
    quiet(
        "HostRebooted",
        [s("node_boot_time_seconds", "300x45", H)],
        "initial observation is not a reboot",
    )
    add("HostClockUnsynchronized", [s("node_timex_sync_status", ZERO, H)], H)
    quiet(
        "HostClockUnsynchronized", [], "unsupported timex remains explicitly uncovered"
    )
    add(
        "HostNetworkErrors",
        [
            s(
                "node_network_receive_errs_total",
                "0x4 120+120x20 2520x15",
                H,
                device="ens3",
            ),
            s("node_network_transmit_errs_total", "0x45", H, device="ens3"),
        ],
        {**H, "device": "ens3"},
    )
    quiet(
        "HostNetworkErrors",
        [
            s("node_network_receive_errs_total", "0+120x45", H, device="veth123"),
            s("node_network_transmit_errs_total", "0x45", H, device="veth123"),
        ],
        "virtual network interface",
    )
    for alert, state in [
        ("HostSystemdUnitFailed", "failed"),
        ("HostSystemdUnitInactive", "inactive"),
    ]:
        add(
            alert,
            [s("node_systemd_unit_state", BAD, H, name="alloy.service", state=state)],
            {**N, "instance": "node-a", "name": "alloy.service"},
        )
        quiet(
            alert,
            [
                s(
                    "node_systemd_unit_state",
                    "1x45",
                    H,
                    name="absent.service",
                    state=state,
                )
            ],
            "unit not in inventory",
        )
    E = {**N, "job": "etcd", "instance": "node-a"}
    add(
        "EtcdNoLeader",
        [s("etcd_server_has_leader", ZERO, E)],
        {**N, "instance": "node-a"},
    )
    add(
        "EtcdHighLeaderChanges",
        [s("etcd_server_leader_changes_seen_total", "0x4 1+1x20 21x30", E)],
        E,
        checks=[("4m", []), ("24m", [E]), ("45m", [])],
    )
    quiet(
        "EtcdHighLeaderChanges",
        [s("etcd_server_leader_changes_seen_total", "0x4 1x45", E)],
        "one planned leader change",
    )
    fsync = [
        s("etcd_disk_wal_fsync_duration_seconds_bucket", "0+1x45", E, le="0.5"),
        s("etcd_disk_wal_fsync_duration_seconds_bucket", "0+20x45", E, le="1"),
        s("etcd_disk_wal_fsync_duration_seconds_bucket", "0+20x45", E, le="+Inf"),
        s("etcd_disk_wal_fsync_duration_seconds_count", "0+20x45", E),
    ]
    add(
        "EtcdHighFsyncDuration",
        fsync,
        {**N, "instance": "node-a"},
        checks=[("4m", []), ("24m", [{**N, "instance": "node-a"}])],
    )
    quiet(
        "EtcdHighFsyncDuration",
        fsync[:-1] + [s("etcd_disk_wal_fsync_duration_seconds_count", "0+1x45", E)],
        "low sample histogram",
    )
    quiet("EtcdHighFsyncDuration", [], "missing histogram")
    add(
        "EtcdDbSizeExceedingQuota",
        [
            s("etcd_mvcc_db_total_size_in_bytes", "50x4 99x20 50x15", E),
            s("etcd_server_quota_backend_bytes", "100x45", E),
        ],
        {**N, "instance": "node-a"},
    )
    add(
        "EtcdHighFailedProposals",
        [s("etcd_server_proposals_failed_total", "0x4 10+10x20 210x20", E)],
        E,
    )
    quiet(
        "EtcdHighFailedProposals",
        [s("etcd_server_proposals_failed_total", "100x4 0x45", E)],
        "etcd counter reset",
    )
    C = {**N, "exported_namespace": "app", "name": "tls"}
    CO = {**C, "namespace": "app"}
    add(
        "CertManagerCertNotReady",
        [s("certmanager_certificate_ready_status", ZERO, C, condition="True")],
        CO,
    )
    for alert in ("CertExpiringSoon", "CertExpiryCritical"):
        add(
            alert,
            [
                s(
                    "certmanager_certificate_expiration_timestamp_seconds",
                    "100000x1599 10000000x200",
                    C,
                ),
                s(
                    "certmanager_certificate_renewal_timestamp_seconds",
                    "0x1599 9000000x200",
                    C,
                ),
            ],
            CO,
            "scheduled renewal overdue then recovered",
            checks=[("30m", []), ("25h", [CO]), ("28h", [])],
        )
        quiet(
            alert,
            [
                s(
                    "certmanager_certificate_expiration_timestamp_seconds",
                    "200000x1700",
                    C,
                ),
                s(
                    "certmanager_certificate_renewal_timestamp_seconds",
                    "150000x1700",
                    C,
                ),
            ],
            "normal renewal window",
            when="25h",
        )
        add(
            alert,
            [s("certmanager_certificate_expiration_timestamp_seconds", "0x1700", C)],
            CO,
            "expired certificate even without renewal metric",
            checks=[("25h", [CO])],
        )
    add("CNPGCollectorDown", [s("cnpg_collector_up", ZERO, D)], D)
    # Declared DBs in both clusters have a heartbeat except the single failing DB.
    dbbase = [
        s(
            "cnpg_collector_up",
            "1x60",
            {"cluster": c, "namespace": "app", "cnpg_cluster": db},
        )
        for c in ("natsume", "meruto")
        for db in ("db", "disabled-db")
    ]
    dbbase[0] = s(
        "cnpg_collector_up",
        "1x4 stale _x20 1x30",
        {**N, "namespace": "app", "cnpg_cluster": "db"},
    )
    add(
        "CNPGMetricsAbsent",
        dbbase,
        {**N, "namespace": "app", "cnpg_cluster": "db"},
        checks=[
            ("4m", []),
            ("9m", []),
            ("16m", [{**N, "namespace": "app", "cnpg_cluster": "db"}]),
            ("30m", []),
        ],
    )
    DB = {**N, "namespace": "app", "cnpg_cluster": "db"}
    add(
        "CNPGPostmasterRestarted",
        [s("cnpg_pg_postmaster_start_time", "0x4 300x40", D)],
        DB,
        checks=[("4m", []), ("6m", [DB]), ("16m", [])],
    )
    quiet(
        "CNPGPostmasterRestarted",
        [s("cnpg_pg_postmaster_start_time", "300x45", D)],
        "initial DB observation",
    )
    add("CNPGReplicationLag", [s("cnpg_pg_replication_lag", "0x4 100x20 0x15", D)], D)
    quiet(
        "CNPGReplicationLag",
        [s("cnpg_pg_replication_lag", "100x45", {**D, "cnpg_cluster": "disabled-db"})],
        "single instance DB opts out",
    )
    add(
        "CNPGWALArchiveFailing",
        [
            s("cnpg_pg_stat_archiver_last_failed_time", "0x4 300x40", D),
            s("cnpg_pg_stat_archiver_last_archived_time", "0x25 1560x15", D),
        ],
        D,
    )
    quiet(
        "CNPGWALArchiveFailing",
        [
            s("cnpg_pg_stat_archiver_last_failed_time", "300x4 0x40", D),
            s("cnpg_pg_stat_archiver_last_archived_time", "600x45", D),
        ],
        "archiver reset after success",
    )
    add(
        "CNPGWALArchiveStalled",
        [s("cnpg_pg_stat_archiver_seconds_since_last_archival", "0x4 3600x20 0x15", D)],
        D,
    )
    quiet(
        "CNPGWALArchiveStalled",
        [
            s(
                "cnpg_pg_stat_archiver_seconds_since_last_archival",
                "3600x45",
                {**D, "cnpg_cluster": "disabled-db"},
            )
        ],
        "archive disabled",
    )
    CJ = {**N, "namespace": "app", "cronjob": "db-pg-dump"}
    CJO = {**CJ, "cnpg_cluster": "db"}
    for never in (False, True):
        series = [
            s("kube_cronjob_created", "0x2200", CJ),
            s("kube_cronjob_spec_suspend", "0x2200", CJ),
        ]
        series.append(
            s(
                "kube_cronjob_status_last_successful_time",
                ("_x1999 " if never else "0x1999 ") + "120000x200",
                CJ,
            )
        )
        add(
            "CNPGDumpBackupStale",
            series,
            CJO,
            "never executed" if never else "daily pg_dump stale and later success",
            checks=[("29h", []), ("31h", [CJO]), ("34h", [])],
        )
    quiet(
        "CNPGDumpBackupStale",
        [
            s("kube_cronjob_created", "0x2000", CJ),
            s("kube_cronjob_spec_suspend", "1x2000", CJ),
        ],
        "suspended backup",
        when="31h",
    )
    L = {**N, "volume": "vol-a", "pvc_namespace": "app", "pvc": "data"}
    add(
        "LonghornVolumeFaulted",
        [s("longhorn_volume_robustness", BAD, L, state="faulted")],
        L,
    )
    add(
        "LonghornVolumeFaulted",
        [
            s(
                "longhorn_volume_robustness",
                BAD,
                {**N, "volume": "unmapped"},
                state="faulted",
            )
        ],
        {**N, "volume": "unmapped"},
        "PVC mapping missing still alerts",
    )
    add(
        "LonghornVolumeDegraded",
        [
            s("longhorn_volume_robustness", BAD, L, state="degraded"),
            s("longhorn_volume_state", "1x45", L, state="attached"),
        ],
        L,
        "single replica and attached degraded",
    )
    quiet(
        "LonghornVolumeDegraded",
        [
            s("longhorn_volume_robustness", "1x45", L, state="degraded"),
            s("longhorn_volume_state", "0x45", L, state="attached"),
        ],
        "intentionally detached",
    )
    quiet(
        "LonghornVolumeDegraded",
        [
            s("longhorn_volume_robustness", "0x4 1x5 0x40", L, state="degraded"),
            s("longhorn_volume_state", "1x50", L, state="attached"),
        ],
        "brief rebuild",
    )
    for kind in ("Node", "Disk"):
        entity = {
            **N,
            "node": "node-a",
            **({"disk": "disk-a"} if kind == "Disk" else {}),
        }
        prefix = "longhorn_node_storage_" if kind == "Node" else "longhorn_disk_"
        for level in ("Low", "Critical"):
            add(
                f"Longhorn{kind}Space{level}",
                [
                    s(prefix + "capacity_bytes", "100x45", entity),
                    s(prefix + "reservation_bytes", "20x45", entity),
                    s(prefix + "usage_bytes", "20x4 78x20 20x15", entity),
                ],
                entity,
                "reserve is subtracted from capacity",
            )
    FL = {**N, "kind": "HelmRelease", "exported_namespace": "app", "name": "workload"}
    FLO = {**FL, "namespace": "app"}
    for alert, state in [
        ("FluxReconciliationFailed", "False"),
        ("FluxReconciliationStalled", "Unknown"),
    ]:
        add(
            alert,
            [
                s(
                    "flux_resource_info",
                    BAD,
                    FL,
                    ready=state,
                    suspended="False",
                    instance="operator-a",
                ),
                s(
                    "flux_resource_info",
                    BAD,
                    FL,
                    ready=state,
                    suspended="False",
                    instance="operator-b",
                ),
            ],
            FLO,
            "operator info labels, duplicate targets",
        )
        quiet(
            alert,
            [s("flux_resource_info", "1x45", FL, ready=state, suspended="True")],
            "suspended resource",
        )
        quiet(
            alert,
            [
                s(
                    "flux_resource_info",
                    "0x4 1x5 0x40",
                    FL,
                    ready=state,
                    suspended="False",
                )
            ],
            "short reconcile",
        )
        add(
            alert,
            [
                s("flux_resource_info", "1x45", FL, ready=state, suspended="False"),
                s(
                    "flux_resource_info",
                    "1x45",
                    {**FL, "exported_namespace": "other"},
                    ready="True",
                    suspended="False",
                ),
                s(
                    "flux_resource_info",
                    "1x45",
                    {**FL, "cluster": "meruto"},
                    ready="True",
                    suspended="False",
                ),
            ],
            FLO,
            "same resource in other namespace and cluster",
            checks=[("24m", [FLO])],
        )
    # Entity joins never borrow capacity or health from a similarly named resource.
    for alert in ("KubePersistentVolumeSpaceLow", "KubePersistentVolumeSpaceCritical"):
        other_ns = {**V, "namespace": "other"}
        other_cluster = {**V, "cluster": "meruto"}
        series = volume + [
            s("kubelet_volume_stats_available_bytes", "3x45", V),
            s("kubelet_volume_stats_capacity_bytes", "100x45", V),
        ]
        for entity in (other_ns, other_cluster):
            series += [
                s(
                    "kube_persistentvolumeclaim_status_phase",
                    "1x45",
                    entity,
                    phase="Bound",
                ),
                s("kubelet_volume_stats_available_bytes", "99x45", entity),
                s("kubelet_volume_stats_capacity_bytes", "100x45", entity),
            ]
        add(
            alert,
            series,
            V,
            "same PVC name in different namespace and cluster",
            checks=[("24m", [V])],
        )
    quiet(
        "LonghornVolumeFaulted",
        [s("longhorn_volume_robustness", "0x45", L, state="faulted")],
        "healthy volume",
    )
    quiet(
        "LonghornVolumeFaulted", [], "missing volume metrics are not a healthy sample"
    )
    quiet("LonghornVolumeDegraded", [], "missing volume state")
    add(
        "FluxReconciliationFailed",
        [
            s("flux_resource_info", "1x45", FL, ready="False", suspended="False"),
            s(
                "flux_resource_info",
                "1x45",
                {**FL, "kind": "Kustomization"},
                ready="True",
                suspended="False",
            ),
        ],
        FLO,
        "same name in different kind",
        checks=[("24m", [FLO])],
    )
    return cases
