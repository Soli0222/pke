# PKE monitoring rules

両クラスタの汎用 PrometheusRule を管理する chart。
`rules/<group>.yaml` が式と説明、`values.yaml` が有効化・閾値・待機時間の正となる。
natsume に64ルール、meruto に CNPG を除く57ルールを配置する。
アプリ固有ルールと blackbox の既存ルールは移動しない。

## 設定と追加方法

```yaml
hosts:
  - name: node-a
    units: [alloy.service, k3s.service, etcd.service]
databases:
  - namespace: app
    name: db
    archive: true
    replication: false
    dumpCronJob: db-pg-dump
additionalAnnotations:
  owner: platform
groups:
  host:
    enabled: true
    rules:
      HostMemoryLow:
        enabled: true
        threshold: 0.15
        for: 20m
        severity: warning
        annotations:
          description: 利用状況と直近の変更を確認する
```

- `groups.<group>.enabled` と `groups.<group>.rules.<alert>.enabled` で停止する。未設定の項目は chart の既定値を継承する。
- 各ルールの `for`、`severity`、`annotations` を変更できる。数値判定は `threshold`、証明書は `days`、予測は `window` / `horizonSeconds` / `minimumSamples` を使う。
- `exclusions` は Prometheus の完全一致正規表現。既定の `^$` は名前が空の対象だけを除く。namespace / host / target job / PVC / Longhorn volume / Flux resource と filesystem / mountpoint を指定できる。PVC 名など同名対象への除外はクラスタ全体に効くため、広すぎる式を避ける。
- `diskDevices` / `networkDevices` は対象の allowlist。OS の仮想 interface と device mapper の重複を除く。
- `hosts` は K3s inventory と host_vars の `alloy_systemd_units` に一致させる。DB は CNPG Cluster manifest に一致させ、WAL と replica は opt-in とする。
- 新規グループは `rules/` と `templates/` に各1ファイル、`values.yaml` に設定、`tests/scenarios.py` に正常・異常・復帰と境界条件を追加する。chart version も上げる。

式に Kubernetes のクラスタ名を直書きしない。
Alloy v1.19.2 が `extra_query_matchers` で入力に `cluster="<cluster>"` を加え、`external_labels` で出力にも同じラベルを付ける。
DB 名は `cnpg_cluster`、Kubernetes クラスタ名は `cluster` とし、aggregation / join に `cluster` を残す。
meruto の欠測も `meruto/` prefix に保存されたルールを natsume の Ruler が評価する。
meruto の Alloy が停止しても既に同期されたルールの評価は続く。

出力は `severity`、`alert_family` と対象識別子を持つ。
Pod は namespace / pod / uid / container、PVC は namespace / persistentvolumeclaim、ホストは instance と mountpoint / device / name、DB は namespace / cnpg_cluster、Flux は kind / namespace / name を使う。
cert-manager と Flux Operator の `exported_namespace` は監視対象の namespace へコピーする。scrape 元 namespace と取り違えない。

## ダッシュボードへの調査リンク

各ルールは`dashboard_url`と`panel_url`をannotationに持つ。
`dashboardBaseURL`を共通の接続先とし、`dashboard-links.yaml`でgroupの既定画面・変数と、個別ルールのpanelを指定する。
namespaceは通知対象、Flux画面のnamespaceはexporterの`flux-system`を使い分ける。
clusterはAlloyが式に加えたラベルからRulerで展開し、対象識別子はURLエスケープする。
既存の`additionalAnnotations`やruleの`annotations`による上書きも使える。

ntfyは各alertのpanel URLを優先し、なければdashboard URLを表示する。Runbookも併記する。
通知のリンクは直近6時間を開く。通知先や判定閾値・待機時間には影響しない。
入口の [PKE / Overview](https://grafana.str08.net/d/pke-overview) と [ダッシュボード運用](../../grafana/README.md#overviewと調査リンク) も参照する。

## 検証

Python 3 + PyYAML、Helm 3、Docker、kubectl が必要。

```sh
python3 scripts/validate-monitoring-rules.py
python3 scripts/validate-ntfy-alerts.py
```

前者は Helm render、inventory 照合、無効化・override を検証した後、実 Alloy と偽 Kubernetes / Mimir API で両クラスタの式を変換する。
保存された標準 rule file に対して promtool check / test を実行する。
時系列 fixture の対象ラベルと期待する発火時刻は式から生成せずに定義し、`ALERTS` の実際の firing 状態を検証する。
両コマンドは本番 API / Secret を使わない。Docker image の取得とローカル通信だけを必要とする。

promtool は `prom/prometheus:v3.13.0` に固定する。
[Mimir 3.2.1 の go.mod](https://github.com/grafana/mimir/blob/mimir-3.2.1/go.mod) が参照する mimir-prometheus `e4534561e9ed` の [VERSION](https://github.com/grafana/mimir-prometheus/blob/e4534561e9ed/VERSION) に合わせた。
Mimir の fork と upstream が完全に同じとは仮定せず、投入前には実 Mimir への読み取り query でも構文・評価を確認する。

## collection

`TargetDown` は `up=0` の持続を検知する。discovery から対象自体が消えた場合は発火しない。
欠測には KSM の `kube_node_info{job="kube-state-metrics"}`、ホストごとの `node_uname_info`、Kubernetes / 各ホストの `alloy_build_info` を使う。
KSM の build_info は現在の8080 scrape にないため使わない。
欠測の既定待機は range 5分 + for 5分、さらに最大1評価間隔と配送の group_wait が加わる。
短い欠測から復帰すれば pending は解除される。

remote_write は failed / retried の増加、pending、highest_sent_timestamp を分けて見る。
Kubernetes では同じ self 指標が2経路に存在するため `job=alloy` に限定し、ホストは `job=alloy-host` を使う。
pending は shard 内の待機量で、WAL 全体の未送信量ではない。
送信経路が完全に止まると self 指標も届かないため、欠測ルールと合わせて調査する。
ホスト cAdvisor の `up` は既存 relabel で落ちる。TargetDown による cAdvisor 単独障害の検知は未カバー。

調査では `up{cluster="..."}`、Alloy の component health / scrape error、NetworkPolicy、remote endpoint の応答を確認する。
ホスト停止と collector / network 障害を区別し、欠測だけで Pod アラートを抑制しない。
Ruler / natsume 全断を自己監視だけで検出することはできない。外部経路は #750 で扱う。

## workload

CrashLoopBackOff と restart 回数を区別し、init container の CrashLoop も対象にする。
NotReady / waiting は Pending・Running・Unknown の Pod を対象とし、Succeeded / Failed と deletion_timestamp のある Pod を除く。
OOM は last_terminated_reason と終了時刻を組み合わせ、過去の OOM ラベルが残るだけでは継続発火させない。
Deployment / StatefulSet / DaemonSet は desired と available / ready / scheduled を比較し、15分未満の rollout を待つ。
KSM が重複収集されても resource key ごとに集約してから比較する。

Job は最近24時間の失敗を対象とする。
CronJob の後続実行が成功したら、残存する古い失敗 Job の警報は解消する。
失敗 Job を作らない未実行・停止はこのルールでは分からない。DB の pg_dump は CNPGDumpBackupStale で補う。
`kubectl --context <cluster>@soli -n <namespace> describe pod|job <name>` と events、container の previous logs、workload の desired / ready を照合する。

## kubernetes

Node Ready の true 系列が0なら Unknown も含めて NotReady とする。
MemoryPressure / DiskPressure は true 系列が1の場合だけ判定する。
Pod 収容率は kubelet_running_pods を実際の allocatable pods で割る。

PVC は Bound かつ ReadOnlyMany でないものを対象とする。
空き率10% / 5%、6時間の履歴から4時間先の枯渇を別々に検知する。
予測には300 sample 以上、空き率15%未満、正の容量、window 内に容量変更がないことを要求する。
初回導入は最低 sample 数の蓄積を待つ。30秒 scrape なら約2.5時間が必要で、6時間分揃うまで履歴は部分的となる。
増設後は容量変更が6時間の window から外れるまで予測を止める。

複数 kubelet の同一 PVC は available の最小、capacity の最大で1系列に集約する。
容量0は比率の判定から外し、StatsInvalid で通知する。
Running Pod が使用中の PVC に capacity 統計が30分なければ StatsMissing とする。
未使用・detached の PVC には kubelet 統計を期待しない。
統計非対応 driver や read-only mount を access mode だけで表せない場合は、理由を記録して対象を除外する。除外した容量は未監視であり、正常とは扱わない。

PVC と PV の対応、使用 Pod、kubelet volume stats、storage driver events を確認する。
PVC の空き容量、OS filesystem、Longhorn の replica 健全性は別の問題なので一括抑制しない。

## host

| 指標 | natsume-03 | natsume-08 | meruto-01 |
|---|---|---|---|
| memory / CPU / load / steal / filesystem / inode / disk I/O | 収集済み・有効 | 収集済み・有効 | 収集済み・有効 |
| node_vmstat_oom_kill / boot time / timex / conntrack / network errors | 収集済み・有効 | 収集済み・有効 | 収集済み・有効 |
| systemd / Alloy self | 4 unit・有効 | 3 unit・有効 | 4 unit・有効 |

2026-09-19 の実測。systemd と self の反映記録は MONITORING.md / #739 を参照する。
unit は inventory にある常駐サービスだけに限定し、元々存在しない unit を inactive とみなさない。
Alloy 自体の停止は欠測で検知する。D-Bus / collector 単独の欠測は state=failed ではないため `node_scrape_collector_success{collector="systemd"}` も調査する。

filesystem は空き率10% / 5%と24時間先予測を分ける。
予測は6時間 window、300 sample 以上、空き率20%未満、容量変更なし、read-only=0 が条件。
tmpfs・overlay・擬似 filesystem と kubelet / K3s 配下の重複 mount を除く。
I/O busy は `rate(node_disk_io_time_seconds_total[5m])` を whole disk にだけ適用する。仮想ディスクの並列 I/O や IOPS 上限そのものの飽和率ではない。

OOM / network / steal などの counter は増分で判定し、reset を障害にしない。
boot time の10分間の変化は計画再起動でも通知するが、初回観測だけでは発火しない。10分を超える欠測を挟んだ再起動は直接比較できない。
timex などが非対応の環境を追加するときは、未対応のまま正常扱いせず収集可否と設定を更新する。
`systemctl status <unit>`、journal、`df -h` / `df -i`、`lsblk`、NTP とホストの変更記録を照合する。

## etcd

job=etcd、instance=hostname。単一 member でも leader 不在は critical とする。
leader change は15分に3回超、proposal failed は5分に5件超で通知する。
DB size は実 quota と比較し、quota が0なら比率判定しない。
fsync は instance ごとに bucket rate を集計した p99 > 0.5秒を使い、5分に20観測以上を要求する。
histogram 欠測や低頻度を正常な低レイテンシと解釈しない。

Ansible の etcd 設定、etcd endpoint status / health と journal、disk I/O、maintenance timer を調べる。
この chart は member 変更・再起動・defrag を実行しない。

## certificates

cert-manager の name / exported_namespace を証明書のキーとする。
NotReady は condition=True の値が0で15分、期限は30日 warning（24時間継続）/ 7日 critical（1時間継続）。
通常の更新待ちは除外し、renewal_timestamp を過ぎたものを通知する。期限切れは renewal timestamp によらず対象とする。
renewal 指標が欠測した場合も、期限指標が残っていれば期限警報を隠さない。

barman-cloud の90日証明書は renewBefore=15日なので、残り30日だけでは通知しない。
Certificate status の renewalTime / notAfter を確認し、CertificateRequest → Order → Challenge、issuer credentials と DNS / HTTP 到達性を辿る。
blackbox の証明書期限は URL（instance）単位の別ルールであり、この証明書オブジェクトの name と結合しない。

## cnpg

DB は natsume の grafana / misskey / spotify-nowplaying / spotify-reblend / sui の5つ。
meruto は DB Cluster がなくグループを無効にする（#740）。
collector_up の0と5分欠測を分け、postmaster_start_time の変化で再起動を検知する。
現在すべて instances=1 なので replication lag は opt-in 無効。将来 replica を追加するときに manifest と values を同時に更新する。

WAL archive は外部 archiver が有効な misskey だけを対象とする。
last_failed_time > last_archived_time が15分続くと通知し、次の archive 成功で解消する。
最終 archive から30分経過も15分継続で通知する。現在の archive_timeout は300秒。
archive_timeout は WAL の変更がなければ定期成功を保証しないため、低トラフィック DB に広げる前に WAL 生成周期を再確認する。

4つの pg_dump CronJob は最終成功から30時間 + for 15分で通知し、作成後一度も成功していないケースも検知する。suspend は除く。
CronJob 自体の削除と R2 の内容・復元可能性はこの時刻判定では検知しない。
misskey の base backup freshness は**未カバー**。
実機の `cnpg_collector_last_available_backup_timestamp` は0で、Backup CR も存在せず、ScheduledBackup.lastScheduleTime だけでは成功を証明できない。
後続作業として plugin / Backup CR の生成・保持と R2 の最終成功を調査し、信頼できる時刻の収集方法を決める。WAL が成功していても base backup の成功とは記載しない。

CNPG.md の調査手順に従い、Cluster status、instance logs、archiver 時刻、Job / CronJob status と R2 の格納結果を照合する。

## longhorn

Longhorn v1.12.1 の `longhorn_volume_robustness{state="healthy|degraded|faulted|unknown"}` と `longhorn_volume_state{state="attached|..."}` は状態ごとの0/1 gauge。
数値 enum の3などと比較しない。
faulted は5分、degraded は attached のものが15分続いた場合に通知する。
一時的な rebuild を待ち、意図した detached の degraded は除く。faulted は detached でも失われたデータの可能性があるので対象とする。

meruto の2 volume は実際に1 replica。node 数から期待 replica 数を固定せず、Longhorn が設定済み replica 数から判定した robustness を使う。
volume に付く pvc / pvc_namespace を保持し、対応ラベルがなくても volume の警報を消さない。
容量は node / disk ごとに `(capacity - reservation - usage) / (capacity - reservation)` を計算し、10% / 5%で通知する。
分母0以下や指標欠測では容量比率を判定できない。TargetDown と Longhorn Node status を併せて確認する。

`kubectl --context <cluster>@soli -n longhorn-system get volumes.longhorn.io,nodes.longhorn.io` で robustness、attachment、disk capacity / reserved / scheduled と replica rebuild を照合する。
故障時の volume repair や replica 数の変更は自動実行しない。

## flux

現在の Flux Operator は `flux_resource_info` の値1と ready / suspended ラベルで状態を公開する。
`gotk_reconcile_condition` は存在しないため使わない。
ready=False / Unknown かつ suspended=False の状態を kind / exported_namespace / name で集約し、15分待つ。
Operator の状態収集は既定5分間隔なので検出までさらに約5分かかり得る。
Reconciling が長期化して Unknown のままなら Stalled となる。専用 Reconciling condition は現在の指標から直接判定できない。

resource 系列が消えたことを Ready と解釈しない。controller の up、Operator の scrape、Alloy の経路と実 resource の存在を調べる。
同時失敗した依存 resource は別 entity として通知する。namespace 単位の広い inhibition は行わない。
`kubectl --context <cluster>@soli -n <namespace> describe <kind> <name>` で条件と events を確認し、sourceRef の GitRepository / HelmRepository / HelmChart、dependsOn、controller logs の順に辿る。

## 初期設定一覧

すべて enabled=true。実クラスタの hosts / databases とグループ override に従って描画する。

| グループ | Alert | severity | for | 数値・window 設定 |
|---|---|---|---|---|
| collection | TargetDown | critical | 5m | 式の状態判定 |
| collection | KubeStateMetricsAbsent | critical | 5m | 式の状態判定 |
| collection | NodeExporterAbsent | critical | 5m | 式の状態判定 |
| collection | MetricsStale | critical | 5m | 式の状態判定 |
| collection | AlloyRemoteWriteFailing | warning | 5m | threshold=0 |
| collection | AlloyRemoteWriteRetrying | warning | 5m | threshold=1 |
| collection | AlloyRemoteWriteBacklog | warning | 5m | threshold=10000 |
| collection | AlloyRemoteWriteStalled | warning | 5m | threshold=300 |
| workload | KubePodCrashLooping | warning | 15m | 式の状態判定 |
| workload | KubePodNotReady | warning | 15m | 式の状態判定 |
| workload | KubeContainerWaiting | warning | 15m | 式の状態判定 |
| workload | KubeContainerOOMKilled | warning | 0m | recentSeconds=600 |
| workload | KubeDeploymentReplicasMismatch | warning | 15m | threshold=0 |
| workload | KubeStatefulSetReplicasMismatch | warning | 15m | threshold=0 |
| workload | KubeDaemonSetNotScheduled | warning | 15m | threshold=0 |
| workload | KubeJobFailed | warning | 5m | recentSeconds=86400 |
| kubernetes | KubeNodeNotReady | critical | 10m | 式の状態判定 |
| kubernetes | KubeNodeMemoryPressure | warning | 10m | 式の状態判定 |
| kubernetes | KubeNodeDiskPressure | warning | 10m | 式の状態判定 |
| kubernetes | KubeletTooManyPods | warning | 5m | threshold=0.9 |
| kubernetes | KubePersistentVolumeSpaceLow | warning | 15m | threshold=0.1 |
| kubernetes | KubePersistentVolumeSpaceCritical | critical | 15m | threshold=0.05 |
| kubernetes | KubePersistentVolumeStatsMissing | warning | 30m | 式の状態判定 |
| kubernetes | KubePersistentVolumeStatsInvalid | warning | 30m | 式の状態判定 |
| kubernetes | KubePersistentVolumeFillingUp | warning | 30m | window=6h, horizonSeconds=14400, minimumSamples=300 |
| host | HostMemoryLow | warning | 15m | threshold=0.1 |
| host | HostCPUHigh | warning | 15m | threshold=90 |
| host | HostLoadHigh | warning | 15m | threshold=2 |
| host | HostCPUStealHigh | warning | 15m | threshold=10 |
| host | HostConntrackHigh | warning | 15m | threshold=0.8 |
| host | HostFilesystemSpaceLow | warning | 15m | threshold=0.1 |
| host | HostFilesystemSpaceCritical | critical | 15m | threshold=0.05 |
| host | HostFilesystemFillingUp | warning | 30m | window=6h, horizonSeconds=86400, minimumSamples=300 |
| host | HostInodesLow | warning | 15m | threshold=0.1 |
| host | HostDiskIOSaturation | warning | 15m | threshold=0.9 |
| host | HostOOMKill | warning | 0m | threshold=0 |
| host | HostRebooted | warning | 0m | 式の状態判定 |
| host | HostClockUnsynchronized | warning | 15m | 式の状態判定 |
| host | HostNetworkErrors | warning | 15m | threshold=1 |
| host | HostSystemdUnitFailed | critical | 5m | 式の状態判定 |
| host | HostSystemdUnitInactive | warning | 5m | 式の状態判定 |
| etcd | EtcdNoLeader | critical | 1m | 式の状態判定 |
| etcd | EtcdHighLeaderChanges | warning | 5m | threshold=3 |
| etcd | EtcdHighFsyncDuration | warning | 10m | threshold=0.5, minimumObservations=20 |
| etcd | EtcdDbSizeExceedingQuota | warning | 15m | threshold=0.8 |
| etcd | EtcdHighFailedProposals | warning | 5m | threshold=5 |
| certificates | CertManagerCertNotReady | warning | 15m | 式の状態判定 |
| certificates | CertExpiringSoon | warning | 24h | days=30 |
| certificates | CertExpiryCritical | critical | 1h | days=7 |
| cnpg | CNPGCollectorDown | critical | 5m | 式の状態判定 |
| cnpg | CNPGMetricsAbsent | critical | 5m | 式の状態判定 |
| cnpg | CNPGPostmasterRestarted | warning | 0m | 式の状態判定 |
| cnpg | CNPGReplicationLag | warning | 10m | threshold=30 |
| cnpg | CNPGWALArchiveFailing | warning | 15m | 式の状態判定 |
| cnpg | CNPGWALArchiveStalled | warning | 15m | threshold=1800 |
| cnpg | CNPGDumpBackupStale | warning | 15m | threshold=108000 |
| longhorn | LonghornVolumeFaulted | critical | 5m | 式の状態判定 |
| longhorn | LonghornVolumeDegraded | warning | 15m | 式の状態判定 |
| longhorn | LonghornNodeSpaceLow | warning | 15m | threshold=0.1 |
| longhorn | LonghornNodeSpaceCritical | critical | 15m | threshold=0.05 |
| longhorn | LonghornDiskSpaceLow | warning | 15m | threshold=0.1 |
| longhorn | LonghornDiskSpaceCritical | critical | 15m | threshold=0.05 |
| flux | FluxReconciliationFailed | warning | 15m | 式の状態判定 |
| flux | FluxReconciliationStalled | warning | 15m | 式の状態判定 |
