# PostgreSQL の運用

PKE の CloudNativePG（CNPG）は、natsume のアプリ用 PostgreSQL を管理する。
この文書は構成とバックアップ方式を確認した後、必要な操作の節を参照するための運用手順である。
通常運用の kubectl の接続先は `natsume@soli` を使う。
別クラスタで復元するときは、復元先の context を明示する。

## DB の構成

CNPG operator は両クラスタに導入するが、恒久運用の DB の `Cluster` は natsume のみ。
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
Misskey の物理復元は、[通常の PITR と PGroonga の再構築](#misskey-は-pitr-後に-pgroonga-を再構築する)を標準手順とする。
容量回収のための `VACUUM FULL` は、テーブルの膨張と一時容量を確認して対象を選ぶ。
定期バックアップの復元確認には、この手順を別名 Cluster で実行する。

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

namespace、Cluster 名、ObjectStore 名、image、容量を復元元と復元先に合わせる。
Misskey は PostgreSQL の復元後に PGroonga の再構築を行い、他の DB にはこの追加操作を適用しない。
以下は Misskey の例。`misskey-restore.yaml` として保存し、`imageName` を復元元と互換性のある PGroonga image に置き換える。
復元先 namespace に保持期限のない読み取り用 `misskey-backup-store-restore` と R2 Secret を用意する。
元の Cluster、PVC、backup / WAL は残し、既存名と衝突するリソースを上書きしない。
容量は復元データに加えて REINDEX 中の旧・新 index と WAL の余裕を確保し、StorageClass と実際の空きを確認する。
必要な resources・配置制約は現在の Cluster 定義を基に設定する。
復元先はアプリから分離し、検索再構築と検証が完了するまで Misskey web / worker などの接続・書き込みを開始しない。

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
      database: misskey
      owner: misskey
      recoveryTarget:
        backupID: "<復元に使うBarman backup ID>"
        targetTime: "<復元目標の時刻。タイムゾーン付き>"
  externalClusters:
  - name: misskey-source
    plugin:
      name: barman-cloud.cloudnative-pg.io
      parameters:
        barmanObjectName: misskey-backup-store-restore
        serverName: misskey-cluster-restored
```

placeholder を実際の値へ置き換え、対象 backup の DONE、データ本体と目標までの WAL を確認してから作成する。
`backupID` は Kubernetes の Backup 名ではなく Barman の ID。別の backup が増えても開始点が変わらないよう明示する。
特定時点へ復元する PITR では、`targetTime` にタイムゾーン付きの時刻を指定する。LSN を使う場合は `targetTime` を外し、`targetLSN` を指定する。
利用可能な archive の末尾まで戻す場合は、`targetTime` / `targetLSN` を外す。
timeline を数値指定する場合は、対応する history ファイルも確認する。省略時の `latest` を含め、実際に再生した timeline をログで照合する。
`serverName` は R2 上の復元元ディレクトリ名であり、復元先名に変えない。
`database` / `owner` は復元元と一致させる。省略時の既定値 `app` に任せると、昇格後の role 管理や生成 Secret が実 DB と一致しない場合がある。

PostgreSQL WAL の custom resource manager を認識するため、復元先にも WAL 再生の開始前から preload 設定が必要となる。
preload だけで PGroonga の検索更新まで復元できたとは判断しない。

```sh
PKE_RESTORE_CONTEXT=natsume@soli  # meruto で検証する場合は meruto@soli
PKE_RESTORE_NAMESPACE=misskey
PKE_RESTORE_CLUSTER=misskey-recovery
PKE_RESTORE_DATABASE=misskey

kubectl --context "$PKE_RESTORE_CONTEXT" create --dry-run=server -f misskey-restore.yaml &&
  kubectl --context "$PKE_RESTORE_CONTEXT" create -f misskey-restore.yaml
kubectl cnpg status "$PKE_RESTORE_CLUSTER" --context "$PKE_RESTORE_CONTEXT" -n "$PKE_RESTORE_NAMESPACE"
```

この例には復元先の WAL archive 設定を含めていない。
復元用の `externalClusters[].plugin` だけを指定し、元の prefix へ書き込む `spec.plugins`、Backup、ScheduledBackup を持ち込まない。

### Misskey は PITR 後に PGroonga を再構築する

以下では、前節の `PKE_RESTORE_*` を復元先の context・namespace・Cluster・DB 名に設定して使う。
通常の primary 復元で PostgreSQL のテーブルを目標時点へ戻し、その内容から PGroonga index を再生成する。
復元先に `spec.replica.enabled: true` は設定しない。
現行の PGroonga は standby の WAL 再生を対象としており、通常の archive / PITR では検索 index の更新が欠落し得る。
現行の [CNPG の replica 復元処理](https://github.com/cloudnative-pg/cloudnative-pg/blob/2a35abb4628f209d149825ef3c38011e0701ff2f/pkg/management/postgres/restore.go#L188)では `targetTime` が停止条件として適用されず、一時停止後の昇格でも追加 WAL が再生され得るため、指定時点への復元に standby → 昇格を使わない。
設定の前提は [PGroonga の WAL resource manager](https://pgroonga.github.io/reference/modules/pgroonga-wal-resource-manager.html) と [CNPG の復元 API](https://cloudnative-pg.io/docs/1.28/recovery/)を参照する。

PostgreSQL の復元、PGroonga の再構築、検索・更新の確認までを一連の復元手順とする。
以下を順に実施し、確認が完了してからアプリを再開する。

1. **PostgreSQL の復元完了を確認する。** Ready に加え、固定した backup ID、指定した target、実際の終了 LSN / transaction 時刻と timeline をログで照合する。
   `pg_is_in_recovery()` が false になるまで REINDEX と書き込み検証を開始しない。
   CNPG の通常再起動後に `pg_last_wal_replay_lsn()` が NULL になる場合は、復元 Job / Pod のログを使う。
   `kubectl exec` の接続先は復元先の `.status.currentPrimary` から取得する。

   ```sh
   PKE_RESTORE_POD="$(kubectl --context "$PKE_RESTORE_CONTEXT" -n "$PKE_RESTORE_NAMESPACE" \
     get clusters.postgresql.cnpg.io "$PKE_RESTORE_CLUSTER" -o jsonpath='{.status.currentPrimary}')"

   pke_restore_psql() {
     kubectl --context "$PKE_RESTORE_CONTEXT" -n "$PKE_RESTORE_NAMESPACE" \
       exec -i "$PKE_RESTORE_POD" -c postgres -- \
       psql -X -v ON_ERROR_STOP=1 -U postgres -d "$PKE_RESTORE_DATABASE" "$@"
   }

   pke_restore_psql -c 'SELECT pg_is_in_recovery(), pg_last_wal_replay_lsn(), pg_last_xact_replay_timestamp();'
   ```

2. **修復前の状態を保全する。** PostgreSQL / Groonga / Barman のログ、DB の行数、index 定義と valid / ready、PGroonga 設定を記録する。
   検証用の復元では、再構築前の検索結果も残し、物理コピーと WAL だけで戻った範囲を REINDEX 後の成功と区別する。
   生ログ、投稿 ID、認証情報を含む資料は Git の外に保存し、アクセス権を制限する。
   Groonga 内部 DB が開けず再構築できない場合は、エラーを保存して調査する。内部ファイルや本物の投稿を自動削除して進めない。

3. **PGroonga index を通常の REINDEX で再構築する。** アプリの接続を止めた復元先で行い、他の B-tree 等を一律に再構築しない。
   対象 index を列挙して漏れを確認し、定義・tokenizer・normalizer 等の設定を保持する。
   通常の REINDEX はテーブルから index を作り直す。[PostgreSQL の REINDEX](https://www.postgresql.org/docs/18/sql-reindex.html)と [PGroonga の内部オブジェクト整理](https://pgroonga.github.io/reference/functions/pgroonga-vacuum.html)を参照する。

   ```sh
   pke_restore_psql <<'SQL'
   BEGIN READ ONLY;
   SET LOCAL statement_timeout = '30s';
   SELECT n.nspname, c.relname, pg_get_indexdef(c.oid)
   FROM pg_class c
   JOIN pg_namespace n ON n.oid = c.relnamespace
   JOIN pg_am a ON a.oid = c.relam
   WHERE c.relkind = 'i' AND a.amname = 'pgroonga';
   COMMIT;
   SQL

   pke_restore_psql <<'SQL'
   SET lock_timeout = '30s';
   SET statement_timeout = 0;
   REINDEX (VERBOSE) INDEX public.idx_note_text_with_pgroonga;
   SELECT pgroonga_vacuum();
   ANALYZE public.note;
   SQL
   ```

   再構築を時間だけで打ち切らず、`pg_stat_progress_create_index`、バックエンド状態、ログと PVC 使用量で進捗を追跡する。
   接続断や中断後は実行中の処理を確認してから再試行し、同じ処理を重複実行しない。
   失敗した場合はアプリを再開せず、失敗ログと index 状態を残す。

4. **設定と検索の整合性を確認する。** PGroonga / libgroonga / PostgreSQL のバージョン、preload、resource manager=on、旧 enable_wal=off、crash_safe=off、DB / role 単位の override と pending_restart を確認する。
   custom GUC は接続内で PGroonga をロードしてから確認する。
   index の valid / ready と EXPLAIN を確認し、代表 ID とバックアップ後の通常投稿の検索も復元時点に合わせて照合する。
   本文・ID の一覧を公開資料に出力しない。
   逐次評価との比較は同じ snapshot・同じ行集合で行い、対象行数と timeout を限定する。

   以下は、ID 昇順の20,000行を対象に、逐次評価と PGroonga index の検索結果を比較する例。
   両語で欠落と余分な一致が0であることを確認する。この比較は全投稿・全検索語の一致を保証しない。

   ```sh
   for PKE_RESTORE_TERM in 'ぎる' 'くな'; do
     pke_restore_psql -v search_term="$PKE_RESTORE_TERM" <<'SQL' || break
   BEGIN READ ONLY;
   SET LOCAL statement_timeout = '60s';
   SET LOCAL enable_seqscan = off;
   EXPLAIN (COSTS OFF)
     SELECT id FROM public.note WHERE text &@~ :'search_term';
   WITH sample AS MATERIALIZED (
     SELECT id, text FROM public.note ORDER BY id LIMIT 20000
   ), sequential_matches AS MATERIALIZED (
     SELECT id FROM sample WHERE text &@~ :'search_term'
   ), index_matches AS MATERIALIZED (
     SELECT id FROM public.note WHERE text &@~ :'search_term'
   ), scoped_index AS MATERIALIZED (
     SELECT i.id FROM index_matches i JOIN sample s USING (id)
   )
   SELECT :'search_term' AS term,
     (SELECT count(*) FROM sample) AS rows_compared,
     (SELECT count(*) FROM sequential_matches) AS sequential_matches,
     (SELECT count(*) FROM scoped_index) AS index_matches,
     (SELECT count(*) FROM (
       SELECT id FROM sequential_matches EXCEPT SELECT id FROM scoped_index
     ) d) AS missing_from_index,
     (SELECT count(*) FROM (
       SELECT id FROM scoped_index EXCEPT SELECT id FROM sequential_matches
     ) d) AS extra_in_index;
   COMMIT;
   SQL
   done
   ```

   この sample とは別に、選んだ backup の完了後から復元目標までの投稿についても、本文への逐次評価と index 検索を突き合わせる。
   LSN の進行だけで検索更新の整合性を証明したとは扱わない。

5. **復元先だけで更新と再起動を確認してから切り替える。** 専用 schema / table と PGroonga index を作り、INSERT・UPDATE・DELETE をそれぞれ COMMITする。
   別接続から肯定・否定検索を確認し、必要なら復元先だけを通常再起動して永続性を確認する。本物の `note` に試験投稿を作らない。
   DB / Groonga ログの decode・merge・flush 等のエラー、Pod・PVC と空き容量を確認する。
   アプリ用 Secret の username / dbname が復元元の DB / owner と一致することを、値を公開せずに確認する。
   運用に切り替える際は復元元と異なる archive prefix へバックアップを設定し、Service / Secret の参照と実アプリ接続を確認してから再開する。

`VACUUM FULL` は復元時の既定操作に含めない。
大きなテーブル膨張があり、テーブルの書き直しと WAL に十分な一時容量を確保できる場合だけ検討する。
`note` に FULL を行う場合は同じ操作で index も再構築されるため、上の独立した REINDEX を重ねて実行しない。
旧内部オブジェクト整理と検索検証は、どちらの再構築方法でも行う。

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
meruto に恒久運用の DB はなく、operator の監視と DB の監視を区別する。

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
