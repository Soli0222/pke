# terraform/auth0

sui (https://sui.str08.net) 用の Auth0 テナント設定を管理する。
tfstate は他の Terraform 構成と同じく Cloudflare R2 の `tfstate` バケット (`pke/auth0/terraform.tfstate`) に置く。

## 管理対象

| リソース | 内容 |
|----------|------|
| `auth0_client.sui` | SPA アプリケーション。`authorization_code` のみ、OIDC Conformant、callback は `https://sui.str08.net/api/auth/callback`。Web Origins / Logout URLs は空 |
| `auth0_client_credentials.sui` | Token Endpoint Auth Method を `none` (public client) にする |
| `auth0_connection.sui` | 第三者 CIMD クライアントでも使えるドメインレベルの DB 接続 `sui-users`。signup 無効、brute force protection 有効、password policy `excellent`、username 不要、パスキー有効 |
| `auth0_connection_clients.sui` | 有効クライアントは `auth0_client.sui` と Management API の M2M アプリ |
| `auth0_user.sui` | `sui@str08.net` / `email_verified = true` |
| `auth0_tenant.mcp` | 既存テナントの CIMD 対応と `resource` パラメータ互換設定 |
| `auth0_resource_server.sui_mcp` | MCP 用 API。audience は `https://sui.str08.net/mcp` |
| `auth0_resource_server_scope.sui_mcp` | `read:sui` / `write:sui` |
| `auth0_client_cimd.chatgpt` | ChatGPT の公開 CIMD を取り込み、callback とクライアント認証を設定 |
| `auth0_client_grant.chatgpt_sui_mcp` | ChatGPT にユーザー委任で MCP API を使う権限を付与 |

M2M アプリを `enabled_clients` に入れているのは Auth0 の制約による。
`POST /api/v2/users` は対象 connection がその API を呼ぶクライアントに対して有効化されていないと 400 を返すため、`auth0_user.sui` を Terraform で管理する以上は外せない。
M2M アプリは client_credentials しか使わないので、ログイン経路が増えるわけではない。

## パスキー

`sui-users` 接続で `authentication_methods.passkey.enabled = true` にしている。
Auth0 の仕様上、パスキーを有効にしても password は有効なままにする必要がある (パスキー非対応のブラウザ/端末向け)。

パスキーが実際に動くには**テナント側の前提条件**が要る。以下のパスキー関連設定は、この構成では管理していない。

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
MCP 用に `read/update:tenant_settings`、`create/read/update/delete:resource_servers`、`create/read/update/delete:client_grants` も付与する。

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


## ChatGPT からの MCP OAuth 接続

`mcp.tf` は、ChatGPT に `https://sui.str08.net/mcp` を登録すると認証先を自動発見し、Client ID / Secret の転記なしで接続するための Auth0 側の設定である。
sui 側の Protected Resource Metadata、401 challenge、JWT 検証、scope 制御、トークン更新に対応する MCP セッション管理は別途実装する。
Terraform の適用だけでは接続は完成しない。

### API と権限

- API identifier / token audience: `https://sui.str08.net/mcp`
- JWT: RS256 / RFC 9068 profile。有効期限は1時間。
- scopes: `read:sui`（参照）、`write:sui`（作成・更新・削除）。更新するクライアントは両方を要求する。
- refresh token: `offline_access` で要求する。ローテーションあり、絶対期限90日、未使用期限30日、再利用猶予5秒。
- ユーザー委任は client grant 必須。ChatGPT にだけ両 scope を許可し、client credentials によるアクセスは拒否する。

client grant は利用者の制限を代替しない。
sui は署名・issuer・audience・期限・scope に加えて、既存の利用者許可リストも検証する必要がある。
RFC 9068 のアクセストークンに email が含まれるとは限らないため、利用者の照合は検証済み issuer / subject を基準に設計する。

### 既存設定への影響

`auth0_tenant.mcp` は provider が接続する既存テナントの設定を更新する。新しいテナントを作成するリソースではない。
CIMD 対応と `resource_parameter_profile = "compatibility"` はテナント全体に適用される。
後者は `audience` が指定されていない場合に `resource` を宛先として使う。
別の Terraform state で同じテナントを管理している場合は、管理元を統合してから適用する。

`sui-users` は `is_domain_connection = true` により第三者クライアントのログインでも利用可能になる。
`enabled_clients` の2件は第一者クライアントの設定であり、第三者クライアント全体の許可リストにはならない。
MCP API へのアクセスは専用 client grant で制限する。signup 無効とパスキー設定は維持する。

### CIMD の適用前確認

Auth0 は管理者が CIMD URL を取り込んで登録する方式であり、その登録を Terraform が行う。
`chatgpt_cimd_url` の既定値は `https://chatgpt.com/oauth/client.json`。
Auth0 の discovery が `authorization_response_iss_parameter_supported: true` を通知し、認可応答でも正しい `iss` を返す場合に使う。
この条件を満たさない場合は、ChatGPT の接続管理画面に表示される接続固有の URL を指定する。

```bash
export TF_VAR_chatgpt_cimd_url='https://chatgpt.com/oauth/<callback_id>/client.json'
```

CIMD URL はリソース作成後に変更すると再作成になる。
同じ URL のメタデータを取り直す場合は `external_client_id_version` を増やす。
callback、JWKS、token endpoint のクライアント認証方式は公開 CIMD から取り込み、独自の値で上書きしない。

確認した ChatGPT の公開 CIMD は `private_key_jwt` を優先し、`none` も対応方式として掲載している。
Auth0 の公式文書では `private_key_jwt` は Enterprise 向けとされているため、テナントの利用可否と CIMD import 時に選択される方式を確認する。
`terraform validate` はプランの利用資格やリモート CIMD の取り込み可否を検証しない。
Auth0 に有効な旧 Rules がある場合は、strict な第三者 CIMD クライアントのログインが失敗するため Actions への移行が必要。

適用時は通常の `terraform plan` で既存設定の差分を確認する。
適用後は Auth0 discovery の CIMD・PKCE S256・offline access の通知を確認し、sui 側の実装後に ChatGPT で接続とトークン更新を検証する。

参照:

- [Auth0: Register Applications with CIMD](https://auth0.com/docs/get-started/auth0-overview/create-applications/register-applications-with-cimd)
- [OpenAI: Authentication](https://developers.openai.com/plugins/build/auth)
- [Auth0 Terraform provider: CIMD client](https://registry.terraform.io/providers/auth0/auth0/latest/docs/resources/client_cimd)
