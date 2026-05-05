# CNPG (CloudNativePG) 運用メモ

このリポジトリで稼働している CNPG クラスタの構成と、バックアップ／リストアの手順をまとめる。

## 標準セットアップ

### オペレータ層 (`flux/clusters/natsume/apps/cnpg/`)

- **cloudnative-pg** Helm chart (`helmrelease-cnpg.yaml`) — オペレータ本体
- **plugin-barman-cloud** Helm chart (`helmrelease-plugin-barman-cloud.yaml`) — S3バックアップ／復元用プラグイン

両方とも `cnpg-system` ネームスペースに常駐する。

### バックアップ用シークレット

- `flux/clusters/natsume/apps/cnpg-backup-config/` — 1Password から `cnpg-backup-flux-vars` Secret を `flux-system` ns に取得し、`CNPG_BACKUP_ENDPOINT_URL` を Flux Kustomization の `postBuild.substituteFrom` で各アプリに注入
- 各アプリ ns には `cnpg-backup-s3-secret` (1Password 由来、`ACCESS_KEY_ID` / `ACCESS_SECRET_KEY`) を `OnePasswordItem` で配置

各アプリの Kustomization は以下を `dependsOn` する:

```yaml
dependsOn:
- name: cnpg
- name: cnpg-backup-config
```

### アプリごとの構成

各 namespace に4点セット:

1. `Cluster` (postgresql.cnpg.io/v1) — `plugins[]` に `barman-cloud.cloudnative-pg.io` を参照
2. `ObjectStore` (barmancloud.cnpg.io/v1) — S3 接続情報、retention `30d`、gzip 圧縮
3. `ScheduledBackup` — 日次 base backup、`method: plugin`
4. `OnePasswordItem` — S3 クレデンシャル

### S3 レイアウト

共有バケット `s3://cnpg-backup/` にクラスタ名サブディレクトリで分かれて配置される:

```
s3://cnpg-backup/<cluster-name>/base/<backup-id>/   # base backup
s3://cnpg-backup/<cluster-name>/wals/               # WAL (現在は未使用)
```

### WAL アーカイブ方針

**全クラスタで `isWALArchiver: false`**。理由:

- 各クラスタは `instances: 2` で streaming replication が効いているため、レプリカブートストラップ用の WAL アーカイブは不要
- PITR 要件は無く、日次 base backup で「最大24時間ぶんのロス」が許容できる用途のみ

PITR が必要になったクラスタが出てきたら、当該 `Cluster` の `plugins[].isWALArchiver` を `true` に戻す。

### 現在のクラスタ一覧

`ScheduledBackup.spec.schedule` は CNPG オペレータが UTC で評価する。下表は **UTC 表記 / JST 換算 (= UTC + 9h)** を併記している。

| アプリ | namespace | Cluster名 | instances | schedule (UTC) | JST |
|---|---|---|---|---|---|
| misskey | misskey | misskey-cluster | 2 | `0 30 18 * * *` | 03:30 |
| grafana | grafana | grafana-cluster | 2 | `0 20 18 * * *` | 03:20 |
| sui | sui | sui-cluster | 2 | `0 0 18 * * *` | 03:00 |
| spotify-nowplaying | spotify-nowplaying | spn-cluster | 2 | `0 20 18 * * *` | 03:20 |
| spotify-reblend | spotify-reblend | reblend-cluster | 2 | `0 10 18 * * *` | 03:10 |
| misskey-stg | misskey-stg | misskey-stg-cluster | 1 | (バックアップ無し) | — |

---

## 手動バックアップ

### kubectl cnpg プラグイン経由 (推奨)

```bash
kubectl cnpg backup <cluster-name> -n <namespace> \
  --backup-name manual-$(date +%Y%m%d-%H%M%S) \
  --method plugin
```

例:

```bash
kubectl cnpg backup misskey-cluster -n misskey \
  --backup-name manual-20260505 \
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

### 進捗・結果の確認

```bash
kubectl get backup -n <namespace>
kubectl describe backup <backup-name> -n <namespace>
```

`status.phase: completed` になれば完了。S3 上の path は `status.backupId` で確認できる。

---

## CNPG クラスタへのリストア (S3 から)

新しい `Cluster` を `bootstrap.recovery` で起動して S3 のbase backupから復元する。**既存クラスタを上書きするのではなく、別名で建てる** のが基本。

### 手順

1. 復元元の `ObjectStore` (または `Backup` リソース) を新クラスタの ns に用意（あるいは元の ns に作る）
2. 新しい `Cluster` を作る:

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
  externalClusters:
  - name: misskey-cluster-source
    plugin:
      name: barman-cloud.cloudnative-pg.io
      parameters:
        barmanObjectName: misskey-backup-store
        serverName: misskey-cluster   # ← 元クラスタ名
```

ポイント:

- `serverName` は **S3 上のサブディレクトリ名 = 元 Cluster 名** を指定
- `bootstrap.recovery` は新規クラスタにしか効かない (起動済みクラスタは bootstrap を再実行しない)
- 特定の base backup を選びたいときは `bootstrap.recovery.backup.name` で `Backup` リソースを参照
- WAL アーカイブが無いので `recoveryTarget` (PITR) は使えない。base backup の完了時点まで戻る

### 切替

復元クラスタの動作確認が済んだら、Service や DSN を新クラスタへ向け直す（または旧クラスタを削除して新クラスタを正規名にリネームする運用は CNPG の機能では難しいので、**アプリ側の接続先を切り替える** のが現実的）。

---

## 非 CNPG (素の PostgreSQL) へのリストア

barman-cloud のbase backupは pg_basebackup と同じ tar 形式なので、CNPG なしの PostgreSQL に展開できる。

### 必要なもの

- `barman-cli-cloud` パッケージ (Debian/Ubuntu: `barman-cli-cloud`、macOS: `pip install barman-cloud`)
- S3 アクセスキー、エンドポイント、バケット名

### 手順

#### 1. S3 認証を環境変数に

```bash
export AWS_ACCESS_KEY_ID=<ACCESS_KEY_ID>
export AWS_SECRET_ACCESS_KEY=<ACCESS_SECRET_KEY>
export AWS_ENDPOINT_URL=<CNPG_BACKUP_ENDPOINT_URL>
```

1Password の `cnpg-backup-s3-secret` および `cnpg-backup-flux-vars` から取得する。

#### 2. バックアップ一覧を確認

```bash
barman-cloud-backup-list \
  --endpoint-url $AWS_ENDPOINT_URL \
  s3://cnpg-backup misskey-cluster
```

`<server>` 部分は **元の Cluster 名**。

#### 3. base backup を取得・展開

```bash
mkdir -p /var/lib/postgresql/restore
barman-cloud-restore \
  --endpoint-url $AWS_ENDPOINT_URL \
  s3://cnpg-backup misskey-cluster \
  <backup-id>  \
  /var/lib/postgresql/restore
```

`<backup-id>` は前ステップの一覧で得られる ID (例: `20260504T033000`) または `latest`。

展開後は素の `PGDATA` ディレクトリ構造になっている。

#### 4. 起動

CNPG 由来の設定ファイル (`postgresql.auto.conf` の replication 設定、`recovery.signal` 等) が残っている場合は除去:

```bash
cd /var/lib/postgresql/restore
rm -f recovery.signal standby.signal
# postgresql.auto.conf を確認、cnpg関連の primary_conninfo 等は削除
```

PostgreSQL バージョンに合わせた `pg_ctl` または systemd で起動:

```bash
pg_ctl -D /var/lib/postgresql/restore start
```

初回起動時は WAL リプレイが走り、base backup 完了時点の状態で立ち上がる。

#### 5. データ抜き出し (論理ダンプで他DBへ移すパターン)

起動できたら通常の `pg_dump` で論理ダンプが取れる:

```bash
pg_dump -h localhost -U postgres -Fc misskey > misskey.dump
pg_restore -h <new-host> -U <user> -d <newdb> misskey.dump
```

### 注意

- **PostgreSQL のメジャーバージョンを揃える** こと。base backup は物理レプリケーション形式なので異バージョン間で起動できない（`pg_dump` を介せば論理移行は可能）
- pgroonga 等の拡張機能を使っているクラスタ (misskey) は、復元先にも同じ拡張がインストールされている必要がある (`shared_preload_libraries` の整合性)
- 復元中はネットワーク帯域を使うので、本番S3エンドポイントの帯域に注意

---

## 参考

- [CloudNativePG Docs - Recovery](https://cloudnative-pg.io/documentation/current/recovery/)
- [plugin-barman-cloud](https://github.com/cloudnative-pg/plugin-barman-cloud)
- [barman-cloud-restore](https://docs.pgbarman.org/release/latest/user_guide/commands/barman_cloud/barman_cloud_restore.html)
