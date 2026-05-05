# CNPG (CloudNativePG) 運用メモ

このリポジトリで稼働している CNPG クラスタの構成と、バックアップ／リストアの手順をまとめる。

## 標準セットアップ

### オペレータ層 (`flux/clusters/natsume/apps/cnpg/`)

- **cloudnative-pg** Helm chart (`helmrelease-cnpg.yaml`) — オペレータ本体
- **plugin-barman-cloud** Helm chart (`helmrelease-plugin-barman-cloud.yaml`) — S3 バックアップ／復元用プラグイン

両方とも `cnpg-system` ネームスペースに常駐する。

### バックアップ用シークレット

- 各アプリ ns に `cnpg-backup-s3-secret` (1Password 由来) を `OnePasswordItem` で配置。キーは以下:
  - `ACCESS_KEY_ID` / `ACCESS_SECRET_KEY` — R2 のクレデンシャル
  - `ENDPOINT` — R2 の S3 互換エンドポイント URL

pg_dump CronJob はこれらを env 経由で参照する。misskey の `ObjectStore` は `endpointURL` に Flux の `postBuild.substituteFrom` で `cnpg-backup-flux-vars` (`CNPG_BACKUP_ENDPOINT_URL`) を注入する。

### バックアップ方式

クラスタごとに **2 通り** ある。書き込みが活発な `misskey` は WAL archive + base backup で PITR 可能、それ以外は日次の `pg_dump` (custom format) を S3 に投げる単純構成。

#### 方式 A: WAL archive + base backup (misskey)

namespace に4点セット:

1. `Cluster` — `plugins[]` に `barman-cloud.cloudnative-pg.io` を参照
2. `ObjectStore` (`objectstore.yaml`) — S3 接続情報、retention `7d`、gzip 圧縮
3. `ScheduledBackup` (`scheduledbackup.yaml`) — 日次 base backup、`method: plugin`
4. `OnePasswordItem` — S3 クレデンシャル (`cnpg-backup-s3-secret`)

#### 方式 B: pg_dump CronJob (grafana / sui / spotify-reblend / spotify-nowplaying)

namespace に3点セット:

1. `Cluster` — `plugins[]` 無し (=archive_mode off で起動)
2. `OnePasswordItem` — S3 クレデンシャル (`cnpg-backup-s3-secret`)
3. `CronJob` (`cronjob-pg-dump.yaml`) — 毎日深夜 JST に `pg_dump -Fc | aws s3 cp -` で R2 にアップロード、7日より古いオブジェクトを同 Job 内で削除

`CronJob.spec.timeZone: Asia/Tokyo` を明示しているため schedule は JST として解釈される。CNPG の `<cluster>-app` Secret から認証情報を取得し、`<cluster>-ro` Service 経由でレプリカから dump を取る (primary 負荷を避けるため)。

### S3 レイアウト

R2 バケット `s3://cnpg-backup/`:

```
s3://cnpg-backup/<cluster-name>/base/<backup-id>/                          # 方式A: base backup
s3://cnpg-backup/<cluster-name>/wals/                                      # 方式A: WAL
s3://cnpg-backup/<cluster-name>/<cluster-name>-YYYYMMDD-HHMMSS.dump        # 方式B: pg_dump
```

方式 B へ切り替え済みのクラスタに過去の barman レイアウト (`<cluster>/base/`, `<cluster>/wals/`) が残っている場合は手で消す:

```bash
aws s3 rm --endpoint-url $AWS_ENDPOINT_URL --recursive s3://cnpg-backup/<cluster>/base/
aws s3 rm --endpoint-url $AWS_ENDPOINT_URL --recursive s3://cnpg-backup/<cluster>/wals/
```

### Cluster 共通設定

全 Cluster に以下を入れている:

```yaml
spec:
  primaryUpdateMethod: switchover   # primary を再起動する代わりにレプリカへ切替えてから旧 primary を更新
  smartShutdownTimeout: 60          # smart shutdown の最大待ち時間 (秒)。default 180
```

`switchover` でローリング更新時の primary 切断回数を最小化、`smartShutdownTimeout: 60` で居座るアイドル接続が原因で再起動が止まる事故を回避する。アプリ側のドライバには別途リトライが必要 (CNPG だけでは完全には吸収できない)。

### Pooler

misskey 系は HTTP / WebSocket / queue worker / 通知配信が常時動いていて再起動の体感が大きいので、PgBouncer (`Pooler` CRD) を挟んでいる。

- `pooler.yaml` — `type: rw`、`poolMode: session`、`instances: 2`、podAntiAffinity あり
- アプリは `*-pooler-rw:5432` に接続 (HelmRelease の `externalPostgresql.host` で指定)
- session mode を選んでいる理由: TypeORM が `LISTEN/NOTIFY` や session 単位の状態を持つため transaction mode だと壊れる
- 再起動時は PgBouncer の `PAUSE` がアプリ ↔ pooler の TCP を保持したまま PG 再接続を待ってくれるので、トランザクション外であれば切断を体感されない

### 現在のクラスタ一覧

| アプリ | namespace | Cluster名 | instances | バックアップ方式 | Pooler | スケジュール |
|---|---|---|---|---|---|---|
| misskey | misskey | misskey-cluster | 2 | WAL archive + base | misskey-pooler-rw | `0 30 18 * * *` UTC = 03:30 JST |
| grafana | grafana | grafana-cluster | 2 | pg_dump | — | 03:00 JST |
| sui | sui | sui-cluster | 2 | pg_dump | — | 03:05 JST |
| spotify-reblend | spotify-reblend | reblend-cluster | 2 | pg_dump | — | 03:10 JST |
| spotify-nowplaying | spotify-nowplaying | spn-cluster | 2 | pg_dump | — | 03:15 JST |
| misskey-stg | misskey-stg | misskey-stg-cluster | 1 | バックアップ無し | misskey-stg-pooler-rw | — |

方式 A の `ScheduledBackup.spec.schedule` は CNPG オペレータが UTC で評価する (timeZone 指定不可)。方式 B の Kubernetes `CronJob` は `spec.timeZone` で TZ 明示可能。pg_dump 時刻は 5 分間隔で分散している。

---

## 手動バックアップ (misskey: WAL archive)

### kubectl cnpg プラグイン経由

```bash
kubectl cnpg backup misskey-cluster -n misskey \
  --backup-name manual-$(date +%Y%m%d-%H%M%S) \
  --method plugin
```

### Backup CR を直接作成

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: misskey-manual-20260505
  namespace: misskey
spec:
  cluster:
    name: misskey-cluster
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
```

```bash
kubectl apply -f backup.yaml
kubectl get backup -n misskey -w
```

`status.phase: completed` になれば完了。S3 上の path は `status.backupId` で確認できる。

---

## 手動バックアップ (pg_dump)

### CronJob を即時実行する

```bash
kubectl -n <namespace> create job --from=cronjob/<cluster>-pg-dump <cluster>-pg-dump-manual-$(date +%Y%m%d-%H%M%S)
kubectl -n <namespace> logs -f job/<cluster>-pg-dump-manual-...
```

例:

```bash
kubectl -n grafana create job --from=cronjob/grafana-cluster-pg-dump grafana-cluster-pg-dump-manual-20260505
```

### Pod に入って手で叩く

```bash
kubectl -n <namespace> run pgdump-oneshot --rm -it \
  --image=postgres:18.3-alpine3.23 --restart=Never -- /bin/sh
# 中で env を埋めて pg_dump -Fc -h <cluster>-ro -U <user> -d <db> > /tmp/x.dump
```

---

## CNPG クラスタへのリストア (misskey: WAL archive)

新しい `Cluster` を `bootstrap.recovery` で起動して S3 の base backup から復元する。既存クラスタを上書きするのではなく、別名で建てるのが基本。

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: misskey-cluster-restored
  namespace: misskey
spec:
  instances: 2
  imageName: ghcr.io/soli0222/pgroonga-cnpg/4.0.6-alpine:18
  storage:
    size: 150Gi
  bootstrap:
    recovery:
      source: misskey-cluster-source
      recoveryTarget:
        targetTime: "2026-05-05 02:30:00.000000+00:00"
        targetInclusive: true
  externalClusters:
  - name: misskey-cluster-source
    plugin:
      name: barman-cloud.cloudnative-pg.io
      parameters:
        barmanObjectName: misskey-backup-store
        serverName: misskey-cluster
```

ポイント:

- `serverName` は S3 上のサブディレクトリ名 = 元 Cluster 名
- `bootstrap.recovery` は新規クラスタにしか効かない
- PITR しない場合は `recoveryTarget` を省略する
- `recoveryTarget` のキー (`targetTime` / `targetLSN` / `targetXID` / `targetName` / `targetImmediate`) はどれか1つだけ指定する
- 復元対象の WAL が retention (`7d`) で消えていると失敗する
- 復元クラスタの動作確認後、アプリ側の接続先を新クラスタへ向ける

---

## pg_dump からのリストア

PITR は無く、最新 dump からの復元のみ。流れは「クラスタを作り直す → dump を流し込む」。

### 1. dump を取得

```bash
export AWS_ACCESS_KEY_ID=<ACCESS_KEY_ID>
export AWS_SECRET_ACCESS_KEY=<ACCESS_SECRET_KEY>
export AWS_ENDPOINT_URL=<ENDPOINT>

aws s3 ls --endpoint-url $AWS_ENDPOINT_URL s3://cnpg-backup/<cluster-name>/
aws s3 cp --endpoint-url $AWS_ENDPOINT_URL \
  s3://cnpg-backup/<cluster-name>/<cluster-name>-YYYYMMDD-HHMMSS.dump \
  ./restore.dump
```

1Password の `cnpg-backup-s3-secret` から `ACCESS_KEY_ID` / `ACCESS_SECRET_KEY` / `ENDPOINT` を取れる。

### 2. クラスタを作り直す (または別名で立てる)

`bootstrap.initdb` で空クラスタを起動するだけ。`owner` は **元と同じユーザー名・DB名** にすること。`<cluster-name>-app` Secret に同じ DSN が入るので、アプリの接続設定を変えずに済む。

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: grafana-cluster
  namespace: grafana
spec:
  instances: 2
  primaryUpdateMethod: switchover
  smartShutdownTimeout: 60
  storage:
    size: 10Gi
  bootstrap:
    initdb:
      database: grafana   # 元と同じ
      owner: grafana      # 元と同じ
```

### 3. dump を流し込む

`pg_dump -Fc` で取った dump は `pg_restore` で食わせる。

```bash
kubectl -n <namespace> port-forward svc/<cluster-name>-rw 5432:5432 &

PGUSER=$(kubectl -n <namespace> get secret <cluster-name>-app -o jsonpath='{.data.username}' | base64 -d)
PGPASSWORD=$(kubectl -n <namespace> get secret <cluster-name>-app -o jsonpath='{.data.password}' | base64 -d)
PGDATABASE=$(kubectl -n <namespace> get secret <cluster-name>-app -o jsonpath='{.data.dbname}' | base64 -d)

PGPASSWORD=$PGPASSWORD pg_restore \
  -h localhost -p 5432 -U $PGUSER -d $PGDATABASE \
  --no-owner --no-privileges \
  -j 4 \
  ./restore.dump
```

ポイント:

- `--no-owner --no-privileges` を付ける (dump 時にも付けているのでロール存在に依存しない)
- `-j 4` で並列復元 (custom format でのみ可能)
- ロールは `bootstrap.initdb.owner` で再作成済みなので別途投入不要
- 復元後はアプリ Pod を再起動して接続を張り直す
- misskey は `pgroonga` 拡張を使うので、復元先 Cluster の `imageName` も `ghcr.io/soli0222/pgroonga-cnpg/...` に揃える

### 別 PG (CNPG 不使用) への移行

dump は logical なので、任意の PG (同 major 以上) にそのまま `pg_restore` できる。pgroonga 等の拡張機能を使っている DB は、復元先にも同じ拡張がインストールされている必要がある (`shared_preload_libraries` の整合性)。

---

## 参考

- [CloudNativePG Docs](https://cloudnative-pg.io/documentation/current/)
- [CNPG Pooler / PgBouncer](https://cloudnative-pg.io/documentation/current/connection_pooling/)
- [pg_dump / pg_restore](https://www.postgresql.org/docs/current/app-pgdump.html)
