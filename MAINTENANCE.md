# Misskey DB の夜間メンテナンス

Misskey の書き込みを停止してからDBを保全し、PGroongaのインデックスを修復してWAL resource managerへ移行する。
再開前に不要領域を整理し、新しい物理バックアップを取得する。
この文書は実行手順であり、メンテナンスを実施済みとする記録ではない。

## 対象と方針

対象は `natsume@soli` の namespace `misskey`、CNPG Cluster `misskey-cluster-restored`、DB `misskey` とする。
対象インデックスは `public.note` の `public.idx_note_text_with_pgroonga`。
merutoと他のDBには変更を適用しない。
image、容量、PostgreSQL設定は [Cluster定義](flux/clusters/natsume/apps/misskey/cluster.yaml)、アプリ設定は [HelmRelease定義](flux/clusters/natsume/apps/misskey/helmrelease-misskey.yaml)を正とする。
通常のバックアップ運用と復元手順は [CNPG.md](CNPG.md) を参照する。

- `pgroonga_wal_resource_manager` を導入し、従来の `pgroonga.enable_wal` を無効化する。
- `pgroonga_crash_safer` は導入せず、`pgroonga.enable_crash_safe` は無効のままにする。
- 検索provider、ObjectStore、定期バックアップの方式、保存先、スケジュールは維持する。
- `VACUUM FULL` は不要領域を測定して対象を選ぶ。全DBへ一律に実行しない。
- 通常の再構築で失敗した場合、PGroonga内部DBの削除へ自動的に進まない。
- 本番への障害注入、合成投稿、合成通知は行わない。投稿確認は再開後の通常利用で行う。

WAL resource managerは、正常なバックアップを起点にPGroongaの更新を再生するための機能である。
すでに壊れたインデックスの修復と、稼働中primaryのクラッシュ復旧は別の問題である。
crash-saferを導入しないため、primaryの異常終了後に手動修復が必要になる可能性は残る。
無停止の定期バックアップから正しく復元できる条件の確認は、後続の検証に残す。

## 実行前の準備

以下のコマンドはリポジトリのルートから、同じシェルで節ごとに実行する。
各コマンドの終了コードと出力を確認し、失敗した場合は次の変更へ進まない。
実行者が中断する場合は、停止状態、完了した処理、実行中の処理、保全先を引き継ぐ。

```sh
umask 077
PKE_CONTEXT=natsume@soli
PKE_NAMESPACE=misskey
PKE_DB_CLUSTER=misskey-cluster-restored
PKE_DATABASE=misskey
PKE_SCHEDULE=misskey-cluster-restored-daily-backup
PKE_MAINT_DIR="$HOME/backups/pke/misskey/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$PKE_MAINT_DIR"

pke_refresh_primary() {
  PKE_DB_POD="$(kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
    get clusters.postgresql.cnpg.io "$PKE_DB_CLUSTER" \
    -o jsonpath='{.status.currentPrimary}')" || return
  test -n "$PKE_DB_POD"
}

pke_psql() {
  kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
    exec -i "$PKE_DB_POD" -c postgres -- \
    psql -X -U postgres -d "$PKE_DATABASE" -v ON_ERROR_STOP=1 "$@"
}

pke_refresh_primary
kubectl cnpg status "$PKE_DB_CLUSTER" --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" get deployments,pods,jobs,cronjobs
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get scheduledbackups.postgresql.cnpg.io,backups.postgresql.cnpg.io
df -h "$PKE_MAINT_DIR"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec "$PKE_DB_POD" -c postgres -- df -h /var/lib/postgresql/data
```

CNPGのDB Podを停止する操作と、Misskeyアプリの停止を区別する。
この手順ではDBを稼働させたままアプリを止め、設定変更時にDBを再起動する。
DB Podへの接続は再起動後に再取得する。

保全先はDBノードとは別の永続ディスクに置く。
dumpは非公開ノートやアプリ内の認証情報を含み得るため、内容をログやPRへ貼らず、保全ディレクトリをGit管理に入れない。
圧縮率に依存せずdumpを保存できる空き容量と、作業中のDB容量を確認する。
DB使用量は、テーブル別容量の合計にPGroongaの `pgrn*` ファイルなどを加えた値である。
使用量だけを根拠に肥大を判断しない。

ライブラリと診断用拡張の配布状況を確認する。
PostgreSQLのmajor versionとPGroongaの互換性を確認し、この作業にimage更新を混在させない。

```sh
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec "$PKE_DB_POD" -c postgres -- pg_config --pkglibdir
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec "$PKE_DB_POD" -c postgres -- ls -l \
  /usr/local/lib/postgresql/pgroonga_wal_resource_manager.so
pke_psql <<'SQL'
SELECT version();
SELECT extname, extversion FROM pg_extension WHERE extname IN ('pgroonga', 'pgstattuple');
SELECT name, default_version, installed_version
FROM pg_available_extensions WHERE name = 'pgstattuple';
SQL
```

現在の一時停止状態、replica数、設定を保存する。
以下の復帰コマンドは「作業開始時にFluxと定期バックアップが有効」という通常状態を想定する。
すでに停止していたリソースは、復帰時にもその状態を維持する。

```sh
kubectl --context "$PKE_CONTEXT" -n flux-system \
  get fluxinstances.fluxcd.controlplane.io flux -o json \
  > "$PKE_MAINT_DIR/fluxinstance-before.json"
kubectl --context "$PKE_CONTEXT" -n flux-system \
  get kustomizations.kustomize.toolkit.fluxcd.io flux-system misskey \
  -o custom-columns=NAME:.metadata.name,SUSPEND:.spec.suspend,REVISION:.status.lastAppliedRevision \
  > "$PKE_MAINT_DIR/flux-before.txt"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get helmreleases.helm.toolkit.fluxcd.io misskey \
  -o custom-columns=NAME:.metadata.name,SUSPEND:.spec.suspend \
  > "$PKE_MAINT_DIR/helm-before.txt"
PKE_WEB_REPLICAS="$(kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get deployment misskey-web -o jsonpath='{.spec.replicas}')"
printf '%s\n' "$PKE_WEB_REPLICAS" > "$PKE_MAINT_DIR/web-replicas-before.txt"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get scheduledbackups.postgresql.cnpg.io "$PKE_SCHEDULE" \
  -o jsonpath='{.spec.suspend}{"\n"}' > "$PKE_MAINT_DIR/schedule-before.txt"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get clusters.postgresql.cnpg.io "$PKE_DB_CLUSTER" \
  -o jsonpath='{.spec.postgresql}{"\n"}' > "$PKE_MAINT_DIR/postgresql-before.json"
cp flux/clusters/natsume/apps/misskey/cluster.yaml "$PKE_MAINT_DIR/cluster-before.yaml"
pke_psql <<'SQL' > "$PKE_MAINT_DIR/pgroonga-settings-before.txt"
SELECT pg_postmaster_start_time();
SELECT name, setting, source FROM pg_settings
WHERE name IN ('shared_preload_libraries', 'autovacuum') OR name LIKE 'pgroonga.%'
ORDER BY name;
SELECT COALESCE(d.datname, '*') AS database, COALESCE(r.rolname, '*') AS role, cfg
FROM pg_db_role_setting s
LEFT JOIN pg_database d ON d.oid = s.setdatabase
LEFT JOIN pg_roles r ON r.oid = s.setrole
CROSS JOIN LATERAL unnest(s.setconfig) cfg
WHERE cfg LIKE 'pgroonga.%';
SELECT n.nspname, c.relname, pg_relation_filenode(c.oid) AS filenode,
       i.indisvalid, i.indisready, pg_get_indexdef(c.oid) AS definition
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_am a ON a.oid = c.relam WHERE a.amname = 'pgroonga';
SQL
```

DB単位、role単位、roleとDBの組み合わせにある上書きを確認する。
以降の解除SQLは、DB `misskey` に旧 `enable_wal=on` がある場合を想定する。
別の上書きがあれば対象を明示して解除し、他のDBの設定には触れない。

## 1. Misskeyの書き込みを停止する

Flux Operatorによるrootの停止解除を防ぐため、まずFluxInstanceの再調整を止める。
続いて、親のKustomizationから子の停止状態が上書きされないよう、root、Misskey、HelmReleaseの順に止める。
rootを止めている間も、他アプリの既存Kustomizationは動作を続けるが、rootからの構成更新は保留される。
CNPG operatorとBarman pluginは停止しない。

```sh
kubectl --context "$PKE_CONTEXT" -n flux-system \
  annotate fluxinstance flux fluxcd.controlplane.io/reconcile=disabled --overwrite
flux suspend kustomization flux-system --context "$PKE_CONTEXT" -n flux-system
flux suspend kustomization misskey --context "$PKE_CONTEXT" -n flux-system
flux suspend helmrelease misskey --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  patch scheduledbackups.postgresql.cnpg.io "$PKE_SCHEDULE" --type merge \
  -p '{"spec":{"suspend":true}}'
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  scale deployment misskey-web --replicas=0
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get deployment misskey-web
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get pods -l app.kubernetes.io/instance=misskey
pke_psql <<'SQL'
SELECT usename, application_name, backend_type, state, count(*)
FROM pg_stat_activity WHERE datname = current_database()
GROUP BY usename, application_name, backend_type, state;
SQL
```

MisskeyのPod消失と、Misskey用roleの接続数が0になったことを確認する。
移行Job、別のworker、HPA、DB直結ツールなどが存在する場合は、それらによる再接続も止める。
Valkeyを停止したり、キューを消したりしない。
停止前に始まったBackupは完了または失敗を確認してから進む。

autovacuumによるインデックス更新も止める。
この一時設定はGitへ残さず、手動VACUUMは修復後に実行する。

```sh
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  patch clusters.postgresql.cnpg.io "$PKE_DB_CLUSTER" --type merge \
  -p '{"spec":{"postgresql":{"parameters":{"autovacuum":"off"}}}}'
pke_psql <<'SQL'
SHOW autovacuum;
SELECT pid, datname, backend_type, state
FROM pg_stat_activity WHERE backend_type = 'autovacuum worker';
SQL
```

`autovacuum=off` と既存workerの終了を確認する。
activeなVACUUM、DDL、書き込み接続もないことを確認する。
workerが継続する場合は終了を待つ。
transaction ID wraparound対策のworkerはoffでも起動し得るため、Backup取得直前にも確認する。

## 2. DBとログを保全する

アプリ停止後に論理バックアップを取得する。
dumpにはテーブルデータとインデックス定義が入り、破損したPGroongaインデックスの実データはコピーされない。
新しい別名Clusterへ復元する際の保全物として扱い、本番DBへ上書きrestoreしない。

```sh
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec "$PKE_DB_POD" -c postgres -- \
  pg_dump -U postgres -d "$PKE_DATABASE" --format=custom \
  > "$PKE_MAINT_DIR/misskey-before.dump" \
  2> "$PKE_MAINT_DIR/pg-dump.stderr"
```

終了コード0とファイルが空でないことを確認する。
次のコマンドはarchiveを最後まで読み、SQLの出力先を `/dev/null` とする。
DBへのrestoreは実行しない。
目次確認と全データの読み取りが成功しても、別DBへの復元検証を済ませたことにはならない。

```sh
test -s "$PKE_MAINT_DIR/misskey-before.dump"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec -i "$PKE_DB_POD" -c postgres -- pg_restore --list \
  < "$PKE_MAINT_DIR/misskey-before.dump" > "$PKE_MAINT_DIR/dump-contents.txt"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec -i "$PKE_DB_POD" -c postgres -- pg_restore --file=/dev/null \
  < "$PKE_MAINT_DIR/misskey-before.dump"
shasum -a 256 "$PKE_MAINT_DIR/misskey-before.dump" \
  > "$PKE_MAINT_DIR/misskey-before.dump.sha256"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  logs "$PKE_DB_POD" -c postgres --since=24h > "$PKE_MAINT_DIR/postgres-before.log"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec "$PKE_DB_POD" -c postgres -- \
  gzip -c /var/lib/postgresql/data/pgdata/pgroonga.log > "$PKE_MAINT_DIR/pgroonga-before.log.gz"
gzip -t "$PKE_MAINT_DIR/pgroonga-before.log.gz"
```

PGroongaログは大きくなるため、元ファイルを残したまま圧縮して転送する。
転送が切れた場合は不完全なファイルを成功扱いせず、サーバー側に残った読み取り処理を確認してから別名で再取得する。

dumpまたはarchive確認が失敗した場合、移行、REINDEX、FULLへ進まない。
既存の物理バックアップとWAL archiveも残す。
移行前のバックアップは移行後のWAL resource managerによって修復されるものではない。

## 3. WAL resource managerへ移行する

Cluster定義の `spec.postgresql` に以下を追加する。
既存のparametersと既存のpreload libraryを保持し、`pgroonga_wal_resource_manager` は重複なく追加する。

```yaml
spec:
  postgresql:
    shared_preload_libraries:
    - pgroonga_wal_resource_manager
    parameters:
      pgroonga.enable_wal_resource_manager: "on"
      pgroonga.enable_wal: "off"
      pgroonga.enable_crash_safe: "off"
```

この断片だけでCluster全体を置き換えない。
`shared_preload_libraries` はCNPG専用の配列フィールドに設定し、`parameters.shared_preload_libraries` には置かない。
bootstrapの復元元、storage、image、Barman設定は変更しない。
この節の作業は手順実行時に行い、手順書の作成だけで本番設定が移行済みとは扱わない。

```sh
kubectl kustomize flux/clusters/natsume/apps/misskey > /dev/null
python3 scripts/sync-monitoring-inventory.py
git diff --check
git diff -- flux/clusters/natsume/apps/misskey/cluster.yaml
pke_psql -c 'ALTER DATABASE misskey RESET pgroonga.enable_wal;'
kubectl --context "$PKE_CONTEXT" apply \
  -f flux/clusters/natsume/apps/misskey/cluster.yaml
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  patch clusters.postgresql.cnpg.io "$PKE_DB_CLUSTER" --type merge \
  -p '{"spec":{"postgresql":{"parameters":{"autovacuum":"off"}}}}'
```

設定適用で一時キーが失われないよう、autovacuum停止を再指定する。
再起動後も `SHOW autovacuum` と既存workerの終了を確認する。

DB単位の旧WAL設定の解除は新しい接続から有効になる。
更新が自動再起動を伴った場合は、その完了を先に確認する。
再起動が必要なままの場合は、次のコマンドで現在のprimaryを指定して再起動する。
直前からPostgreSQLの起動時刻が更新されたことも確認し、古いPodのReadyだけで完了判定しない。

```sh
kubectl cnpg restart "$PKE_DB_CLUSTER" "$PKE_DB_POD" \
  --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE"
```

CNPG status、PodのReady、SQL接続を確認し、primaryを再取得する。
不要な再起動を繰り返さない。

```sh
pke_refresh_primary
kubectl cnpg status "$PKE_DB_CLUSTER" --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get clusters.postgresql.cnpg.io "$PKE_DB_CLUSTER" \
  -o jsonpath='{.metadata.generation}{"\n"}{.status.conditions}{"\n"}'
pke_psql <<'SQL'
SELECT pg_postmaster_start_time();
SELECT name, setting, source, pending_restart FROM pg_settings
WHERE name IN ('shared_preload_libraries', 'pgroonga.enable_wal',
               'pgroonga.enable_wal_resource_manager', 'pgroonga.enable_crash_safe');
SQL
```

`shared_preload_libraries` にresource managerがあり、`enable_wal_resource_manager=on`、`enable_wal=off`、`enable_crash_safe=off` となることを確認する。
PGroongaのGUCが表示されない接続では `LOAD 'pgroonga';` 後に再確認する。
`pending_restart` がなく、アプリもまだ停止していることを確認してから修復へ進む。

最終的な設定変更をGitにも残し、[CNPG.md](CNPG.md) にWAL方式と復元先にも必要なpreload設定を反映する。
Conventional Commitsを使い、対象クラスタのKustomize buildと適用されるCIを確認する。
Gitへの反映は実行者の承認範囲に従う。
Fluxが参照するリモートのrevisionに変更が入るまで、MisskeyのKustomizationは再開しない。

## 4. 不要領域を測定して再構築方法を選ぶ

設定適用と再起動の後も、アプリ停止とautovacuum停止が維持されていることを確認する。

```sh
pke_psql <<'SQL'
SHOW autovacuum;
SELECT pid, datname, backend_type, state
FROM pg_stat_activity WHERE backend_type = 'autovacuum worker';
SQL
```

容量と統計を保存する。
`n_live_tup` と `reltuples` は推定値で、リストア後に食い違う場合がある。
ANALYZEで件数統計を更新しても、不要領域を測定したことにはならない。

```sh
pke_psql <<'SQL' > "$PKE_MAINT_DIR/sizes-before-rebuild.txt"
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;
SELECT c.oid::regclass AS relation, c.reltuples::bigint AS estimated_rows,
       pg_size_pretty(pg_table_size(c.oid)) AS table_size,
       pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 12;
SQL
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec "$PKE_DB_POD" -c postgres -- df -h /var/lib/postgresql/data \
  > "$PKE_MAINT_DIR/df-before-rebuild.txt"
pke_psql -c 'ANALYZE;'
```

`pgstattuple` が未導入なら、保全完了後に診断用として追加する。
今回追加したかを記録し、作業後に不要なら `DROP EXTENSION pgstattuple;` で外す。
既存の拡張は削除しない。

```sh
pke_psql -c 'CREATE EXTENSION IF NOT EXISTS pgstattuple;'
pke_psql <<'SQL' > "$PKE_MAINT_DIR/bloat-measurement.txt"
SELECT c.oid::regclass AS relation, s.*
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL pgstattuple_approx(c.oid::regclass) s
WHERE n.nspname = 'public'
  AND c.relname IN ('note', 'drive_file', '__chart__per_user_notes',
                   '__chart__instance', '__chart_day__per_user_notes');
SELECT c.oid::regclass AS relation, c.reltoastrelid::regclass AS toast_relation,
       s.*
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL pgstattuple_approx(c.reltoastrelid::regclass) s
WHERE n.nspname = 'public' AND c.reltoastrelid <> 0
  AND c.relname IN ('note', 'drive_file', '__chart__per_user_notes',
                   '__chart__instance', '__chart_day__per_user_notes');
SQL
```

`dead_tuple_len` と `approx_free_space` を不要領域の目安にする。
これらの合計がFULLで必ず返る量とは限らず、通常運用で再利用する余白も含む。
大きな解放が見込める場合は `pgstattuple('対象テーブル'::regclass)` で精査し、対象、見込む解放量、空き容量、残りの停止時間を決める。
FULLは新しいテーブルと全インデックス、PGroongaファイル、WALの増加分が同時に必要になるため、heapだけの容量で判断しない。
診断に失敗した場合はFULLの自動選択をせず、インデックス修復と通常VACUUMを優先する。

### noteをFULL対象にしない場合

PGroongaの内部名を記録してから、対象インデックスを通常のREINDEXで再構築する。
この時点ではアプリが停止しているため、`CONCURRENTLY` は付けない。

```sh
pke_psql -Atc "SELECT pgroonga_table_name('public.idx_note_text_with_pgroonga');" \
  > "$PKE_MAINT_DIR/pgroonga-source-before.txt"
pke_psql <<'SQL'
SET lock_timeout = '30s';
SET statement_timeout = 0;
REINDEX (VERBOSE) INDEX public.idx_note_text_with_pgroonga;
SQL
```

### noteをFULL対象にする場合

FULLによるテーブル書き換えではインデックスも再構築される。
上の独立したREINDEXを重ねて実行せず、先にFULLを実施して再構築結果を確認する。
内部名の保存は上と同じコマンドで行う。

```sh
pke_psql <<'SQL'
SET lock_timeout = '30s';
SET statement_timeout = 0;
VACUUM (FULL, ANALYZE, VERBOSE) public.note;
SQL
```

note以外のFULL対象も、選んだテーブルを明示して1つずつ処理する。
FULLやREINDEXが失敗した場合はエラーを保全し、実行中のbackendと空き容量を確認する。
接続が切れてもサーバー側の処理が続いている場合があるため、状態を確認せず同じ処理を再実行しない。

別の接続から進捗とアプリ停止状態を確認する。

```sh
pke_psql <<'SQL'
SELECT pid, command, relid::regclass, index_relid::regclass, phase,
       blocks_done, blocks_total, tuples_done, tuples_total
FROM pg_stat_progress_create_index;
SELECT pid, relid::regclass, command, phase,
       heap_blks_scanned, heap_blks_total
FROM pg_stat_progress_cluster;
SQL
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec "$PKE_DB_POD" -c postgres -- df -h /var/lib/postgresql/data
```

## 5. 古いPGroonga領域を整理して検索を確認する

通常VACUUMと統計更新を実行し、PGroonga内部に残った旧オブジェクトも整理する。
REINDEX直後には古い内部オブジェクトが残ることがある。
通常VACUUMもこの整理を行うが、明示的な `pgroonga_vacuum()` の成功まで確認する。
`pgrn*` や `pg_wal` のファイルを手動削除しない。

```sh
pke_psql -c 'VACUUM (ANALYZE, INDEX_CLEANUP ON);'
pke_psql -c 'SELECT pgroonga_vacuum();'
pke_psql <<'SQL' > "$PKE_MAINT_DIR/sizes-after-rebuild.txt"
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;
SELECT c.oid::regclass AS relation, c.reltuples::bigint AS estimated_rows,
       pg_size_pretty(pg_table_size(c.oid)) AS table_size,
       pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 12;
SELECT c.relname, pg_relation_filenode(c.oid) AS filenode,
       i.indisvalid, i.indisready, pg_get_indexdef(c.oid) AS definition
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.oid = 'public.idx_note_text_with_pgroonga'::regclass;
SELECT pgroonga_table_name('public.idx_note_text_with_pgroonga');
SQL
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec "$PKE_DB_POD" -c postgres -- df -h /var/lib/postgresql/data \
  > "$PKE_MAINT_DIR/df-after-rebuild.txt"
```

旧内部名と新内部名が異なることを確認する。
旧内部名について `pgroonga_command('object_exist', ARRAY['name', '記録した旧内部名'])` の結果がfalseになることを確認する。
GroongaコマンドのJSON応答は、先頭のreturn codeが0であることも確認する。
PostgreSQLからのSQL終了コード0だけではGroongaコマンドの成功を判定しない。
`indisvalid=true` だけでは内部データの健全性を保証しないため、検索も確認する。

```sh
pke_psql <<'SQL'
BEGIN READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL enable_seqscan = off;
EXPLAIN (COSTS OFF) SELECT id FROM public.note WHERE text &@~ 'ぎる' LIMIT 20;
SELECT id FROM public.note WHERE text &@~ 'ぎる' LIMIT 20;
SELECT id FROM public.note WHERE text &@~ 'くな' LIMIT 20;
COMMIT;
SQL
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  exec "$PKE_DB_POD" -c postgres -- \
  tail -n 100 /var/lib/postgresql/data/pgdata/pgroonga.log
```

実行計画がPGroongaインデックスを使い、decode、merge、flushエラーが新しく出ていないことを確認する。
検索対象本文は出力せず、結果のIDだけを使う。
本番で確認用のINSERT、UPDATE、DELETEは実行しない。
不要領域の整理量は、DB全体、テーブル別、Podの `df` をそれぞれ比較する。
`sizes-before-rebuild.txt` と `sizes-after-rebuild.txt`、`df-before-rebuild.txt` と `df-after-rebuild.txt` を比較する。
テーブル別の結果は容量順なので、行位置ではなくrelation名で対応づける。
通常VACUUMで再利用可能になった領域が、すべてOSへ返ることは期待しない。

## 6. 新しい物理バックアップを取得する

アプリ停止、autovacuum停止、手動メンテナンス完了の状態を維持する。
PGroongaを更新する接続がないことを再確認し、flush後からBackup完了まで検索の追加操作も控える。
診断用拡張を今回だけ追加した場合は、このバックアップを取る前に削除する。

```sh
pke_psql -c "SELECT pgroonga_command('io_flush');"
```

JSON応答のreturn code=0と結果=trueを確認する。
flushに失敗した場合はバックアップ成功として扱わず、再開へ進まない。

```sh
PKE_BACKUP_NAME="${PKE_DB_CLUSTER}-maintenance-$(date -u +%Y%m%d%H%M%S)"
kubectl cnpg backup "$PKE_DB_CLUSTER" --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  --method=plugin --plugin-name=barman-cloud.cloudnative-pg.io \
  --backup-name="$PKE_BACKUP_NAME"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get backups.postgresql.cnpg.io "$PKE_BACKUP_NAME" \
  -o jsonpath='{.status.phase}{"\n"}{.status.backupId}{"\n"}{.status.beginWal}{"\n"}{.status.endWal}{"\n"}{.status.startedAt}{"\n"}{.status.stoppedAt}{"\n"}'
```

`status.phase=completed` を確認するまで、アプリとautovacuumを再開しない。
失敗した場合はpluginログとR2の保存状況を確認して原因を解消し、別名のBackupで再取得する。
「リソースが作られた」「WALが送られた」だけで完了としない。
取得後にBackup名、backup ID、WAL範囲、完了状態を保全ディレクトリへ記録する。
Barmanのbackup IDを次のコマンドで保存する。
IDが空の場合はpluginログとR2で確認し、IDを特定できるまで復元検証へ進まない。

```sh
PKE_BACKUP_ID="$(kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get backups.postgresql.cnpg.io "$PKE_BACKUP_NAME" \
  -o jsonpath='{.status.backupId}')"
test -n "$PKE_BACKUP_ID"
printf '%s\n' "$PKE_BACKUP_ID" > "$PKE_MAINT_DIR/backup-id.txt"
```

R2の確認は [CNPG.md](CNPG.md#バックアップの成否を確認する) に従い、認証情報を出力しない。

## 7. 通常運用とFlux管理へ戻す

一時設定のautovacuumを元へ戻す。
作業前にparametersへ明示されていなかった場合は、次のpatchで一時キーを削除する。
明示されていた場合は保存した値へ戻す。

```sh
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  patch clusters.postgresql.cnpg.io "$PKE_DB_CLUSTER" --type merge \
  -p '{"spec":{"postgresql":{"parameters":{"autovacuum":null}}}}'
pke_psql -c 'SHOW autovacuum;'
```

WAL resource manager設定がリモートGitへ反映済みであることを確認する。
まずrootを再開し、MisskeyのKustomizationとHelmReleaseがまだ停止していることを確認する。
次にMisskeyのKustomizationだけを再開して、移行後のCluster定義を適用する。

```sh
flux resume kustomization flux-system --context "$PKE_CONTEXT" -n flux-system
flux reconcile kustomization flux-system --with-source \
  --context "$PKE_CONTEXT" -n flux-system
kubectl --context "$PKE_CONTEXT" -n flux-system \
  get kustomizations.kustomize.toolkit.fluxcd.io misskey \
  -o jsonpath='{.spec.suspend}{"\n"}'
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  get helmreleases.helm.toolkit.fluxcd.io misskey \
  -o jsonpath='{.spec.suspend}{"\n"}'
flux resume kustomization misskey --context "$PKE_CONTEXT" -n flux-system
```

MisskeyのKustomizationは `wait: true` のため、webを止めている間はReadyにならない場合がある。
Ready待ちによるタイムアウトとCluster設定の適用を区別する。
Clusterのgeneration、preload設定、新しい接続でのGUCを照合し、古いGit設定へ戻っていないことを確認する。
Gitのdesired replica数が保存した値と一致していることも確認する。

```sh
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  scale deployment misskey-web --replicas="$PKE_WEB_REPLICAS"
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  rollout status deployment/misskey-web --timeout=10m
flux resume helmrelease misskey --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE"
flux reconcile kustomization misskey --context "$PKE_CONTEXT" -n flux-system
kubectl --context "$PKE_CONTEXT" -n "$PKE_NAMESPACE" \
  patch scheduledbackups.postgresql.cnpg.io "$PKE_SCHEDULE" --type merge \
  -p '{"spec":{"suspend":false}}'
kubectl --context "$PKE_CONTEXT" -n flux-system \
  annotate fluxinstance flux fluxcd.controlplane.io/reconcile=enabled --overwrite
```

作業開始時にすでに停止していたリソースは、上のresumeやpatchの対象から外す。
FluxInstanceのannotationも保存値へ戻す。開始時にannotationがなかった場合は追加したキーを削除する。
FluxInstanceの再調整停止はFlux controllers自体の停止ではなく、他アプリの既存Kustomizationは動作を続ける。
FluxのReadyに加えて、rootとMisskeyの `status.lastAppliedRevision` が移行設定を含むrevisionであること、`status.observedGeneration` が `metadata.generation` と一致することを確認する。
ClusterはCNPG statusと実際のPostgreSQL設定で確認し、statusに存在しないobservedGenerationを完了条件にしない。

Misskeyの通常利用による投稿保存と検索を確認し、新しいPGroongaエラーがないことを確認する。
監視ではDB collector、アプリ状態、WAL archiveの失敗と未送信量、新しいbase backupの最終成功を確認する。
Backup完了は実際の別Clusterへの復元成功を意味しない。

## 中断と失敗時の扱い

| 状況 | 対応 |
|---|---|
| 保全dump取得前の失敗 | 修復やFULLへ進まず、停止状態と接続元を確認する |
| dump取得またはarchive確認の失敗 | 不完全なdumpを保全成功とせず、原因を解消して再取得する |
| DB再起動失敗 | アプリ停止を維持し、PodのイベントとPostgreSQLログを確認する |
| REINDEXまたはFULLの失敗 | 実行中backend、ロック、容量を確認し、失敗ログを保全する |
| PGroonga内部DBの破損で再構築できない | 内部DB削除へ自動的に進まず、追加の復旧手順を判断する |
| flushまたは物理Backupの失敗 | アプリを再開せず、原因解消後にBackupを再取得する |
| Gitへの反映が未完了 | MisskeyのKustomizationを停止したまま維持し、設定が戻らない状態を保つ |
| 再開後も投稿が失敗 | 再度webを停止してログを保全し、検索とインデックス更新を調査する |

設定移行後にcustom WALを生成した場合、moduleを外して旧設定へ戻す操作を単純なロールバックとして扱わない。
復元側にもresource managerが必要であり、移行前のバックアップとの互換性やWAL再生範囲を確認する必要がある。
破損した既存インデックスへ戻すだけでは、投稿障害は解消しない。
現Cluster、PVC、バックアップprefixを削除せず、保全dumpの復元が必要なら別名Clusterを使う。

## 後続の復元検証

[CNPG.mdの復元手順](CNPG.md#base-backup-と-wal-から復元する)を基に、別名Clusterへ新しい物理バックアップを復元する。
その復元例に、節6の `backup-id.txt` に保存したBarmanのIDを `spec.bootstrap.recovery.recoveryTarget.backupID` として追加する。
KubernetesのBackupリソース名ではなく、`status.backupId` の値を指定する。
以下は追加する部分であり、placeholderを保存したIDへ置き換えてから復元先Clusterを作成する。

```yaml
spec:
  bootstrap:
    recovery:
      source: misskey-source
      recoveryTarget:
        backupID: "<節6で保存したBarmanのbackup ID>"
```

`source` は復元先の `externalClusters` の名前と一致させ、復元元の `serverName` は `misskey-cluster-restored` とする。
`backupID` を省略すると、復元目標の指定に応じて自動選択され、指定がなければ最新のバックアップが選ばれる。
検証までに定期バックアップが増えても、メンテナンス中に取得したものを起点にするためIDを固定する。
このIDは復元開始に使うbase backupを選ぶものであり、WAL再生の終了時点は別に決める。
選択仕様は [CNPGのRecoveryTarget](https://cloudnative-pg.io/docs/1.28/cloudnative-pg.v1/#recoverytarget) を参照する。

復元先にも対応するimageと `pgroonga_wal_resource_manager` のpreload設定を、WAL再生の開始前から用意する。
復元元のarchive prefixへ書き戻さず、Misskeyの接続先も切り替えない。

以下を確認する。

- Backup取得後のWALを再生できること。
- PGroongaインデックスを使った検索が成功すること。
- 隔離した復元先でINSERT、UPDATE、DELETEが成功すること。
- decode、merge、flushエラーがないこと。
- 書き込みを続けながら取得した定期バックアップからも復元できるか。その条件と未確認範囲。

復元先のSQL書き込み検証は本番とは分離して行う。
本番で故障や試験投稿を発生させない。
検証結果はPRや別の作業報告へ記載し、この文書に検証日時や障害の作業履歴を蓄積しない。

## 参照

- [PGroongaのWAL resource manager](https://pgroonga.github.io/reference/modules/pgroonga-wal-resource-manager.html)
- [PGroongaのWAL再生と物理コピーの条件](https://pgroonga.github.io/reference/streaming-replication-wal-resource-manager.html)
- [PGroongaの旧内部オブジェクト整理](https://pgroonga.github.io/reference/functions/pgroonga-vacuum.html)
- [PGroongaの旧WALの整理](https://pgroonga.github.io/reference/functions/pgroonga-wal-truncate.html)
- [PostgreSQLのVACUUM](https://www.postgresql.org/docs/18/sql-vacuum.html)
- [PostgreSQLの不要領域測定](https://www.postgresql.org/docs/18/pgstattuple.html)
- [CNPGのPostgreSQL設定](https://cloudnative-pg.io/docs/1.28/postgresql_conf/)
- [FluxInstanceの再調整制御](https://fluxoperator.dev/docs/crd/fluxinstance/#reconciliation-configuration)
