# terraform/auth0

sui (https://sui.str08.net) 用の Auth0 テナント設定を管理する。
tfstate は他の Terraform 構成と同じく Cloudflare R2 の `tfstate` バケット (`pke/auth0/terraform.tfstate`) に置く。

## 管理対象

| リソース | 内容 |
|----------|------|
| `auth0_client.sui` | SPA アプリケーション。`authorization_code` のみ、OIDC Conformant、callback は `https://sui.str08.net/api/auth/callback`。Web Origins / Logout URLs は空 |
| `auth0_client_credentials.sui` | Token Endpoint Auth Method を `none` (public client) にする |
| `auth0_connection.sui` | sui 専用の DB 接続 `sui-users`。signup 無効、brute force protection 有効、password policy `excellent`、username 不要、パスキー有効 |
| `auth0_connection_clients.sui` | 有効クライアントは `auth0_client.sui` と Management API の M2M アプリ |
| `auth0_user.sui` | `sui@str08.net` / `email_verified = true` |

M2M アプリを `enabled_clients` に入れているのは Auth0 の制約による。
`POST /api/v2/users` は対象 connection がその API を呼ぶクライアントに対して有効化されていないと 400 を返すため、`auth0_user.sui` を Terraform で管理する以上は外せない。
M2M アプリは client_credentials しか使わないので、ログイン経路が増えるわけではない。

## パスキー

`sui-users` 接続で `authentication_methods.passkey.enabled = true` にしている。
Auth0 の仕様上、パスキーを有効にしても password は有効なままにする必要がある (パスキー非対応のブラウザ/端末向け)。

パスキーが実際に動くには**テナント側の前提条件**が要る。これらは接続単位ではなくテナント全体の設定なので、この構成では管理していない。

- New Universal Login を使う
- Identifier First 認証を有効にする
- カスタムログインページを無効にする
- カスタムドメインを設定しておく (パスキーは relying party ドメインに紐づくため、後からドメインを変えると登録済みパスキーが無効になる)

パスワードの最大長は 72 バイト (bcrypt の制約)。Auth0 のハード上限で、接続設定でもテナント設定でも変更できない。

既定の `Username-Password-Authentication` 接続は触らない。
`auth0_user` の `verify_email` は指定しない (指定すると `email_verified` の設定を上書きしてしまうため)。

## 認証情報

Auth0 Management API の M2M アプリの認証情報を 1Password の `terraform auth0` に置く。

- `AUTH0_DOMAIN`
- `AUTH0_CLIENT_ID`
- `AUTH0_CLIENT_SECRET`

M2M アプリには Management API に対して最低限 `create/read/update/delete:clients`、`create/read/update:client_credentials`、`create/read/update/delete:connections`、`create/read/update/delete:users` が必要。

## 使い方

```bash
cd terraform/auth0
source ./setup.sh
terraform init
terraform plan
terraform apply
```

ユーザーの初期パスワードは Terraform が生成し state に入る。
初回ログイン用に取り出す場合:

```bash
terraform output -raw sui_user_initial_password
```

Auth0 側でパスワードを変更しても Terraform は追従しない (`ignore_changes = [password]`)。
