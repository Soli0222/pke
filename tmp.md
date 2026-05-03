# Misskey PostgreSQL / Redis 移行手順

## 前提

- 移行先 namespace は `misskey`。
- 移行先 PostgreSQL は CNPG `misskey-cluster`。
- 移行先 PostgreSQL Service は `misskey-cluster-rw:5432`。
- 移行先 Valkey Service は `misskey-valkey:6379`。
- PostgreSQL の database / user はどちらも `misskey`。
- Redis RDB は Redis ライセンス変更後の形式なので、Valkey への投入は `rdb-cli dump.rdb redis -h <valkey_host> -p <valkey_port>` を使う。
- Import 中は Misskey を起動しない。既存 Misskey 側も dump 取得前に停止、または書き込みを止める。

## 1. 移行先リソースを作る

Misskey の Flux Kustomization を反映する。

```bash
flux reconcile kustomization misskey -n flux-system --with-source
```

CNPG Cluster と Valkey が Ready になるまで待つ。

```bash
sudo kubectl get cluster -n misskey misskey-cluster
sudo kubectl get pods -n misskey
sudo kubectl get svc -n misskey
```

Misskey 本体が起動しないよう確認する。必要なら HelmRelease を suspend する。

```bash
flux suspend helmrelease misskey -n misskey
```

## 2. 既存 PostgreSQL から dump を取得する

既存 Misskey を停止、またはメンテナンス状態にして書き込みを止めてから取得する。

推奨は custom format。

```bash
pg_dump \
  -h <old_postgres_host> \
  -p <old_postgres_port> \
  -U <old_postgres_user> \
  -d <old_misskey_database> \
  -Fc \
  --no-owner \
  --no-acl \
  -f misskey.dump
```

plain SQL で取得済みの場合は `misskey.sql` として扱う。

## 3. 既存 Redis から RDB を取得する

既存 Redis ホスト上で `dump.rdb` を確保する。`SAVE` か `BGSAVE` で最新の RDB を吐かせ、`/var/lib/redis/dump.rdb` 等を作業ディレクトリにコピーしておく。

```bash
redis-cli -h 127.0.0.1 -p <old_redis_port> SAVE
cp /var/lib/redis/dump.rdb ./dump.rdb
```

取得後、既存側で書き込みが再開されていないことを確認する。

## 3.5 dump / rdb を natsume-03 へ転送する

dump/rdb を取得するホストと natsume-03 はどちらも sshd を停めているので、private VLAN (`192.168.9.0/24`) で L2 疎通している前提で netcat を使ってワンショット転送する。natsume-03 の private IP は `192.168.9.3` (ens7)。

netcat の方言差を避けるため、ここでは `ncat` (nmap-ncat) を使う前提で書く。素の `nc` (openbsd 版) でも `nc -l 5555` / `nc -N 192.168.9.3 5555` で同じ動作になる。

転送中の整合性確認のため、送信側であらかじめハッシュを取っておく。

```bash
# 送信側 (dump/rdb ホスト)
sha256sum misskey.dump dump.rdb
```

natsume-03 で受信側を起動する。同じポートを使い回すので、片方ずつ転送する。

```bash
# 受信側 (natsume-03)
ncat -l --recv-only 192.168.9.3 5555 > misskey.dump
```

別ホストから送信する。

```bash
# 送信側 (dump/rdb ホスト)
ncat --send-only 192.168.9.3 5555 < misskey.dump
```

完了したら受信側プロセスは自動で終了する。続けて RDB を同じ手順で送る。

```bash
# 受信側 (natsume-03)
ncat -l --recv-only 192.168.9.3 5555 > dump.rdb

# 送信側 (dump/rdb ホスト)
ncat --send-only 192.168.9.3 5555 < dump.rdb
```

natsume-03 側で hash を照合し、送信側と一致することを確認する。

```bash
# natsume-03
sha256sum misskey.dump dump.rdb
```

進捗を見たい場合は送信側で `pv` を挟む。

```bash
pv misskey.dump | ncat --send-only 192.168.9.3 5555
```

ファイアウォールで 5555/tcp を natsume-03 の ens7 で受けられるようにしておくこと。以降の手順 (5. の `pg_restore` と 8. の `rdb-cli`) は natsume-03 上で実行する想定で、この `misskey.dump` / `dump.rdb` を入力に使う。

## 4. 移行先 CNPG primary を確認する

CNPG の primary Pod 名を取得する。

```bash
export DEST_CONTEXT="default"
export NAMESPACE="misskey"
export CLUSTER="misskey-cluster"
export DATABASE="misskey"
export OWNER="misskey"
export DEST_PRIMARY="$(
  sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" get cluster "$CLUSTER" \
    -o jsonpath='{.status.currentPrimary}'
)"
echo "$DEST_PRIMARY"
```

空の場合は Cluster がまだ Ready ではない。

疎通と extension を確認する。

```bash
sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c 'select version();'

sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c 'select extname from pg_extension order by extname;'
```

`pgroonga` が無い場合は作成する。

```bash
sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c 'create extension if not exists pgroonga;'
```

## 5. PostgreSQL dump を import する

app user ではなく CNPG primary Pod 内で `postgres` ユーザーとして restore する。app user で `--clean` 付き restore をすると、既存 object の owner / privilege によって失敗しやすい。

custom format の dump を import する。

```bash
sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" exec -i -c postgres "$DEST_PRIMARY" -- \
  pg_restore -U postgres -d "$DATABASE" --clean --if-exists --no-owner \
  < misskey.dump
```

plain SQL の場合は `psql` で投入する。

```bash
sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" exec -i -c postgres "$DEST_PRIMARY" -- \
  psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" \
  < misskey.sql
```

## 6. restored object の owner / privileges を修正する

`pg_restore --no-owner` で restore した object は `postgres` owner になりやすい。Misskey の app user が migration や通常処理で object を触れるよう、owner と権限を `misskey` に戻す。

```bash
sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" exec -i -c postgres "$DEST_PRIMARY" -- \
  psql -v ON_ERROR_STOP=1 -U postgres -d "$DATABASE" <<SQL
GRANT CONNECT, TEMPORARY ON DATABASE "$DATABASE" TO "$OWNER";
GRANT USAGE, CREATE ON SCHEMA public TO "$OWNER";
ALTER SCHEMA public OWNER TO "$OWNER";

DO \$\$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT schemaname, tablename
    FROM pg_tables
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', r.schemaname, r.tablename, '$OWNER');
    EXECUTE format('GRANT ALL PRIVILEGES ON TABLE %I.%I TO %I', r.schemaname, r.tablename, '$OWNER');
  END LOOP;

  FOR r IN
    SELECT sequence_schema, sequence_name
    FROM information_schema.sequences
    WHERE sequence_schema = 'public'
  LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', r.sequence_schema, r.sequence_name, '$OWNER');
    EXECUTE format('GRANT ALL PRIVILEGES ON SEQUENCE %I.%I TO %I', r.sequence_schema, r.sequence_name, '$OWNER');
  END LOOP;

  FOR r IN
    SELECT schemaname, viewname
    FROM pg_views
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER VIEW %I.%I OWNER TO %I', r.schemaname, r.viewname, '$OWNER');
    EXECUTE format('GRANT ALL PRIVILEGES ON TABLE %I.%I TO %I', r.schemaname, r.viewname, '$OWNER');
  END LOOP;

  FOR r IN
    SELECT schemaname, matviewname
    FROM pg_matviews
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', r.schemaname, r.matviewname, '$OWNER');
    EXECUTE format('GRANT ALL PRIVILEGES ON TABLE %I.%I TO %I', r.schemaname, r.matviewname, '$OWNER');
  END LOOP;

  FOR r IN
    SELECT n.nspname AS schema_name, t.typname AS type_name
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'public'
      AND t.typtype IN ('b', 'c', 'd', 'e', 'r')
      AND t.typrelid = 0
      AND t.typelem = 0
  LOOP
    EXECUTE format('ALTER TYPE %I.%I OWNER TO %I', r.schema_name, r.type_name, '$OWNER');
  END LOOP;

  FOR r IN
    SELECT n.nspname AS schema_name, p.proname AS function_name, p.oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
  LOOP
    EXECUTE format('ALTER FUNCTION %I.%I(%s) OWNER TO %I',
      r.schema_name,
      r.function_name,
      pg_get_function_identity_arguments(r.oid),
      '$OWNER'
    );
  END LOOP;
END
\$\$;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO "$OWNER";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO "$OWNER";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO "$OWNER";
SQL
```

import 後に件数と extension を軽く確認する。

```bash
sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c 'select count(*) from "user";'

sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c 'select extname from pg_extension order by extname;'

sudo kubectl --context "$DEST_CONTEXT" -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c '\dt+'
```

## 7. 移行先 Valkey に接続する

ローカルから port-forward する。

```bash
sudo kubectl port-forward -n misskey svc/misskey-valkey 16379:6379
```

別 terminal で空であることを確認する。

```bash
redis-cli -h 127.0.0.1 -p 16379 dbsize
```

既に key がある場合は、移行先を間違えていないことを確認してから消す。

```bash
redis-cli -h 127.0.0.1 -p 16379 flushall
```

## 8. Redis RDB を Valkey に import する

Redis ライセンス変更後の RDB なので、`rdb-cli` で Valkey に流し込む。

```bash
rdb-cli dump.rdb redis -h 127.0.0.1 -p 16379
```

投入後に key 数を確認する。

```bash
redis-cli -h 127.0.0.1 -p 16379 dbsize
redis-cli -h 127.0.0.1 -p 16379 info keyspace
```

## 9. Misskey を起動する

HelmRelease を suspend していた場合は再開する。

```bash
flux resume helmrelease misskey -n misskey
flux reconcile helmrelease misskey -n misskey
```

現在の values では `web.replicaCount: 0` なので、実際に Misskey を起動するタイミングで replica 数を増やす。

起動後に状態を確認する。

```bash
sudo kubectl get pods -n misskey
sudo kubectl logs -n misskey deploy/misskey --tail=200
```

## 10. 切り戻し方針

- 移行元 PostgreSQL と Redis は、移行先で Misskey の起動確認が終わるまで削除しない。
- 問題が出た場合は Misskey を停止し、DNS / Ingress / traffic を移行元へ戻す。
- 移行先 DB を作り直す場合は CNPG Cluster の PVC と Valkey PVC を削除する必要があるため、削除対象を必ず確認してから実行する。
