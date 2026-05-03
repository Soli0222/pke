# PostgreSQL dump を sudo kubectl cp で投入する手順

`sudo kubectl exec -i ... < misskey.dump` で stdin streaming すると、長時間 exec の途中で API server / kube-apiserver の TCP セッションが切れて pg_restore がコンテナ内で死亡し、クライアント側だけハングする事故が起きやすい。代わりにダンプを Pod 内に `sudo kubectl cp` で置いてから `pg_restore` をファイルから読ませる。stdin を使わないので exec stream が切れても restore 本体は生きる。

`tmp.md` の手順 1〜4 で CNPG Cluster が Ready、 `$DEST_PRIMARY` が取れている前提。`misskey.dump` は natsume-03 の作業ディレクトリにある前提。

## 0. 環境変数

```bash
export NAMESPACE="misskey"
export CLUSTER="misskey-cluster"
export DATABASE="misskey"
export OWNER="misskey"
export DEST_PRIMARY="$(
  sudo kubectl -n "$NAMESPACE" get cluster "$CLUSTER" \
    -o jsonpath='{.status.currentPrimary}'
)"
echo "$DEST_PRIMARY"
```

## 1. 事前チェック

データボリュームに dump 分の空きがあるか確認する。`-Fc` の dump はだいたい RDB の 1/4〜1/2 程度のサイズになる。

```bash
sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- df -h /var/lib/postgresql/data
ls -lh misskey.dump
```

ローカル dump の整合性を確認したい場合はハッシュを取っておく。

```bash
sha256sum misskey.dump
```

## 2. dump を Pod に転送する

データ PVC 上に置く。空きが足りないなら別の場所 (例: 別途 emptyDir / hostPath を用意) を検討する。

```bash
sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  mkdir -p /var/lib/postgresql/data/restore

sudo kubectl -n "$NAMESPACE" cp \
  ./misskey.dump \
  "$DEST_PRIMARY":/var/lib/postgresql/data/restore/misskey.dump \
  -c postgres
```

転送後にハッシュを照合する。

```bash
sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  sha256sum /var/lib/postgresql/data/restore/misskey.dump
```

## 3. pg_restore をコンテナ内で実行する

`-i` を付けない。stdin を使わないので exec stream が切れても pg_restore は止まらない。進捗を出すため `-v` を付ける。

```bash
sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  pg_restore -U postgres -d "$DATABASE" \
  --clean --if-exists --no-owner -v \
  /var/lib/postgresql/data/restore/misskey.dump
```

並列復元したい場合は `-j N` を付ける (custom format + ファイル入力なら使える)。 N は CPU と I/O の余裕に合わせる。並列化すると WAL 発生量が増えるので、PVC 容量と `archive_command` 状態を確認してから。

```bash
sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  pg_restore -U postgres -d "$DATABASE" \
  --clean --if-exists --no-owner -v -j 4 \
  /var/lib/postgresql/data/restore/misskey.dump
```

### exec stream が途中で切れた場合

`sudo kubectl exec` クライアント側がエラーで死んでも、Pod 内の `pg_restore` プロセスは生き続ける。状態確認は別 terminal から行う。

```bash
# プロセス確認
sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  bash -c 'ps -ef | grep -E "pg_restore|^postgres:" | grep -v grep'

# 接続確認
sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c \
  "select pid, state, wait_event, now()-query_start as age, substring(query,1,100) from pg_stat_activity where datname='$DATABASE' and pid <> pg_backend_pid();"
```

`pg_restore` プロセスがまだ居ればそのまま完了を待つ。死んでいたら `sudo kubectl exec ... pg_restore ...` をもう一度叩く (`--clean --if-exists` 付きなので冪等)。

## 4. import 確認

インデックスがすべて valid であること、件数が想定通りであることを確認する。

```bash
sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c "
    select count(*) filter (where indisvalid) as valid,
           count(*) filter (where not indisvalid) as invalid,
           count(*) filter (where not indisready) as not_ready
    from pg_index;"

sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c "
    select 'tables'   as kind, count(*) from pg_tables   where schemaname='public'
    union all select 'fkeys',    count(*) from pg_constraint where contype='f'
    union all select 'pkeys',    count(*) from pg_constraint where contype='p'
    union all select 'triggers', count(*) from pg_trigger    where not tgisinternal
    union all select 'matviews', count(*) from pg_matviews   where schemaname='public';"

sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  psql -U postgres -d "$DATABASE" -c 'select count(*) from "user";'
```

移行元 (natsume-01) でも同じクエリを流して件数を突き合わせる。

## 5. dump ファイルを削除する

データ PVC を圧迫し続けないよう、確認が終わったら消す。

```bash
sudo kubectl -n "$NAMESPACE" exec -c postgres "$DEST_PRIMARY" -- \
  rm -rf /var/lib/postgresql/data/restore
```

この後は `tmp.md` の手順 6 (owner / privileges 修正) に戻る。
