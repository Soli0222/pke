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
| misskey | `misskey-cluster` | `misskey` | 150Gi | base backup + WAL archive | 01:30、WAL は継続 |
| grafana | `grafana-cluster` | `grafana` | 10Gi | pg_dump | 03:00 |
| sui | `sui-cluster` | `sui` | 5Gi | pg_dump | 03:05 |
| spotify-reblend | `reblend-cluster` | `reblend` | 5Gi | pg_dump | 03:10 |
| spotify-nowplaying | `spn-cluster` | `spn` | 5Gi | pg_dump | 03:15 |

定義は [natsume の各アプリ](flux/clusters/natsume/apps/) の `cluster.yaml` にある。
operator / Barman Cloud plugin のバージョンは [cnpg](flux/clusters/natsume/apps/cnpg/)、DB image と PostgreSQL 設定は各 Cluster を参照する。
Misskey の image には PGroonga が必要で、復元先にも同じ拡張と対応する PostgreSQL major version を用意する。

## バックアップの保存先と認証

R2 の bucket は `cnpg-backup`、保持期間は両方式とも7日。
Misskey の [ObjectStore](flux/clusters/natsume/apps/misskey/objectstore.yaml) は WAL / base backup を gzip 圧縮し、[ScheduledBackup](flux/clusters/natsume/apps/misskey/scheduledbackup.yaml) は UTC の6フィールド cron を使う。
他の DB の `cronjob-pg-dump.yaml` は `Asia/Tokyo` で実行し、`pg_dump -Fc --no-owner --no-privileges` の出力を保存する。
pg_dump の日時は UTC でファイル名に入る。

```text
s3://cnpg-backup/<cluster>/base/<backup-id>/
s3://cnpg-backup/<cluster>/wals/
s3://cnpg-backup/<cluster>/<cluster>-YYYYMMDD-HHMMSS.dump
```

| 1Password item | 同期先とキー |
|---|---|
| `cnpg-backup-s3-secret` | 各 DB namespace の同名 Secret。`ACCESS_KEY_ID`、`ACCESS_SECRET_KEY`、`ENDPOINT` |
| `cnpg-backup-flux-vars` | `flux-system/cnpg-backup-flux-vars`。`CNPG_BACKUP_ENDPOINT_URL` を Flux が Misskey の ObjectStore に注入 |

AWS CLI を使う操作では、1Password から `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_ENDPOINT_URL` を環境変数へ読み込んでおく。
認証情報をシェル履歴や出力に残さない。

## バックアップの成否を確認する

```sh
kubectl --context natsume@soli get clusters.postgresql.cnpg.io -A
kubectl --context natsume@soli -n misskey get scheduledbackups,backups
kubectl --context natsume@soli get cronjobs,jobs -A
aws s3 ls --endpoint-url "$AWS_ENDPOINT_URL" s3://cnpg-backup/ --recursive
```

Misskey は Backup の `status.phase=completed`、plugin のログ、R2 の base backup と必要な WAL を確認する。
ScheduledBackup の `lastScheduleTime` は実行予定が処理された時刻であり、成功の証拠にはならない。
pg_dump は CronJob の `lastSuccessfulTime`、失敗 Job のログ、R2 の dump を照合する。
成功 Job は保持しない設定なので、Job が見当たらないだけで未実行とは判断しない。

バックアップの完了と復元可能性は、別名 DB への復元で確認する。
WAL archive の成功だけで base backup の成功を判断しない。

## 手動バックアップを作る

Misskey は `kubectl cnpg` plugin で実行する。

```sh
kubectl cnpg backup misskey-cluster --context natsume@soli -n misskey \
  --method=plugin --plugin-name=barman-cloud.cloudnative-pg.io
kubectl --context natsume@soli -n misskey get backups -w
```

他の DB は既存 CronJob から Job を作る。以下は Grafana の例。

```sh
backup_job="grafana-cluster-pg-dump-manual-$(date -u +%Y%m%d%H%M%S)"
kubectl --context natsume@soli -n grafana create job "$backup_job" \
  --from=cronjob/grafana-cluster-pg-dump
kubectl --context natsume@soli -n grafana logs -f "job/$backup_job"
kubectl --context natsume@soli -n grafana get job "$backup_job"
```

Job の Complete と R2 への保存を確認する。
CronJob と同じ処理を使うため、手動実行でも保持期限を過ぎた dump の削除が行われる。

## Misskey を base backup と WAL から復元する

base backup と、その時点から復元目標までの WAL が必要となる。
復元は別名の Cluster に行い、元の DB とバックアップを残す。
方式は [Barman Cloud plugin の復元手順](https://cloudnative-pg.io/plugin-barman-cloud/docs/usage/#restoring-a-cluster) に従う。

以下を `misskey-restore.yaml` として保存し、`imageName` を復元元と互換性のある PGroonga image に置き換える。
復元先 namespace に `misskey-backup-store` と R2 Secret が存在すること、150Gi 以上を確保できるノードがあることを確認する。
必要な resources・配置制約は現在の Cluster 定義を基に設定する。

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: misskey-cluster-restored
  namespace: misskey
spec:
  instances: 1
  imageName: <PGroonga image compatible with the backup>
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
        serverName: misskey-cluster
```

特定時点へ復元する PITR では、`bootstrap.recovery.recoveryTarget.targetTime` にタイムゾーン付きの復元時刻を指定する。
省略時は利用可能な WAL の末尾まで復元する。
`serverName` は R2 上の復元元ディレクトリ名であり、復元先名に変えない。

```sh
kubectl --context natsume@soli apply -f misskey-restore.yaml
kubectl cnpg status misskey-cluster-restored --context natsume@soli -n misskey
```

この例には復元先の WAL archive 設定を含めていない。
運用に切り替える際は、復元元の archive を上書きしない保存先でバックアップを設定する。
アプリの DB 認証は復元先に合わせて設定し、接続確認後に Service / Secret の参照を切り替える。

## pg_dump から復元する

pg_dump は dump 作成時点への復元で、PITR はできない。
以下は Grafana を別名 `grafana-cluster-restored` に復元する例。

1. R2 の対象 dump を選び、ローカルに取得する。

   ```sh
   aws s3 ls --endpoint-url "$AWS_ENDPOINT_URL" s3://cnpg-backup/grafana-cluster/
   aws s3 cp --endpoint-url "$AWS_ENDPOINT_URL" \
     "s3://cnpg-backup/grafana-cluster/<dump-file>" ./restore.dump
   ```

2. [Grafana の Cluster 定義](flux/clusters/natsume/apps/grafana/cluster.yaml)を基に、`metadata.name` を `grafana-cluster-restored` とした空の Cluster を作る。
   DB / owner は `grafana` にそろえ、復元元と互換性のある PostgreSQL image を明示する。
   配置先の空き容量を確認し、Ready と新しい `grafana-cluster-restored-app` Secret の生成を待つ。

3. 復元先への port-forward を別ターミナルで維持する。

   ```sh
   kubectl --context natsume@soli -n grafana port-forward \
     svc/grafana-cluster-restored-rw 15432:5432
   ```

4. dump を作成した PostgreSQL と互換性のある `pg_restore` で投入する。
   以下は復元先 Secret を環境変数へ読み込み、値を出力せずに使う。

   ```sh
   PGUSER="$(kubectl --context natsume@soli -n grafana get secret grafana-cluster-restored-app -o jsonpath='{.data.username}' | base64 -d)"
   PGPASSWORD="$(kubectl --context natsume@soli -n grafana get secret grafana-cluster-restored-app -o jsonpath='{.data.password}' | base64 -d)"
   PGDATABASE="$(kubectl --context natsume@soli -n grafana get secret grafana-cluster-restored-app -o jsonpath='{.data.dbname}' | base64 -d)"
   export PGUSER PGPASSWORD PGDATABASE
   pg_restore -h 127.0.0.1 -p 15432 --no-owner --no-privileges \
     --exit-on-error -j 4 ./restore.dump
   unset PGUSER PGPASSWORD PGDATABASE
   ```

復元後はテーブル・拡張・データとアプリ接続を確認する。
接続先 Service と Secret を Git のアプリ定義で変更し、バックアップ CronJob と PodMonitor、監視対象 DB 名もそろえる。
切替時はアプリの書き込みを止める時点と復元対象を決め、確認用の古い dump のまま運用を再開しない。

## アラートからの調査

`cluster` は Kubernetes クラスタ、`cnpg_cluster` は DB 名。
meruto に DB はなく、operator の監視と DB の監視を区別する。

| アラート | 確認するもの |
|---|---|
| CollectorDown / MetricsAbsent | Cluster status、DB Pod、PodMonitor、Alloy の scrape error、NetworkPolicy |
| PostmasterRestarted | PostgreSQL の起動時刻、Pod 再作成、計画作業、ログ |
| WALArchiveFailing / Stalled | Misskey の最終成功・失敗時刻、WAL 生成、plugin のログ、ObjectStore、R2 接続 |
| DumpBackupStale | CronJob の最終成功、suspend、Job ログ、R2 の dump。CronJob 自体の削除も確認 |

Misskey の base backup の最終成功時刻はアラートで監視できていないため、Backup と R2 を直接確認する。
WAL archive の成功や ScheduledBackup の実行時刻で代用しない。
閾値とルールの設定は [CNPG の監視ルール](charts/monitoring-rules/README.md#cnpg)を参照する。
