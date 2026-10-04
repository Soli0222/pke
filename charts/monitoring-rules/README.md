# monitoring-rules

![Version: 0.4.1](https://img.shields.io/badge/Version-0.4.1-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square)

PKE platform alert rules evaluated by Mimir

## CNPG

`databases` は CNPG の `Cluster` と `ScheduledBackup` から生成する。
通常の障害・再起動は DB 名を含まない共通式で評価し、欠測・archive・replication・backup 期限は `pke_cnpg_*` recording metrics と照合する。
base backup は Barman plugin の `barman_cloud_cloudnative_pg_io_last_available_backup_timestamp` を使う。
`CNPGBaseBackupStale` は DB ごとの期限超過、成功時刻0、または5分間の欠測が15分続くと通知する。
Misskey は30時間、週次の4DBは7日12時間を期限とする。初回未成功も検知する。
WAL archive の失敗・停滞は全DBで監視する。
停滞は同じ DB Pod に未送信 WAL（`cnpg_collector_pg_wal_archive_status{value="ready"}`）があり、最終成功から30分超の状態が15分続いた場合に通知する。
未送信 WAL のないアイドル状態では通知しない。成功時刻だけでは復元可能性を保証しない。
操作は [DB運用手順](../../CNPG.md) を参照する。

## Falco

検知 counter の増分・初回系列・起動直後の検知を Mimir で評価する。
priority 0–3 は critical、4–5 は warning として既存の ntfy 経路へ通知する。
Falco の欠測、カーネルイベントと出力イベントの破棄も監視する。
例外条件・ログの確認・監視の制約は [Falco 運用手順](../../MONITORING.md#falco) を参照する。

## 期待対象と収集の欠測

PKE の `hosts` / `databases` は `python3 scripts/sync-monitoring-inventory.py` で生成する。
Ansible の K3s inventory、`alloy_systemd_units`、`network_netplan` と CNPG manifest が正であり、HelmRelease の生成ブロックを直接編集しない。
CI は `--check` で生成漏れを検出する。
毎日の base backup は30時間、毎週は7日12時間を期限とする。
それ以外の schedule や期限を使う場合は `ScheduledBackup.metadata.annotations` の `monitoring.pke.soli0222.com/backup-max-age-seconds` に正の秒数を指定する。

期待対象は `pke.expectations` と `pke.expectations.<番号>` の recording rules で生成する。
`maxRulesPerGroup`（既定20）ごとに分割し、対象数が増えても Mimir のグループ上限を超えないようにする。exporter が消失しても期待値は残る。
`NodeExporterAbsent`、`MetricsStale`、`FalcoMetricsAbsent`、`HostSystemdUnitAbsent` はこの期待値と観測を照合する。
unit の failed / inactive は対象 unit 全体を共通式で評価する。
recording group 自体の欠落や評価失敗は Pipeline のルール同期・評価監視で確認する。

## Host

`HostNetworkErrors` は netplan に宣言した device を監視する。interface の命名規則には依存しない。
`HostDiskIOSaturation` は node exporter が公開する block device 全体を監視し、device mapper などの論理デバイスも含む。
同じ I/O が論理・物理デバイスの双方に現れる場合がある。busy time は IOPS 上限や並列処理の飽和そのものではないため、デバイス階層と latency を併せて確認する。
`HostSystemdUnitAbsent` は5分間の欠測がさらに5分続くと通知する。unit の削除・改名、collector の状態を確認する。

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| absenceWindow | string | `"5m"` |  |
| additionalAnnotations | object | `{}` |  |
| dashboardBaseURL | string | `"https://grafana.str08.net"` |  |
| databases | list | `[]` |  |
| exclusions.filesystems | string | `"tmpfs|devtmpfs|overlay|squashfs|nsfs|tracefs|proc|sysfs"` |  |
| exclusions.fluxResources | string | `"^$"` |  |
| exclusions.hosts | string | `"^$"` |  |
| exclusions.mountpoints | string | `"^/(dev|proc|sys|run)(/.*)?$|^/var/lib/(kubelet|rancher/k3s)/.*"` |  |
| exclusions.namespaces | string | `"^$"` |  |
| exclusions.pvcs | string | `"^$"` |  |
| exclusions.targetJobs | string | `"^$"` |  |
| exclusions.volumes | string | `"^$"` |  |
| fluxKinds | string | `"Kustomization|HelmRelease|GitRepository|HelmRepository|HelmChart|OCIRepository|Bucket"` |  |
| groups.certificates.enabled | bool | `true` |  |
| groups.certificates.rules.CertExpiringSoon.annotations | object | `{}` |  |
| groups.certificates.rules.CertExpiringSoon.days | int | `30` |  |
| groups.certificates.rules.CertExpiringSoon.enabled | bool | `true` |  |
| groups.certificates.rules.CertExpiringSoon.for | string | `"24h"` |  |
| groups.certificates.rules.CertExpiringSoon.severity | string | `"warning"` |  |
| groups.certificates.rules.CertExpiryCritical.annotations | object | `{}` |  |
| groups.certificates.rules.CertExpiryCritical.days | int | `7` |  |
| groups.certificates.rules.CertExpiryCritical.enabled | bool | `true` |  |
| groups.certificates.rules.CertExpiryCritical.for | string | `"1h"` |  |
| groups.certificates.rules.CertExpiryCritical.severity | string | `"critical"` |  |
| groups.certificates.rules.CertManagerCertNotReady.annotations | object | `{}` |  |
| groups.certificates.rules.CertManagerCertNotReady.enabled | bool | `true` |  |
| groups.certificates.rules.CertManagerCertNotReady.for | string | `"15m"` |  |
| groups.certificates.rules.CertManagerCertNotReady.severity | string | `"warning"` |  |
| groups.cnpg.enabled | bool | `true` |  |
| groups.cnpg.rules.CNPGCollectorDown.annotations | object | `{}` |  |
| groups.cnpg.rules.CNPGCollectorDown.enabled | bool | `true` |  |
| groups.cnpg.rules.CNPGCollectorDown.for | string | `"5m"` |  |
| groups.cnpg.rules.CNPGCollectorDown.severity | string | `"critical"` |  |
| groups.cnpg.rules.CNPGBaseBackupStale.annotations | object | `{}` |  |
| groups.cnpg.rules.CNPGBaseBackupStale.enabled | bool | `true` |  |
| groups.cnpg.rules.CNPGBaseBackupStale.for | string | `"15m"` |  |
| groups.cnpg.rules.CNPGBaseBackupStale.severity | string | `"warning"` |  |
| groups.cnpg.rules.CNPGMetricsAbsent.annotations | object | `{}` |  |
| groups.cnpg.rules.CNPGMetricsAbsent.enabled | bool | `true` |  |
| groups.cnpg.rules.CNPGMetricsAbsent.for | string | `"5m"` |  |
| groups.cnpg.rules.CNPGMetricsAbsent.severity | string | `"critical"` |  |
| groups.cnpg.rules.CNPGPostmasterRestarted.annotations | object | `{}` |  |
| groups.cnpg.rules.CNPGPostmasterRestarted.enabled | bool | `true` |  |
| groups.cnpg.rules.CNPGPostmasterRestarted.for | string | `"0m"` |  |
| groups.cnpg.rules.CNPGPostmasterRestarted.severity | string | `"warning"` |  |
| groups.cnpg.rules.CNPGReplicationLag.annotations | object | `{}` |  |
| groups.cnpg.rules.CNPGReplicationLag.enabled | bool | `true` |  |
| groups.cnpg.rules.CNPGReplicationLag.for | string | `"10m"` |  |
| groups.cnpg.rules.CNPGReplicationLag.severity | string | `"warning"` |  |
| groups.cnpg.rules.CNPGReplicationLag.threshold | int | `30` |  |
| groups.cnpg.rules.CNPGWALArchiveFailing.annotations | object | `{}` |  |
| groups.cnpg.rules.CNPGWALArchiveFailing.enabled | bool | `true` |  |
| groups.cnpg.rules.CNPGWALArchiveFailing.for | string | `"15m"` |  |
| groups.cnpg.rules.CNPGWALArchiveFailing.severity | string | `"warning"` |  |
| groups.cnpg.rules.CNPGWALArchiveStalled.annotations | object | `{}` |  |
| groups.cnpg.rules.CNPGWALArchiveStalled.enabled | bool | `true` |  |
| groups.cnpg.rules.CNPGWALArchiveStalled.for | string | `"15m"` |  |
| groups.cnpg.rules.CNPGWALArchiveStalled.severity | string | `"warning"` |  |
| groups.cnpg.rules.CNPGWALArchiveStalled.threshold | int | `1800` |  |
| groups.collection.enabled | bool | `true` |  |
| groups.collection.rules.AlloyRemoteWriteBacklog.annotations | object | `{}` |  |
| groups.collection.rules.AlloyRemoteWriteBacklog.enabled | bool | `true` |  |
| groups.collection.rules.AlloyRemoteWriteBacklog.for | string | `"5m"` |  |
| groups.collection.rules.AlloyRemoteWriteBacklog.severity | string | `"warning"` |  |
| groups.collection.rules.AlloyRemoteWriteBacklog.threshold | int | `10000` |  |
| groups.collection.rules.AlloyRemoteWriteFailing.annotations | object | `{}` |  |
| groups.collection.rules.AlloyRemoteWriteFailing.enabled | bool | `true` |  |
| groups.collection.rules.AlloyRemoteWriteFailing.for | string | `"5m"` |  |
| groups.collection.rules.AlloyRemoteWriteFailing.severity | string | `"warning"` |  |
| groups.collection.rules.AlloyRemoteWriteFailing.threshold | int | `0` |  |
| groups.collection.rules.AlloyRemoteWriteRetrying.annotations | object | `{}` |  |
| groups.collection.rules.AlloyRemoteWriteRetrying.enabled | bool | `true` |  |
| groups.collection.rules.AlloyRemoteWriteRetrying.for | string | `"5m"` |  |
| groups.collection.rules.AlloyRemoteWriteRetrying.severity | string | `"warning"` |  |
| groups.collection.rules.AlloyRemoteWriteRetrying.threshold | int | `1` |  |
| groups.collection.rules.AlloyRemoteWriteStalled.annotations | object | `{}` |  |
| groups.collection.rules.AlloyRemoteWriteStalled.enabled | bool | `true` |  |
| groups.collection.rules.AlloyRemoteWriteStalled.for | string | `"5m"` |  |
| groups.collection.rules.AlloyRemoteWriteStalled.severity | string | `"warning"` |  |
| groups.collection.rules.AlloyRemoteWriteStalled.threshold | int | `300` |  |
| groups.collection.rules.KubeStateMetricsAbsent.annotations | object | `{}` |  |
| groups.collection.rules.KubeStateMetricsAbsent.enabled | bool | `true` |  |
| groups.collection.rules.KubeStateMetricsAbsent.for | string | `"5m"` |  |
| groups.collection.rules.KubeStateMetricsAbsent.severity | string | `"critical"` |  |
| groups.collection.rules.MetricsStale.annotations | object | `{}` |  |
| groups.collection.rules.MetricsStale.enabled | bool | `true` |  |
| groups.collection.rules.MetricsStale.for | string | `"5m"` |  |
| groups.collection.rules.MetricsStale.severity | string | `"critical"` |  |
| groups.collection.rules.NodeExporterAbsent.annotations | object | `{}` |  |
| groups.collection.rules.NodeExporterAbsent.enabled | bool | `true` |  |
| groups.collection.rules.NodeExporterAbsent.for | string | `"5m"` |  |
| groups.collection.rules.NodeExporterAbsent.severity | string | `"critical"` |  |
| groups.collection.rules.TargetDown.annotations | object | `{}` |  |
| groups.collection.rules.TargetDown.enabled | bool | `true` |  |
| groups.collection.rules.TargetDown.for | string | `"5m"` |  |
| groups.collection.rules.TargetDown.severity | string | `"critical"` |  |
| groups.etcd.enabled | bool | `true` |  |
| groups.etcd.rules.EtcdDbSizeExceedingQuota.annotations | object | `{}` |  |
| groups.etcd.rules.EtcdDbSizeExceedingQuota.enabled | bool | `true` |  |
| groups.etcd.rules.EtcdDbSizeExceedingQuota.for | string | `"15m"` |  |
| groups.etcd.rules.EtcdDbSizeExceedingQuota.severity | string | `"warning"` |  |
| groups.etcd.rules.EtcdDbSizeExceedingQuota.threshold | float | `0.8` |  |
| groups.etcd.rules.EtcdHighFailedProposals.annotations | object | `{}` |  |
| groups.etcd.rules.EtcdHighFailedProposals.enabled | bool | `true` |  |
| groups.etcd.rules.EtcdHighFailedProposals.for | string | `"5m"` |  |
| groups.etcd.rules.EtcdHighFailedProposals.severity | string | `"warning"` |  |
| groups.etcd.rules.EtcdHighFailedProposals.threshold | int | `5` |  |
| groups.etcd.rules.EtcdHighFsyncDuration.annotations | object | `{}` |  |
| groups.etcd.rules.EtcdHighFsyncDuration.enabled | bool | `true` |  |
| groups.etcd.rules.EtcdHighFsyncDuration.for | string | `"10m"` |  |
| groups.etcd.rules.EtcdHighFsyncDuration.minimumObservations | int | `20` |  |
| groups.etcd.rules.EtcdHighFsyncDuration.severity | string | `"warning"` |  |
| groups.etcd.rules.EtcdHighFsyncDuration.threshold | float | `0.5` |  |
| groups.etcd.rules.EtcdHighLeaderChanges.annotations | object | `{}` |  |
| groups.etcd.rules.EtcdHighLeaderChanges.enabled | bool | `true` |  |
| groups.etcd.rules.EtcdHighLeaderChanges.for | string | `"5m"` |  |
| groups.etcd.rules.EtcdHighLeaderChanges.severity | string | `"warning"` |  |
| groups.etcd.rules.EtcdHighLeaderChanges.threshold | int | `3` |  |
| groups.etcd.rules.EtcdNoLeader.annotations | object | `{}` |  |
| groups.etcd.rules.EtcdNoLeader.enabled | bool | `true` |  |
| groups.etcd.rules.EtcdNoLeader.for | string | `"1m"` |  |
| groups.etcd.rules.EtcdNoLeader.severity | string | `"critical"` |  |
| groups.falco.enabled | bool | `true` |  |
| groups.falco.rules.FalcoKernelEventsDropped.annotations | object | `{}` |  |
| groups.falco.rules.FalcoKernelEventsDropped.enabled | bool | `true` |  |
| groups.falco.rules.FalcoKernelEventsDropped.for | string | `"0m"` |  |
| groups.falco.rules.FalcoKernelEventsDropped.severity | string | `"warning"` |  |
| groups.falco.rules.FalcoMetricsAbsent.annotations | object | `{}` |  |
| groups.falco.rules.FalcoMetricsAbsent.enabled | bool | `true` |  |
| groups.falco.rules.FalcoMetricsAbsent.for | string | `"5m"` |  |
| groups.falco.rules.FalcoMetricsAbsent.severity | string | `"critical"` |  |
| groups.falco.rules.FalcoOutputEventsDropped.annotations | object | `{}` |  |
| groups.falco.rules.FalcoOutputEventsDropped.enabled | bool | `true` |  |
| groups.falco.rules.FalcoOutputEventsDropped.for | string | `"0m"` |  |
| groups.falco.rules.FalcoOutputEventsDropped.severity | string | `"warning"` |  |
| groups.falco.rules.FalcoSecurityCritical.annotations | object | `{}` |  |
| groups.falco.rules.FalcoSecurityCritical.enabled | bool | `true` |  |
| groups.falco.rules.FalcoSecurityCritical.for | string | `"0m"` |  |
| groups.falco.rules.FalcoSecurityCritical.severity | string | `"critical"` |  |
| groups.falco.rules.FalcoSecurityWarning.annotations | object | `{}` |  |
| groups.falco.rules.FalcoSecurityWarning.enabled | bool | `true` |  |
| groups.falco.rules.FalcoSecurityWarning.for | string | `"0m"` |  |
| groups.falco.rules.FalcoSecurityWarning.severity | string | `"warning"` |  |
| groups.flux.enabled | bool | `true` |  |
| groups.flux.rules.FluxReconciliationFailed.annotations | object | `{}` |  |
| groups.flux.rules.FluxReconciliationFailed.enabled | bool | `true` |  |
| groups.flux.rules.FluxReconciliationFailed.for | string | `"15m"` |  |
| groups.flux.rules.FluxReconciliationFailed.severity | string | `"warning"` |  |
| groups.flux.rules.FluxReconciliationStalled.annotations | object | `{}` |  |
| groups.flux.rules.FluxReconciliationStalled.enabled | bool | `true` |  |
| groups.flux.rules.FluxReconciliationStalled.for | string | `"15m"` |  |
| groups.flux.rules.FluxReconciliationStalled.severity | string | `"warning"` |  |
| groups.host.enabled | bool | `true` |  |
| groups.host.rules.HostCPUHigh.annotations | object | `{}` |  |
| groups.host.rules.HostCPUHigh.enabled | bool | `true` |  |
| groups.host.rules.HostCPUHigh.for | string | `"15m"` |  |
| groups.host.rules.HostCPUHigh.severity | string | `"warning"` |  |
| groups.host.rules.HostCPUHigh.threshold | int | `90` |  |
| groups.host.rules.HostCPUStealHigh.annotations | object | `{}` |  |
| groups.host.rules.HostCPUStealHigh.enabled | bool | `true` |  |
| groups.host.rules.HostCPUStealHigh.for | string | `"15m"` |  |
| groups.host.rules.HostCPUStealHigh.severity | string | `"warning"` |  |
| groups.host.rules.HostCPUStealHigh.threshold | int | `10` |  |
| groups.host.rules.HostClockUnsynchronized.annotations | object | `{}` |  |
| groups.host.rules.HostClockUnsynchronized.enabled | bool | `true` |  |
| groups.host.rules.HostClockUnsynchronized.for | string | `"15m"` |  |
| groups.host.rules.HostClockUnsynchronized.severity | string | `"warning"` |  |
| groups.host.rules.HostConntrackHigh.annotations | object | `{}` |  |
| groups.host.rules.HostConntrackHigh.enabled | bool | `true` |  |
| groups.host.rules.HostConntrackHigh.for | string | `"15m"` |  |
| groups.host.rules.HostConntrackHigh.severity | string | `"warning"` |  |
| groups.host.rules.HostConntrackHigh.threshold | float | `0.8` |  |
| groups.host.rules.HostDiskIOSaturation.annotations | object | `{}` |  |
| groups.host.rules.HostDiskIOSaturation.enabled | bool | `true` |  |
| groups.host.rules.HostDiskIOSaturation.for | string | `"15m"` |  |
| groups.host.rules.HostDiskIOSaturation.severity | string | `"warning"` |  |
| groups.host.rules.HostDiskIOSaturation.threshold | float | `0.9` |  |
| groups.host.rules.HostFilesystemFillingUp.annotations | object | `{}` |  |
| groups.host.rules.HostFilesystemFillingUp.enabled | bool | `true` |  |
| groups.host.rules.HostFilesystemFillingUp.for | string | `"30m"` |  |
| groups.host.rules.HostFilesystemFillingUp.horizonSeconds | int | `86400` |  |
| groups.host.rules.HostFilesystemFillingUp.minimumSamples | int | `300` |  |
| groups.host.rules.HostFilesystemFillingUp.severity | string | `"warning"` |  |
| groups.host.rules.HostFilesystemFillingUp.window | string | `"6h"` |  |
| groups.host.rules.HostFilesystemSpaceCritical.annotations | object | `{}` |  |
| groups.host.rules.HostFilesystemSpaceCritical.enabled | bool | `true` |  |
| groups.host.rules.HostFilesystemSpaceCritical.for | string | `"15m"` |  |
| groups.host.rules.HostFilesystemSpaceCritical.severity | string | `"critical"` |  |
| groups.host.rules.HostFilesystemSpaceCritical.threshold | float | `0.05` |  |
| groups.host.rules.HostFilesystemSpaceLow.annotations | object | `{}` |  |
| groups.host.rules.HostFilesystemSpaceLow.enabled | bool | `true` |  |
| groups.host.rules.HostFilesystemSpaceLow.for | string | `"15m"` |  |
| groups.host.rules.HostFilesystemSpaceLow.severity | string | `"warning"` |  |
| groups.host.rules.HostFilesystemSpaceLow.threshold | float | `0.1` |  |
| groups.host.rules.HostInodesLow.annotations | object | `{}` |  |
| groups.host.rules.HostInodesLow.enabled | bool | `true` |  |
| groups.host.rules.HostInodesLow.for | string | `"15m"` |  |
| groups.host.rules.HostInodesLow.severity | string | `"warning"` |  |
| groups.host.rules.HostInodesLow.threshold | float | `0.1` |  |
| groups.host.rules.HostLoadHigh.annotations | object | `{}` |  |
| groups.host.rules.HostLoadHigh.enabled | bool | `true` |  |
| groups.host.rules.HostLoadHigh.for | string | `"15m"` |  |
| groups.host.rules.HostLoadHigh.severity | string | `"warning"` |  |
| groups.host.rules.HostLoadHigh.threshold | int | `2` |  |
| groups.host.rules.HostMemoryLow.annotations | object | `{}` |  |
| groups.host.rules.HostMemoryLow.enabled | bool | `true` |  |
| groups.host.rules.HostMemoryLow.for | string | `"15m"` |  |
| groups.host.rules.HostMemoryLow.severity | string | `"warning"` |  |
| groups.host.rules.HostMemoryLow.threshold | float | `0.1` |  |
| groups.host.rules.HostNetworkErrors.annotations | object | `{}` |  |
| groups.host.rules.HostNetworkErrors.enabled | bool | `true` |  |
| groups.host.rules.HostNetworkErrors.for | string | `"15m"` |  |
| groups.host.rules.HostNetworkErrors.severity | string | `"warning"` |  |
| groups.host.rules.HostNetworkErrors.threshold | int | `1` |  |
| groups.host.rules.HostOOMKill.annotations | object | `{}` |  |
| groups.host.rules.HostOOMKill.enabled | bool | `true` |  |
| groups.host.rules.HostOOMKill.for | string | `"0m"` |  |
| groups.host.rules.HostOOMKill.severity | string | `"warning"` |  |
| groups.host.rules.HostOOMKill.threshold | int | `0` |  |
| groups.host.rules.HostRebooted.annotations | object | `{}` |  |
| groups.host.rules.HostRebooted.enabled | bool | `true` |  |
| groups.host.rules.HostRebooted.for | string | `"0m"` |  |
| groups.host.rules.HostRebooted.severity | string | `"warning"` |  |
| groups.host.rules.HostSystemdUnitAbsent.enabled | bool | `true` | 期待 unit の欠測 |
| groups.host.rules.HostSystemdUnitAbsent.for | string | `"5m"` | |
| groups.host.rules.HostSystemdUnitAbsent.severity | string | `"warning"` | |
| groups.host.rules.HostSystemdUnitAbsent.annotations | object | `{}` | |
| groups.host.rules.HostSystemdUnitFailed.annotations | object | `{}` |  |
| groups.host.rules.HostSystemdUnitFailed.enabled | bool | `true` |  |
| groups.host.rules.HostSystemdUnitFailed.for | string | `"5m"` |  |
| groups.host.rules.HostSystemdUnitFailed.severity | string | `"critical"` |  |
| groups.host.rules.HostSystemdUnitInactive.annotations | object | `{}` |  |
| groups.host.rules.HostSystemdUnitInactive.enabled | bool | `true` |  |
| groups.host.rules.HostSystemdUnitInactive.for | string | `"5m"` |  |
| groups.host.rules.HostSystemdUnitInactive.severity | string | `"warning"` |  |
| groups.kubernetes.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubeNodeDiskPressure.annotations | object | `{}` |  |
| groups.kubernetes.rules.KubeNodeDiskPressure.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubeNodeDiskPressure.for | string | `"10m"` |  |
| groups.kubernetes.rules.KubeNodeDiskPressure.severity | string | `"warning"` |  |
| groups.kubernetes.rules.KubeNodeMemoryPressure.annotations | object | `{}` |  |
| groups.kubernetes.rules.KubeNodeMemoryPressure.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubeNodeMemoryPressure.for | string | `"10m"` |  |
| groups.kubernetes.rules.KubeNodeMemoryPressure.severity | string | `"warning"` |  |
| groups.kubernetes.rules.KubeNodeNotReady.annotations | object | `{}` |  |
| groups.kubernetes.rules.KubeNodeNotReady.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubeNodeNotReady.for | string | `"10m"` |  |
| groups.kubernetes.rules.KubeNodeNotReady.severity | string | `"critical"` |  |
| groups.kubernetes.rules.KubePersistentVolumeFillingUp.annotations | object | `{}` |  |
| groups.kubernetes.rules.KubePersistentVolumeFillingUp.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubePersistentVolumeFillingUp.for | string | `"30m"` |  |
| groups.kubernetes.rules.KubePersistentVolumeFillingUp.horizonSeconds | int | `14400` |  |
| groups.kubernetes.rules.KubePersistentVolumeFillingUp.minimumSamples | int | `300` |  |
| groups.kubernetes.rules.KubePersistentVolumeFillingUp.severity | string | `"warning"` |  |
| groups.kubernetes.rules.KubePersistentVolumeFillingUp.window | string | `"6h"` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceCritical.annotations | object | `{}` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceCritical.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceCritical.for | string | `"15m"` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceCritical.severity | string | `"critical"` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceCritical.threshold | float | `0.05` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceLow.annotations | object | `{}` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceLow.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceLow.for | string | `"15m"` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceLow.severity | string | `"warning"` |  |
| groups.kubernetes.rules.KubePersistentVolumeSpaceLow.threshold | float | `0.1` |  |
| groups.kubernetes.rules.KubePersistentVolumeStatsInvalid.annotations | object | `{}` |  |
| groups.kubernetes.rules.KubePersistentVolumeStatsInvalid.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubePersistentVolumeStatsInvalid.for | string | `"30m"` |  |
| groups.kubernetes.rules.KubePersistentVolumeStatsInvalid.severity | string | `"warning"` |  |
| groups.kubernetes.rules.KubePersistentVolumeStatsMissing.annotations | object | `{}` |  |
| groups.kubernetes.rules.KubePersistentVolumeStatsMissing.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubePersistentVolumeStatsMissing.for | string | `"30m"` |  |
| groups.kubernetes.rules.KubePersistentVolumeStatsMissing.severity | string | `"warning"` |  |
| groups.kubernetes.rules.KubeletTooManyPods.annotations | object | `{}` |  |
| groups.kubernetes.rules.KubeletTooManyPods.enabled | bool | `true` |  |
| groups.kubernetes.rules.KubeletTooManyPods.for | string | `"5m"` |  |
| groups.kubernetes.rules.KubeletTooManyPods.severity | string | `"warning"` |  |
| groups.kubernetes.rules.KubeletTooManyPods.threshold | float | `0.9` |  |
| groups.longhorn.enabled | bool | `true` |  |
| groups.longhorn.rules.LonghornDiskSpaceCritical.annotations | object | `{}` |  |
| groups.longhorn.rules.LonghornDiskSpaceCritical.enabled | bool | `true` |  |
| groups.longhorn.rules.LonghornDiskSpaceCritical.for | string | `"15m"` |  |
| groups.longhorn.rules.LonghornDiskSpaceCritical.severity | string | `"critical"` |  |
| groups.longhorn.rules.LonghornDiskSpaceCritical.threshold | float | `0.05` |  |
| groups.longhorn.rules.LonghornDiskSpaceLow.annotations | object | `{}` |  |
| groups.longhorn.rules.LonghornDiskSpaceLow.enabled | bool | `true` |  |
| groups.longhorn.rules.LonghornDiskSpaceLow.for | string | `"15m"` |  |
| groups.longhorn.rules.LonghornDiskSpaceLow.severity | string | `"warning"` |  |
| groups.longhorn.rules.LonghornDiskSpaceLow.threshold | float | `0.1` |  |
| groups.longhorn.rules.LonghornNodeSpaceCritical.annotations | object | `{}` |  |
| groups.longhorn.rules.LonghornNodeSpaceCritical.enabled | bool | `true` |  |
| groups.longhorn.rules.LonghornNodeSpaceCritical.for | string | `"15m"` |  |
| groups.longhorn.rules.LonghornNodeSpaceCritical.severity | string | `"critical"` |  |
| groups.longhorn.rules.LonghornNodeSpaceCritical.threshold | float | `0.05` |  |
| groups.longhorn.rules.LonghornNodeSpaceLow.annotations | object | `{}` |  |
| groups.longhorn.rules.LonghornNodeSpaceLow.enabled | bool | `true` |  |
| groups.longhorn.rules.LonghornNodeSpaceLow.for | string | `"15m"` |  |
| groups.longhorn.rules.LonghornNodeSpaceLow.severity | string | `"warning"` |  |
| groups.longhorn.rules.LonghornNodeSpaceLow.threshold | float | `0.1` |  |
| groups.longhorn.rules.LonghornVolumeDegraded.annotations | object | `{}` |  |
| groups.longhorn.rules.LonghornVolumeDegraded.enabled | bool | `true` |  |
| groups.longhorn.rules.LonghornVolumeDegraded.for | string | `"15m"` |  |
| groups.longhorn.rules.LonghornVolumeDegraded.severity | string | `"warning"` |  |
| groups.longhorn.rules.LonghornVolumeFaulted.annotations | object | `{}` |  |
| groups.longhorn.rules.LonghornVolumeFaulted.enabled | bool | `true` |  |
| groups.longhorn.rules.LonghornVolumeFaulted.for | string | `"5m"` |  |
| groups.longhorn.rules.LonghornVolumeFaulted.severity | string | `"critical"` |  |
| groups.workload.enabled | bool | `true` |  |
| groups.workload.rules.KubeContainerOOMKilled.annotations | object | `{}` |  |
| groups.workload.rules.KubeContainerOOMKilled.enabled | bool | `true` |  |
| groups.workload.rules.KubeContainerOOMKilled.for | string | `"0m"` |  |
| groups.workload.rules.KubeContainerOOMKilled.recentSeconds | int | `600` |  |
| groups.workload.rules.KubeContainerOOMKilled.severity | string | `"warning"` |  |
| groups.workload.rules.KubeContainerWaiting.annotations | object | `{}` |  |
| groups.workload.rules.KubeContainerWaiting.enabled | bool | `true` |  |
| groups.workload.rules.KubeContainerWaiting.for | string | `"15m"` |  |
| groups.workload.rules.KubeContainerWaiting.reasons | string | `"ImagePullBackOff|ErrImagePull|CreateContainerConfigError|CreateContainerError|RunContainerError|InvalidImageName|ContainerCreating"` |  |
| groups.workload.rules.KubeContainerWaiting.severity | string | `"warning"` |  |
| groups.workload.rules.KubeDaemonSetNotScheduled.annotations | object | `{}` |  |
| groups.workload.rules.KubeDaemonSetNotScheduled.enabled | bool | `true` |  |
| groups.workload.rules.KubeDaemonSetNotScheduled.for | string | `"15m"` |  |
| groups.workload.rules.KubeDaemonSetNotScheduled.severity | string | `"warning"` |  |
| groups.workload.rules.KubeDaemonSetNotScheduled.threshold | int | `0` |  |
| groups.workload.rules.KubeDeploymentReplicasMismatch.annotations | object | `{}` |  |
| groups.workload.rules.KubeDeploymentReplicasMismatch.enabled | bool | `true` |  |
| groups.workload.rules.KubeDeploymentReplicasMismatch.for | string | `"15m"` |  |
| groups.workload.rules.KubeDeploymentReplicasMismatch.severity | string | `"warning"` |  |
| groups.workload.rules.KubeDeploymentReplicasMismatch.threshold | int | `0` |  |
| groups.workload.rules.KubeJobFailed.annotations | object | `{}` |  |
| groups.workload.rules.KubeJobFailed.enabled | bool | `true` |  |
| groups.workload.rules.KubeJobFailed.for | string | `"5m"` |  |
| groups.workload.rules.KubeJobFailed.recentSeconds | int | `86400` |  |
| groups.workload.rules.KubeJobFailed.severity | string | `"warning"` |  |
| groups.workload.rules.KubePodCrashLooping.annotations | object | `{}` |  |
| groups.workload.rules.KubePodCrashLooping.enabled | bool | `true` |  |
| groups.workload.rules.KubePodCrashLooping.for | string | `"15m"` |  |
| groups.workload.rules.KubePodCrashLooping.severity | string | `"warning"` |  |
| groups.workload.rules.KubePodNotReady.annotations | object | `{}` |  |
| groups.workload.rules.KubePodNotReady.enabled | bool | `true` |  |
| groups.workload.rules.KubePodNotReady.for | string | `"15m"` |  |
| groups.workload.rules.KubePodNotReady.severity | string | `"warning"` |  |
| groups.workload.rules.KubeStatefulSetReplicasMismatch.annotations | object | `{}` |  |
| groups.workload.rules.KubeStatefulSetReplicasMismatch.enabled | bool | `true` |  |
| groups.workload.rules.KubeStatefulSetReplicasMismatch.for | string | `"15m"` |  |
| groups.workload.rules.KubeStatefulSetReplicasMismatch.severity | string | `"warning"` |  |
| groups.workload.rules.KubeStatefulSetReplicasMismatch.threshold | int | `0` |  |
| hosts | list | `[]` | 生成された name / units / networkDevices |
| maxRulesPerGroup | int | `20` | 期待値グループの分割サイズ。Mimir の上限以下に設定 |
| interval | string | `"1m"` |  |
| runbookBaseURL | string | `"https://github.com/Soli0222/pke/blob/main/charts/monitoring-rules/README.md"` |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
