# GitHub Terraform

`terraform/github/` は `Soli0222/*` の GitHub repository settings、default branch、GitHub Actions repository secrets を管理する Terraform 構成である。
repository と secret の宣言元は `repositories.yaml` に集約する。

## 管理対象

Terraform は次の resource を管理する。

| Resource | 用途 |
|----------|------|
| `github_repository.repositories` | repository settings |
| `github_branch_default.repositories` | default branch |
| `github_actions_secret.repository` | GitHub Actions repository secrets |
| `data.external.onepassword_actions_secret` | 1Password から secret value を読む external data source |

`github_repository.repositories` は `prevent_destroy = true` を使う。
Terraform から repository を破棄しない。

## Provider と Backend

| 対象 | 値 |
|------|----|
| Terraform | `>= 1.5.0` |
| GitHub provider | `integrations/github` `6.12.1` |
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
| `setup.sh` | `GITHUB_TOKEN` と R2 backend credentials の export |

## Repository Settings

`repositories.yaml` の `global.repository` に共通設定を置く。
`repositories.<name>` に同じ key を書いた場合は repository 側の値が優先される。
`security_and_analysis` も global と repository 個別を merge する。

現行の global default は repository を public にし、pull request merge 後の branch 削除を有効にする。
secret scanning と push protection は global で有効にする。
`mk-stream` と `spotify-nowplaying` は個別 override で secret scanning を無効にする。

`has_downloads`、`vulnerability_alerts`、`ignore_vulnerability_alerts_during_read` は provider 側で deprecated または no-op 扱いである。
YAML の管理対象から外し、Terraform 側の `ignore_changes` で plan noise を抑える。

## Managed Repositories

現行の `repositories.yaml` は次の repository を管理する。

```text
daypassed-bot
diary-cli
emoji-bot-gateway
emoji-renderer
helm-charts
mk-stream
note-tweet-connector
pgroonga-cnpg
pke
rss-fetcher
spotify-nowplaying
spotify-reblend
sui
summaly
vip-responder
webhook-test
```

## GitHub Actions Secrets

Actions secrets も `repositories.yaml` に宣言する。
全 repository 共通の secret は `global.actions_secrets` に置く。
repository 個別の secret は `repositories.<name>.actions_secrets` に置く。
同名 secret がある場合は repository 個別の指定が優先される。

現行の global secret は Renovate 用 GitHub App credential である。

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

1. `repositories.yaml` の `repositories` に repository 名を追加する。
2. GitHub 側に既存 repository がある場合は `terraform import 'github_repository.repositories["<repo>"]' <repo>` を実行する。
3. default branch も `terraform import 'github_branch_default.repositories["<repo>"]' <repo>` で import する。
4. `terraform plan` で差分を確認する。

既存 secret を Terraform 管理へ追加する。

1. `repositories.yaml` の対象 repository に `actions_secrets` を追加する。
2. `onepassword` source を `item + field`、`reference`、`vault + item + file` のいずれかで指定する。
3. `terraform plan` で値を読めることと差分を確認する。

global default を変える場合は影響範囲が全 managed repository に広がる。
repository 個別 override で済む変更は、対象 repository の下にだけ書く。
