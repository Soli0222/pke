# GitHub Terraform

`terraform/github/` は `Soli0222/*` の GitHub repository settings、default branch、GitHub Actions repository secrets を管理する Terraform 構成である。
repository と secret の宣言元は `repositories.yaml` に集約する。

## 管理対象

Terraform は次の resource を管理する。

| Resource | 用途 |
|----------|------|
| `github_repository.repositories` | active repository settings |
| `github_repository.archived` | archive 済み repository の archived state |
| `github_branch_default.repositories` | active repository の default branch |
| `github_actions_secret.repository` | active repository の GitHub Actions repository secrets |
| `data.external.onepassword_actions_secrets` | 1Password から secret value をまとめて読む external data source |

`github_repository.repositories` と `github_repository.archived` は `prevent_destroy = true` を使う。
Terraform から repository を破棄しない。

## Provider と Backend

| 対象 | 値 |
|------|----|
| Terraform | `>= 1.5.0` |
| GitHub provider | `integrations/github` `6.13.0` |
| External provider | `hashicorp/external` `2.4.0` |
| GitHub owner | `Soli0222` |
| State backend | Cloudflare R2 S3 compatible backend |
| State key | `github/terraform.tfstate` |

`setup.sh` は `GITHUB_TOKEN` と R2 backend credentials を export する。
`GITHUB_TOKEN` は `gh auth token` から取得する。
R2 credentials は 1Password item `terraform kkg-pve` から読む。

```bash
cd terraform/github
source ./setup.sh
terraform init
terraform plan
```

## ファイル構成

| ファイル | 内容 |
|----------|------|
| `repositories.yaml` | repository settings と Actions secrets の唯一の宣言元 |
| `versions.tf` | Terraform version、provider、backend、`github_owner`、output |
| `locals.tf` | `repositories.yaml` の decode、global default と repository override の merge |
| `repositories.tf` | `github_repository` と `github_branch_default` |
| `actions_secrets.tf` | 1Password backed `github_actions_secret` |
| `op-read-secret.rb` | external provider から呼ぶ 1Password 読み取り helper |
| `import-existing-repositories.sh` | 既存 repository と default branch の再開可能な import helper |
| `setup.sh` | `GITHUB_TOKEN` と R2 backend credentials の export |

## Repository Settings

`repositories.yaml` の `global.repository` に共通設定を置く。
`repositories.<name>` に同じ key を書いた場合は repository 側の値が優先される。
`security_and_analysis` も global と repository 個別を merge する。

現行の global default は repository を public にし、pull request merge 後の branch 削除を有効にする。
secret scanning と push protection は global で有効にする。
`mk-stream` と `spotify-nowplaying` は個別 override で secret scanning を無効にする。
Private repository では `security_and_analysis` blockを生成しない。
今回追加した既存repositoryは `topics: []` でoverrideし、globalの `renovate` topicを追加しない。

archive 済み repository は `github_repository.archived` で `archived` state だけを管理する。
GitHub がarchive後のrepository settingsをread-onlyにするため、それ以外の属性は `ignore_changes` とする。
default branch と Actions secrets も `active_repositories` だけを対象とし、archive 済み repository への更新は行わない。

`has_downloads`、`vulnerability_alerts`、`ignore_vulnerability_alerts_during_read` は provider 側で deprecated または no-op 扱いである。
YAML の管理対象から外し、Terraform 側の `ignore_changes` で plan noise を抑える。

## Managed Repositories

現行の `repositories.yaml` は次の active repository を管理する。

```text
amemado
daypassed-bot
diary-cli
emoji-bot-gateway
emoji-renderer
exiforge
homebrew-exiforge
keymap
kubecon-schedule
Mac-NowPlaying
mk-stream
pgroonga-cnpg
picpress
pke
polestar-hub
polestar_wrapped
rss-fetcher
shared-workflows
soli-site
spn-cli
spotify-nowplaying
spotify-reblend
sui
summaly
vip-responder
VVCSoftware_VTM
webhook-test
```

次の archive 済み repository は archived state だけを管理する。

```text
antenna_del_spam
audioroute
Charmy
cloudflare-ingress-controller
debug-image
flow-sight
helm-charts
misskey-summarizer
mk-anniv
subscription-manager
summaly-server
```

## GitHub Actions Secrets

Actions secrets も `repositories.yaml` に宣言する。
全 repository 共通の secret は `global.actions_secrets` に置く。
repository 個別の secret は `repositories.<name>.actions_secrets` に置く。
同名 secret がある場合は repository 個別の指定が優先される。

現行の global secret は Renovate 用 GitHub App credential である。

同じ 1Password source はリポジトリ数にかかわらず1回だけ読み取る。
すべての一意な source を単一の external data source へ渡し、helper 内で順次取得するため、1Password Desktop の delegated session を並列に確立しない。

| Secret | 1Password source |
|--------|------------------|
| `RENOVATE_CLIENT_ID` | `vaults/Personal/items/PKE Renovate App` の field `CLIENT_ID` |
| `RENOVATE_PRIVATE_KEY` | `vaults/Personal/items/PKE Renovate App` の file `private-key.pem` |

一部 repository は Docker Hub credential を個別 secret として持つ。
`terraform plan` 時点で 1Password CLI が読める必要がある。

## 1Password Source

`op-read-secret.rb` は次の 3 形式を受け付ける。

### item と field

```yaml
RENOVATE_CLIENT_ID:
  onepassword:
    vault: Personal
    item: PKE Renovate App
    field: CLIENT_ID
```

この形式は `op item get <item> --fields label=<field> --format json` を使う。

### secret reference

```yaml
PRIVATE_KEY:
  onepassword:
    reference: op://Personal/example/private-key.pem
```

この形式は `op read op://...` を直接呼ぶ。

### file attachment

```yaml
RENOVATE_PRIVATE_KEY:
  onepassword:
    vault: Personal
    item: PKE Renovate App
    file: private-key.pem
```

この形式は `op read op://<vault>/<item>/<file>` を呼ぶ。

`onepassword` に未知の key がある場合、helper は error を返す。
secret value は Terraform 上で sensitive 扱いになるが、state には保存される。
R2 backend と plan file は secret と同じ強度で扱う。

## よく使う操作

plan を確認する。

```bash
cd terraform/github
source ./setup.sh
terraform plan
```

新しい repository を追加する。

1. `repositories.yaml` の `repositories` に repository 名を追加する。archive 済みの場合は `archived: true` と現在値に必要な override も指定する。
2. GitHub 側に既存 repository がある場合、activeなら `terraform import 'github_repository.repositories["<repo>"]' <repo>`、archive済みなら `terraform import 'github_repository.archived["<repo>"]' <repo>` を実行する。
3. active repository の場合だけ、default branch も `terraform import 'github_branch_default.repositories["<repo>"]' <repo>` で import する。
4. `terraform plan` で差分を確認する。

複数の既存 repository をまとめて import または中断後に再開する場合は helper を使う。
state に存在する resource は自動的に skipし、archive 済み repository の default branch は対象外にする。

```bash
cd terraform/github
source ./setup.sh
./import-existing-repositories.sh --check
./import-existing-repositories.sh
```

既存 secret を Terraform 管理へ追加する。

1. `repositories.yaml` の対象 repository に `actions_secrets` を追加する。
2. `onepassword` source を `item + field`、`reference`、`vault + item + file` のいずれかで指定する。
3. `terraform plan` で値を読めることと差分を確認する。

global default を変える場合は影響範囲が全 managed repository に広がる。
repository 個別 override で済む変更は、対象 repository の下にだけ書く。
