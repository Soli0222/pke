# CNPG 運用メモ

この文書は PKE で管理する CloudNativePG の構成、バックアップ、リストア手順をまとめる。
現行の CNPG `Cluster` は natsume クラスタにだけ存在する。

## Operator

CNPG operator は両クラスタに導入する。
manifest は `flux/clusters/<cluster>/apps/cnpg/` に置く。

| HelmRelease | Chart | Version | Namespace |
|-------------|-------|---------|-----------|
| `cnpg` | `cloudnative-pg` | `0.28.3` | `cnpg-system` |
| `plugin-barman-cloud` | `plugin-barman-cloud` | `0.7.0` | `cnpg-system` |

`plugin-barman-cloud` は misskey の WAL archive と base backup に使う。
pg_dump 方式のクラスタでは plugin を使わない。

## Backup Secrets

R2 backup 用の Secret は各 application namespace に `cnpg-backup-s3-secret` として作る。
Secret の実体は 1Password item `cnpg-backup-s3-secret` である。

| Key | 用途 |
|-----|------|
| `ACCESS_KEY_ID` | R2 の access key |
| `ACCESS_SECRET_KEY` | R2 の secret key |
| `ENDPOINT` | R2 の S3 compatible endpoint URL |

misskey の `ObjectStore` は `endpointURL: ${CNPG_BACKUP_ENDPOINT_URL}` を使う。
この値は `flux/clusters/natsume/apps/cnpg-backup-config/onepassworditem.yaml` が作る `cnpg-backup-flux-vars` から Flux `postBuild.substituteFrom` で注入する。

## Backup Modes

PKE では 2 種類の backup を使う。
書き込み量が多く、PITR が必要な misskey は WAL archive と base backup を使う。
それ以外の小規模 DB は `pg_dump -Fc` の日次 dump を使う。

### WAL Archive と Base Backup

対象は `misskey-cluster` だけである。
namespace `misskey` に次の manifest を置く。

| File | 役割 |
|------|------|
| `cluster.yaml` | `plugins[]` で `barman-cloud.cloudnative-pg.io` を WAL archiver として参照する |
| `objectstore.yaml` | R2 の destination、credential、retention、compression を定義する |
| `scheduledbackup.yaml` | 日次 base backup を作る |
| `onepassworditem-cnpg-backup.yaml` | `cnpg-backup-s3-secret` を作る |

`objectstore.yaml` の retention は `7d` である。
WAL と base backup は gzip 圧縮する。
WAL upload は `maxParallel: 4` で動く。

`scheduledbackup.yaml` の schedule は `0 30 16 * * *` である。
CNPG の schedule は UTC で評価されるため、これは 01:30 JST に相当する。

### pg_dump CronJob

対象は `grafana-cluster`、`sui-cluster`、`reblend-cluster`、`spn-cluster` である。
各 namespace に次の manifest を置く。

| File | 役割 |
|------|------|
| `cluster.yaml` | plugin なしの CNPG cluster を定義する |
| `onepassworditem-cnpg-backup.yaml` | `cnpg-backup-s3-secret` を作る |
| `cronjob-pg-dump.yaml` | `pg_dump -Fc` を R2 に upload する |

CronJob は `postgres:18.4-alpine3.23` を使う。
`spec.timeZone: Asia/Tokyo` を指定しているため、schedule は JST として評価される。
dump は `--no-owner --no-privileges` を付けて作る。
retention は `RETENTION_DAYS=7` で、古い object は同じ Job 内で削除する。

## S3 Layout

R2 bucket は `s3://cnpg-backup/` である。

```text
s3://cnpg-backup/<cluster-name>/base/<backup-id>/                   # WAL archive 方式の base backup
s3://cnpg-backup/<cluster-name>/wals/                               # WAL archive 方式の WAL
s3://cnpg-backup/<cluster-name>/<cluster-name>-YYYYMMDD-HHMMSS.dump # pg_dump 方式の dump
```

pg_dump の timestamp は UTC で作る。
CronJob の schedule は JST だが、object name は `date -u` で決まる。

過去に barman layout を使っていた cluster を pg_dump 方式へ切り替えた場合、不要な `base/` と `wals/` は手で消す。

```bash
aws s3 rm --endpoint-url "$AWS_ENDPOINT_URL" --recursive "s3://cnpg-backup/<cluster>/base/"
aws s3 rm --endpoint-url "$AWS_ENDPOINT_URL" --recursive "s3://cnpg-backup/<cluster>/wals/"
```

## Cluster 共通設定

現行の CNPG `Cluster` はすべて `instances: 1` である。
すべての `Cluster` に `primaryUpdateMethod: switchover` と `smartShutdownTimeout: 60` を入れる。

`instances: 1` では replica への switchover は発生しない。
それでも設定を明示しておくことで、replica を追加した場合の upgrade 方針を同じ manifest 上に残せる。

`smartShutdownTimeout: 60` は idle connection が長く残って restart が止まる時間を短くするための値である。
アプリ側の retry は別に必要であり、CNPG だけで接続断を吸収できるわけではない。

Pooler CRD は使っていない。
アプリは CNPG の `-rw`、`-r` service に直接接続する。

## 現在の Cluster

| アプリ | Namespace | Cluster | DB | Owner | Node | Storage | Backup |
|--------|-----------|---------|----|-------|------|---------|--------|
| misskey | `misskey` | `misskey-cluster` | `misskey` | `misskey` | `natsume-03` | `topolvm`, `150Gi` | WAL archive と base backup |
| grafana | `grafana` | `grafana-cluster` | `grafana` | `grafana` | `natsume-08` | `topolvm`, `10Gi` | pg_dump |
| sui | `sui` | `sui-cluster` | `sui` | `sui` | `natsume-08` | `topolvm`, `5Gi` | pg_dump |
| spotify-reblend | `spotify-reblend` | `reblend-cluster` | `reblend` | `reblend` | `natsume-08` | `topolvm`, `5Gi` | pg_dump |
| spotify-nowplaying | `spotify-nowplaying` | `spn-cluster` | `spn` | `spn` | `natsume-08` | `topolvm`, `5Gi` | pg_dump |

`misskey-cluster` は `ghcr.io/soli0222/pgroonga-cnpg/4.0.6-alpine:18` を使う。
pgroonga を使う restore 先も同じ拡張を含む image にそろえる。

## Backup Schedule

| Cluster | 方式 | Schedule | Timezone | 接続先 |
|---------|------|----------|----------|--------|
| `misskey-cluster` | ScheduledBackup | `0 30 16 * * *` | UTC | barman cloud plugin |
| `grafana-cluster` | CronJob | `0 3 * * *` | Asia/Tokyo | `grafana-cluster-r` |
| `sui-cluster` | CronJob | `5 3 * * *` | Asia/Tokyo | `sui-cluster-r` |
| `reblend-cluster` | CronJob | `10 3 * * *` | Asia/Tokyo | `reblend-cluster-r` |
| `spn-cluster` | CronJob | `15 3 * * *` | Asia/Tokyo | `spn-cluster-r` |

`misskey-cluster` の `0 30 16 * * *` は 01:30 JST に相当する。
pg_dump は 03:00 から 5 分間隔で分散する。

## 手動バックアップ

### misskey

`kubectl cnpg` plugin から plugin backup を作る。

```bash
kubectl cnpg backup misskey-cluster -n misskey \
  --backup-name "manual-$(date +%Y%m%d-%H%M%S)" \
  --method plugin
```

Backup CR を直接作ってもよい。

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: misskey-manual-20260620
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

`status.phase` が `completed` になれば完了である。
S3 上の path は `status.backupId` で確認する。

### pg_dump

CronJob から一時 Job を作る。

```bash
kubectl -n <namespace> create job \
  --from="cronjob/<cluster>-pg-dump" \
  "<cluster>-pg-dump-manual-$(date +%Y%m%d-%H%M%S)"
kubectl -n <namespace> logs -f "job/<manual-job-name>"
```

grafana の例を示す。

```bash
kubectl -n grafana create job \
  --from=cronjob/grafana-cluster-pg-dump \
  grafana-cluster-pg-dump-manual-20260620
```

一時 Pod で手動 dump する場合は `postgres:18.4-alpine3.23` を使う。

```bash
kubectl -n <namespace> run pgdump-oneshot --rm -it \
  --image=postgres:18.4-alpine3.23 \
  --restart=Never -- /bin/sh
```

Pod 内では `<cluster>-app` Secret の値を使い、`pg_dump -Fc --no-owner --no-privileges` で dump を作る。

## misskey のリストア

WAL archive 方式では、新しい `Cluster` を `bootstrap.recovery` で作り、R2 の base backup と WAL から復元する。
既存 cluster を直接上書きせず、別名で復元して確認してからアプリの接続先を切り替える。

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: misskey-cluster-restored
  namespace: misskey
spec:
  instances: 1
  imageName: ghcr.io/soli0222/pgroonga-cnpg/4.0.6-alpine:18
  primaryUpdateMethod: switchover
  smartShutdownTimeout: 60
  storage:
    storageClass: topolvm
    size: 150Gi
  bootstrap:
    recovery:
      source: misskey-cluster-source
      recoveryTarget:
        targetTime: "<UTC target time>"
        targetInclusive: true
  externalClusters:
  - name: misskey-cluster-source
    plugin:
      name: barman-cloud.cloudnative-pg.io
      parameters:
        barmanObjectName: misskey-backup-store
        serverName: misskey-cluster
```

`serverName` は S3 上の cluster directory 名に合わせる。
PITR しない場合は `recoveryTarget` を省略する。
`recoveryTarget` は `targetTime`、`targetLSN`、`targetXID`、`targetName`、`targetImmediate` のいずれか一つだけを指定する。
retention `7d` を超えて必要な WAL が消えている場合、PITR は失敗する。

## pg_dump からのリストア

pg_dump 方式に PITR はない。
最新または指定した dump から logical restore する。

### 1. dump を取得する

```bash
export AWS_ACCESS_KEY_ID=<ACCESS_KEY_ID>
export AWS_SECRET_ACCESS_KEY=<ACCESS_SECRET_KEY>
export AWS_ENDPOINT_URL=<ENDPOINT>

aws s3 ls --endpoint-url "$AWS_ENDPOINT_URL" "s3://cnpg-backup/<cluster-name>/"
aws s3 cp --endpoint-url "$AWS_ENDPOINT_URL" \
  "s3://cnpg-backup/<cluster-name>/<cluster-name>-YYYYMMDD-HHMMSS.dump" \
  ./restore.dump
```

`ACCESS_KEY_ID`、`ACCESS_SECRET_KEY`、`ENDPOINT` は `cnpg-backup-s3-secret` の元になっている 1Password item から取れる。

### 2. 空の Cluster を作る

同名で作り直すか、別名で復元する。
同名で作る場合は `bootstrap.initdb.database` と `bootstrap.initdb.owner` を元の値にそろえる。
同じ値にしておけば `<cluster>-app` Secret の DSN を使うアプリは接続設定を変えずに済む。

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: grafana-cluster
  namespace: grafana
spec:
  instances: 1
  primaryUpdateMethod: switchover
  smartShutdownTimeout: 60
  storage:
    storageClass: topolvm
    size: 10Gi
  bootstrap:
    initdb:
      database: grafana
      owner: grafana
```

### 3. dump を流し込む

`pg_dump -Fc` の dump は `pg_restore` で復元する。

```bash
kubectl -n <namespace> port-forward "svc/<cluster-name>-rw" 5432:5432
```

別の shell で Secret から接続情報を読む。

```bash
PGUSER="$(kubectl -n <namespace> get secret <cluster-name>-app -o jsonpath='{.data.username}' | base64 -d)"
PGPASSWORD="$(kubectl -n <namespace> get secret <cluster-name>-app -o jsonpath='{.data.password}' | base64 -d)"
PGDATABASE="$(kubectl -n <namespace> get secret <cluster-name>-app -o jsonpath='{.data.dbname}' | base64 -d)"

PGPASSWORD="$PGPASSWORD" pg_restore \
  -h localhost \
  -p 5432 \
  -U "$PGUSER" \
  -d "$PGDATABASE" \
  --no-owner \
  --no-privileges \
  -j 4 \
  ./restore.dump
```

restore 後はアプリ Pod を再起動し、接続を張り直す。
misskey を logical dump から戻す場合も、restore 先 image には pgroonga extension が必要である。

## CNPG 以外の PostgreSQL へ移行する場合

pg_dump は logical dump なので、同じ major version 以上の PostgreSQL に `pg_restore` できる。
ただし、pgroonga などの extension を使っている DB は、復元先にも同じ extension と必要な shared library が必要である。

## 参考

- [CloudNativePG Docs](https://cloudnative-pg.io/documentation/current/)
- [pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)
- [pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html)
