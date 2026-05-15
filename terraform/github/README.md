# GitHub Terraform

GitHub リポジトリ設定と GitHub Actions secrets を管理する Terraform 構成です。

## 対象

- `repositories.yaml` に定義した `Soli0222/*` リポジトリ
- Pull Request merge 後の head branch 自動削除 (`delete_branch_on_merge = true`)
- `repositories.yaml` に定義した GitHub Actions repository secrets

## 初期化

```sh
cd terraform/github
source ./setup.sh
terraform init
terraform plan
```

既存リポジトリと Actions secrets はすでに Terraform 管理へ移行済みです。新しい repository を追加した場合は、対応する resource を `terraform import` してください。

## ファイル構成

| ファイル | 内容 |
|----------|------|
| `repositories.yaml` | repository settings と Actions secrets の唯一の宣言ソース |
| `versions.tf` | provider / backend / `github_owner` / output |
| `locals.tf` | `repositories.yaml` の decode と global/repository override の merge |
| `repositories.tf` | `github_repository` と `github_branch_default` |
| `actions_secrets.tf` | 1Password-backed `github_actions_secret` |
| `op-read-secret.rb` | Terraform external provider から呼ぶ 1Password 読み取り helper |
| `setup.sh` | `GITHUB_TOKEN` と R2 backend credentials の export |

## Repository settings

リポジトリ設定の source of truth は `repositories.yaml` です。Terraform は `yamldecode(file(...))` でこの YAML を読み、`github_repository` と `github_branch_default` に反映します。

`global.repository` に共通設定を置き、`repositories.<name>` に同じキーを書いた場合はリポジトリ側が優先されます。`security_and_analysis` も同じく global と repo 個別を merge します。

`has_downloads`, `vulnerability_alerts`, `ignore_vulnerability_alerts_during_read` は provider 側で deprecated / no-op 扱いのため YAML 管理対象から外し、plan ノイズ抑制のため Terraform 側でのみ `ignore_changes` しています。

## GitHub Actions secrets

Secret の source of truth も `repositories.yaml` です。全リポジトリ共通の secret は `global.actions_secrets`、リポジトリ個別の secret は `repositories.<name>.actions_secrets` に入れます。個別指定が同名の場合は個別値が優先されます。

```yaml
global:
  actions_secrets:
    SHARED_TOKEN:
      onepassword:
        item: terraform github
        field: SHARED_TOKEN
        vault: Private

repositories:
  pke:
    actions_secrets:
      PRIVATE_KEY:
        onepassword:
          reference: op://Private/terraform github/private-key.pem
      CERTIFICATE:
        onepassword:
          item: terraform github
          file: certificate.pem
          vault: Private
```

1Password source は以下をサポートします。

- `item` + `field` + 任意の `vault`: `op item get <item> --fields label=<field>` で key-value field を読む
- `reference`: `op read op://...` で 1Password secret reference を直接読む
- `vault` + `item` + `file`: `op read op://<vault>/<item>/<file>` で file 添付を読む

`value` は Terraform 上で sensitive 扱いですが、値は state に保存されます。R2 backend と plan file は secret と同じ強度で扱ってください。

## よく使う操作

plan:

```sh
cd terraform/github
source ./setup.sh
terraform plan
```

新規 repository を追加する場合:

1. `repositories.yaml` の `repositories` に repository 名を追加
2. GitHub 側に既存 repository があるなら `terraform import 'github_repository.repositories["<repo>"]' <repo>`
3. default branch も `terraform import 'github_branch_default.repositories["<repo>"]' <repo>`
4. `terraform plan`

既存 secret を Terraform 管理へ追加する場合は、`repositories.yaml` の対象 repository に `actions_secrets` と 1Password source を追加して `terraform plan` してください。
