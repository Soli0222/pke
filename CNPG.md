# PostgreSQL の運用

PKE の CloudNativePG（CNPG）は、natsume のアプリ用 PostgreSQL を管理する。
この文書は構成とバックアップ方式を確認した後、必要な操作の節を参照するための運用手順である。
kubectl の接続先は `natsume@soli` を使う。

## DB の構成

CNPG operator は両クラスタに導入するが、DB の `Cluster` は natsume のみ。
すべて `instances: 1`、StorageClass は `topolvm` で、ノード障害時の自動フェイルオーバー用 replica はない。
Pooler は使わず、アプリは CNPG の Service に直接接続する。

| Namespace | Cluster | DB / Owner | 容量 | バックアップ | 実行時刻（JST） |
|---|---|---|---|---|---|
| misskey | `misskey-cluster-restored` | `misskey` | 150Gi | base backup + WAL archive | 毎日 01:30 |
| grafana | `grafana-cluster-restored` | `grafana` | 10Gi | base backup + WAL archive | 日曜 03:00 |
| sui | `sui-cluster-restored` | `sui` | 5Gi | base backup + WAL archive | 日曜 03:15 |
| spotify-reblend | `reblend-cluster-restored` | `reblend` | 5Gi | base backup + WAL archive | 日曜 03:30 |
| spotify-nowplaying | `spn-cluster-restored` | `spn` | 5Gi | base backup + WAL archive | 日曜 03:45 |

定義は [natsume の各アプリ](flux/clusters/natsume/apps/) の `cluster.yaml` にある。
operator / Barman Cloud plugin のバージョンは [cnpg](flux/clusters/natsume/apps/cnpg/)、DB image と PostgreSQL 設定は各 Cluster を参照する。
Misskey の image には PGroonga が必要で、復元先にも同じ拡張と対応する PostgreSQL major version を用意する。
Misskey は `pgroonga_wal_resource_manager` を preload し、`pgroonga.enable_wal_resource_manager=on` で PostgreSQL の WAL に PGroonga の更新を記録する。
旧方式の `pgroonga.enable_wal` と `pgroonga.enable_crash_safe` は off とし、crash-safer は導入しない。
primary の異常終了後には手動修復が必要になる場合がある。
停止を伴う修復とバックアップ取得は [MAINTENANCE.md](MAINTENANCE.md) に従う。
無停止の定期バックアップからの PGroonga 復元条件は、別名 Cluster で検証する。

## バックアップの保存先と認証

全DBで Barman Cloud plugin による base backup と WAL archive を使う。
WAL は継続保存し、base backup は上表の頻度で取得する。
`ObjectStore` は稼働 DB の archive 用と復元元の読み取り用を分ける。
`objectstore-restore.yaml` に保持期限は設定せず、`Cluster.spec.externalClusters` の `serverName` で復元元 prefix を指定する。
稼働 DB の archive は現在の Cluster 名を prefix に使うため、復元元を上書きしない。
各アプリの `ObjectStore` は WAL / base backup を gzip 圧縮し、`ScheduledBackup` は UTC の6フィールド cron を使う。
週次の4DBは `immediate: true` により ScheduledBackup 作成時にも初回バックアップを取得する。
初回は定期実行の時刻分散が効かないため、導入時の負荷と完了状態を確認する。
plugin の追加は sidecar を含む DB Pod の更新を伴う。単一 instance のため、更新時の接続断を見込む。
初回 Backup が失敗した場合は原因を解消して手動で再取得し、週次の次回実行を待たない。
base backup と WAL からの復元を確認するまでは、保管済みの dump を残す。

R2 の bucket は `cnpg-backup`、`retentionPolicy: 7d` は7日間の復元可能期間を表す。
その期間の起点より前の base backup と必要な WAL も保持するため、7日を超えるオブジェクトが残る。
週次では復元時に再生する WAL が日次より多くなり、復元に時間がかかる場合がある。
保持の仕組みは [Barman の retention policy](https://cloudnative-pg.io/plugin-barman-cloud/docs/retention/) を参照する。

```text
s3://cnpg-backup/<cluster>/base/<backup-id>/
s3://cnpg-backup/<cluster>/wals/
```

| 1Password item | 同期先とキー |
|---|---|
| `cnpg-backup-s3-secret` | 各 DB namespace の同名 Secret。`ACCESS_KEY_ID`、`ACCESS_SECRET_KEY`、`ENDPOINT` |
| `cnpg-backup-flux-vars` | `flux-system/cnpg-backup-flux-vars`。`CNPG_BACKUP_ENDPOINT_URL` を Flux が各 DB の ObjectStore に注入 |

AWS CLI を使う操作では、1Password から `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_ENDPOINT_URL` を環境変数へ読み込んでおく。
認証情報をシェル履歴や出力に残さない。

## バックアップの成否を確認する

```sh
kubectl --context natsume@soli get clusters.postgresql.cnpg.io -A
kubectl --context natsume@soli get scheduledbackups,backups -A
kubectl --context natsume@soli get objectstores.barmancloud.cnpg.io -A
aws s3 ls --endpoint-url "$AWS_ENDPOINT_URL" s3://cnpg-backup/ --recursive
```

各 DB の Backup の `status.phase=completed`、plugin のログ、R2 の base backup と必要な WAL を確認する。
ScheduledBackup の `lastScheduleTime` は実行予定が処理された時刻であり、成功の証拠にはならない。
既存の PodMonitor は instance manager の metrics endpoint から plugin の指標も収集し、`cnpg_cluster` ラベルを付ける。
最終成功は `barman_cloud_cloudnative_pg_io_last_available_backup_timestamp`、復元可能期間の起点は `barman_cloud_cloudnative_pg_io_first_recoverability_point` を確認する。
従来の `cnpg_collector_last_available_backup_timestamp` は plugin の成功判定に使わない。

バックアップの完了と復元可能性は、別名 DB への復元で確認する。
WAL archive の成功だけで base backup の成功を判断しない。

## 手動バックアップを作る

全DBで `kubectl cnpg` plugin を使う。以下は Grafana の例。
対象に合わせて namespace と Cluster 名を置き換える。

```sh
kubectl cnpg backup grafana-cluster-restored --context natsume@soli -n grafana \
  --method=plugin --plugin-name=barman-cloud.cloudnative-pg.io
kubectl --context natsume@soli -n grafana get backups -w
```

Backup の `status.phase=completed` と R2 への保存を確認する。
手動実行でも ObjectStore の保持設定が適用される。

## base backup と WAL から復元する

base backup と、その時点から復元目標までの WAL が必要となる。
復元は別名の Cluster に行い、元の DB とバックアップを残す。
方式は [Barman Cloud plugin の復元手順](https://cloudnative-pg.io/plugin-barman-cloud/docs/usage/#restoring-a-cluster) に従う。

全DBで同じ方式を使い、namespace、Cluster 名、ObjectStore 名、image、容量を復元元に合わせる。
以下は Misskey の例。`misskey-restore.yaml` として保存し、`imageName` を復元元と互換性のある PGroonga image に置き換える。
復元先 namespace に `misskey-backup-store` と R2 Secret が存在すること、150Gi 以上を確保できるノードがあることを確認する。
必要な resources・配置制約は現在の Cluster 定義を基に設定する。

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: misskey-recovery
  namespace: misskey
spec:
  instances: 1
  imageName: <PGroonga image compatible with the backup>
  postgresql:
    shared_preload_libraries:
    - pgroonga_wal_resource_manager
    parameters:
      pgroonga.enable_wal_resource_manager: "on"
      pgroonga.enable_wal: "off"
      pgroonga.enable_crash_safe: "off"
  storage:
    storageClass: topolvm
    size: 150Gi
  bootstrap:
    recovery:
      source: misskey-source
  externalClusters:
  - name: misskey-source
    plugin:
      name: barman-cloud.cloudnative-pg.io
      parameters:
        barmanObjectName: misskey-backup-store
        serverName: misskey-cluster-restored
```

特定時点へ復元する PITR では、`bootstrap.recovery.recoveryTarget.targetTime` にタイムゾーン付きの復元時刻を指定する。
省略時は利用可能な WAL の末尾まで復元する。
`serverName` は R2 上の復元元ディレクトリ名であり、復元先名に変えない。

PGroonga の custom WAL を再生するため、復元先にも WAL 再生の開始前から resource manager の preload 設定が必要となる。
メンテナンスで取得したバックアップの検証では、保全した Barman の ID を `bootstrap.recovery.recoveryTarget.backupID` に明示する。

```sh
kubectl --context natsume@soli apply -f misskey-restore.yaml
kubectl cnpg status misskey-recovery --context natsume@soli -n misskey
```

この例には復元先の WAL archive 設定を含めていない。
運用に切り替える際は、復元元の archive を上書きしない保存先でバックアップを設定する。
アプリの DB 認証は復元先に合わせて設定し、接続確認後に Service / Secret の参照を切り替える。

## 保管済みの pg_dump から復元する

R2 に保管済みの `<cluster>/<cluster>-YYYYMMDD-HHMMSS.dump` がある場合に使う。
新規の定期 dump は取得しない。Barman の保持設定はこれらの dump を削除しないため、base backup からの復元確認後に保存要否を判断する。
pg_dump は dump 作成時点への復元で、PITR はできない。
以下は Grafana を別名 `grafana-recovery` に復元する例。

1. R2 の対象 dump を選び、ローカルに取得する。

   ```sh
   aws s3 ls --endpoint-url "$AWS_ENDPOINT_URL" s3://cnpg-backup/grafana-cluster/
   aws s3 cp --endpoint-url "$AWS_ENDPOINT_URL" \
     "s3://cnpg-backup/grafana-cluster/<dump-file>" ./restore.dump
   ```

2. [Grafana の Cluster 定義](flux/clusters/natsume/apps/grafana/cluster.yaml)を基に、`metadata.name` を `grafana-recovery` とした空の Cluster を作る。
   DB / owner は `grafana` にそろえ、復元元と互換性のある PostgreSQL image を明示する。
   `bootstrap` は `initdb` に替え、DB / owner を指定する。`spec.plugins` と `spec.externalClusters` はコピーせず、元の archive に書き込まない。運用開始時に復元先専用の保存先で設定する。
   配置先の空き容量を確認し、Ready と新しい `grafana-recovery-app` Secret の生成を待つ。

3. 復元先への port-forward を別ターミナルで維持する。

   ```sh
   kubectl --context natsume@soli -n grafana port-forward \
     svc/grafana-recovery-rw 15432:5432
   ```

4. dump を作成した PostgreSQL と互換性のある `pg_restore` で投入する。
   以下は復元先 Secret を環境変数へ読み込み、値を出力せずに使う。

   ```sh
   PGUSER="$(kubectl --context natsume@soli -n grafana get secret grafana-recovery-app -o jsonpath='{.data.username}' | base64 -d)"
   PGPASSWORD="$(kubectl --context natsume@soli -n grafana get secret grafana-recovery-app -o jsonpath='{.data.password}' | base64 -d)"
   PGDATABASE="$(kubectl --context natsume@soli -n grafana get secret grafana-recovery-app -o jsonpath='{.data.dbname}' | base64 -d)"
   export PGUSER PGPASSWORD PGDATABASE
   pg_restore -h 127.0.0.1 -p 15432 --no-owner --no-privileges \
     --exit-on-error -j 4 ./restore.dump
   unset PGUSER PGPASSWORD PGDATABASE
   ```

復元後はテーブル・拡張・データとアプリ接続を確認する。
接続先 Service と Secret を Git のアプリ定義で変更し、ObjectStore、ScheduledBackup、PodMonitor、監視対象 DB 名もそろえる。
切替時はアプリの書き込みを止める時点と復元対象を決め、確認用の古い dump のまま運用を再開しない。

## アラートからの調査

`cluster` は Kubernetes クラスタ、`cnpg_cluster` は DB 名。
meruto に DB はなく、operator の監視と DB の監視を区別する。

| アラート | 確認するもの |
|---|---|
| CollectorDown / MetricsAbsent | Cluster status、DB Pod、PodMonitor、Alloy の scrape error、NetworkPolicy |
| PostmasterRestarted | PostgreSQL の起動時刻、Pod 再作成、計画作業、ログ |
| WALArchiveFailing / Stalled | 対象 DB の最終成功・失敗時刻、WAL 生成、plugin のログ、ObjectStore、R2 接続 |
| BaseBackupStale | Barman plugin の最終成功時刻、ScheduledBackup の suspend / スケジュール、Backup の完了状態、ObjectStore、R2 の base backup |

WALArchiveStalled は同じ DB Pod の未送信 WAL（`ready`）が1件以上あり、最終成功から30分超の状態が15分続くと通知する。
DB に活動がなく、新しい WAL の送信待ちがない場合は通知しない。

base backup の期限は `baseBackupMaxAgeSeconds` で DB ごとに指定する。
Misskey は30時間、週次の4DBは7日12時間。期限超過・成功時刻0・欠測を監視する。
WAL archive の成功や ScheduledBackup の実行時刻で代用しない。
閾値とルールの設定は [CNPG の監視ルール](charts/monitoring-rules/README.md#cnpg)を参照する。
