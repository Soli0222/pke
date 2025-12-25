# Tailscale ACL Terraform Configuration

このディレクトリは、Tailscale の ACL（Access Control List）を Terraform で管理する設定を含みます。

## 概要

- provider: `tailscale/tailscale` (main.tf では `0.24.0` を指定)
- ACL は `resource "tailscale_acl" "main"` で管理されています。

## 前提条件

1. Tailscale API キー
   - Tailscale 管理画面の API keys からキーを生成
   - 環境変数 `TAILSCALE_API_KEY` に設定します。

   ```bash
   export TAILSCALE_API_KEY="your-api-key-here"
   ```

   - 補助スクリプト `setup.sh` がリポジトリ内にあり、1Password CLI (`op`) を使ってキーをエクスポートする例を示しています（組織内で 1Password を使っている場合）。

2. Terraform（CLI）がインストールされていること

3. S3 バックエンド（Cloudflare R2）用の認証情報
   - main.tf では Terraform の S3 backend を Cloudflare R2 エンドポイントへ向ける設定をしています。
   - バックエンドにアクセスするために以下を設定してください：

   ```bash
   export AWS_ACCESS_KEY_ID="your-access-key"
   export AWS_SECRET_ACCESS_KEY="your-secret-key"
   ```

   - main.tf の backend 設定（抜粋）:
     - endpoint: https://e334a8146ecc36d6c72387c7e99630ee.r2.cloudflarestorage.com
     - bucket: `tfstate`
     - key: `pke/tailscale/terraform.tfstate`
     - region: `auto` とし、各種検証スキップオプションが有効になっています。

## セットアップと運用手順

### 新規セットアップ

1. 環境変数を設定（上記を参照）
2. Terraform の初期化

```bash
terraform init
```

3. 変更の確認

```bash
terraform plan
```

4. 適用

```bash
terraform apply
```

### 既存 ACL の取り込み（推奨）

付属の `import_acl.sh` を使うと、前提条件チェック、API 接続確認、`terraform init`、および `terraform import tailscale_acl.main acl` を自動で行います。

```bash
export TAILSCALE_API_KEY="..."
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
./import_acl.sh
```

### 手動での import

1. `TAILSCALE_API_KEY` をエクスポート
2. `terraform init`
3. ACL を import

```bash
terraform import tailscale_acl.main acl
```

4. `terraform plan` で差分を確認し、必要であれば `terraform apply` を実行してください。

## ACL 設定について

ACL の中身は `main.tf` の `acl = jsonencode(...)` で直接定義されています。主な構成要素:

- tagOwners: `tag:k8s-operator`, `tag:k8s`, `tag:kkg-external` など
- groups: `group:k8s-readers`, `group:prod`, `group:kkg`（例として GitHub ユーザー `Soli0222@github` が割り当てられています）
- grants: Kubernetes 統合（impersonate 設定）や特定 IP へのアクセス許可
- acls: デフォルトで全許可（`accept` のルールが1つあります）
- ssh: SSH の `check` ルール（nonroot や root へのアクセス指定）

実際の JSON は `main.tf` を確認してください。

## 注意事項

- ACL の変更は慎重に行ってください。誤った設定は通信遮断を招く可能性があります。
- API キーは機密情報です。適切に管理してください。
- バックエンドの R2（S3）認証情報は安全に保管してください。

## トラブルシューティング

一般的なチェック項目:

- `TAILSCALE_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` が設定されているか
- `terraform` と `curl` が利用可能か
- ネットワークから Tailscale API (`https://api.tailscale.com`) にアクセスできるか

付属スクリプト `import_acl.sh` は上記チェックを行い、問題点を出力します。

## リポジトリ内の主なファイル

- `main.tf` - 実際の ACL を保持する Terraform 設定
- `import_acl.sh` - 既存 ACL を Terraform state に import する補助スクリプト
- `setup.sh` - 1Password (`op`) から環境変数をエクスポートする補助スクリプト（組織内利用向け）
- `.terraform.lock.hcl` - プロバイダロックファイル
- `.gitignore` - 無視設定

## 変更履歴 / メモ

- README を main.tf と実際のスクリプトに合わせて更新しました（Cloudflare R2 backend 設定、必要な環境変数、存在するスクリプトの一覧を反映）。

