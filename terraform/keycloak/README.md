# Keycloak と sui の認証

Flux は natsume の Keycloak・CNPG・Ingress・監視を、Terraform は realm・ユーザー・Web クライアント・MCP の scope と CIMD policy を管理する。
定義は [Flux アプリ](../../flux/clusters/natsume/apps/keycloak/) とこのディレクトリに置く。バージョンは HelmRelease と `versions.tf` が正本である。
Keycloak 本体・Service・公開 / 管理 Ingress・ServiceMonitor は codecentric の [keycloakx chart](https://github.com/codecentric/helm-charts/tree/master/charts/keycloakx) を HelmRelease から管理する。
CNPG・バックアップ・1PasswordItem は既存アプリと同じ Flux 構成とし、NetworkPolicy はポート別の接続元制限を表すため別マニフェストで管理する。
Auth0 は切替後の接続確認が完了するまで残す。既存の `terraform/auth0` state は使わない。

## 公開範囲

| 用途 | ホスト / パス | 接続条件 |
|---|---|---|
| sui の認証 | `keycloak.pstr.space` の sui realm の OIDC・login-actions・account と `/resources/` | HTTPS。利用者のクライアント証明書は不要 |
| 管理 UI / API と master realm のログイン | `admin.keycloak.pstr.space` | 既存の `pke-natsume-mtls` によるクライアント証明書必須 |
| health / metrics | Pod の management port | Ingress に公開しない。Alloy から収集 |

公開 Ingress は `/admin/`、`/realms/master/`、匿名 DCR をルーティングしない。
管理 URL の指定だけでは管理 API を閉じられないため、Ingress のパス制限と併用する。
Helm の `KC_HOSTNAME` と `KC_HOSTNAME_ADMIN` はどちらも管理ホストに固定する。これにより、Terraform 未適用の初回起動から master realm のログインと Cookie 確認 iframe が管理ホスト内で完結する。
Terraform は master realm の `frontendUrl` に同じ管理 URL を明示し、sui realm の `frontendUrl` にだけ公開 URL を設定する。
HTTP のバックエンドへ到達できる Pod は NetworkPolicy で Traefik に絞る。Keycloak は Traefik の転送ヘッダーを信頼する。
DNS は既存の ExternalDNS、サーバー証明書は cert-manager の HTTP-01 で管理する。Cloudflare の証明書認証を迂回しないよう DNS-only で使う。

## 先に用意するもの

1Password の Kubernetes vault に次を用意する。値は Git や PR へ書かない。

| Item | フィールド | 用途 |
|---|---|---|
| `keycloak-bootstrap` | `username` / `password` | 初回起動時の一時管理ユーザー。強いランダムパスワードを使用 |
| `terraform keycloak` | `username` / `password` | 初回は上記の管理ユーザー。運用開始後は専用の管理資格情報に交換 |
| `terraform keycloak` | `client-certificate` / `client-private-key` | natsume の mTLS クライアント証明書と秘密鍵（PEM） |
| `terraform keycloak` | `sui-initial-password` | sui 利用者の初回ログイン用。16文字以上のランダム値 |
| `sui-keycloak` | `values.yaml` | **切替時に** `terraform output -raw sui_helm_values` の出力を登録 |

既存の `cnpg-backup-s3-secret`、`cnpg-backup-flux-vars`、R2 state 用の `terraform kkg-pve` を再利用する。
`sui-keycloak` を先に作らない。これが存在しない間、sui の HelmRelease は新設定を適用できず、既存 release は Auth0 設定のまま稼働する（Flux はこの間 NotReady）。
空の values やダミー subject でブロックを回避しない。
Flux 差分 CI は生成 Secret を取得せず `--allow-missing-secrets` で render するため、image.tag と allowedSubjects の実値は差分に含まれない。切替時に Terraform 出力と実際の Deployment の設定を照合する。

## 反映順序

1. `keycloak-bootstrap` を作成し、この Flux 構成をマージする。natsume の Keycloak・CNPG・証明書・DNS が準備できたことを確認する。管理画面 `https://admin.keycloak.pstr.space/admin/master/console/` は、この段階で mTLS 証明書と bootstrap 管理者を使ってログインできる。Terraform の事前適用や手動での realm 設定変更は不要。
2. sui の Bearer JWT 対応 PR をマージし、そのコードを含むコンテナイメージをリリースする。実在する固定タグを確認する。Keycloak だけの先行デプロイではこのリリースを待つ必要はない。
3. このディレクトリで `setup.sh` を読み、確認済みの sui イメージタグを指定して Terraform を適用する。

```sh
source ./setup.sh
export TF_VAR_sui_image_tag='<対応コードを含む公開済みタグ>'
terraform init
terraform plan -out=keycloak.tfplan
terraform apply keycloak.tfplan
```

provider は管理ホストへ mTLS で接続する。`master` は起動時に作られるため import block で取り込む。
初回の Terraform は管理ブラウザのログイン前後どちらでも実行できる（admin-cli の token 取得は管理ホストを直接指定する）。
`master`、sui realm、sui ユーザーには `prevent_destroy` を設定している。
sui の初期パスワードは write-only / ephemeral 引数で渡し、state / plan へ保存しない。`value_wo_version` を変更した場合だけ再設定される。

4. 管理ホストに mTLS 証明書付きブラウザで入り、恒久の管理ユーザーを作る。一時 bootstrap admin を削除し、`terraform keycloak` の資格情報を更新する。初期化済み DB では bootstrap 環境変数によって管理ユーザーは再作成されない。
5. `https://keycloak.pstr.space/realms/sui/account/` に sui ユーザーでログインし、初期パスワード変更とパスキー登録を完了する。Auth0 のパスキーは新しい RP ID へ引き継げないため再登録する。
6. discovery に `read:sui` / `write:sui` があることを確認し、`terraform output -raw sui_helm_values` を `sui-keycloak` の `values.yaml` フィールドへ登録する。1Password Operator の同期後、Flux が sui のイメージ・issuer・client ID・subject を一緒に切り替える。
7. Web ログインと Grok・Claude・ChatGPT の接続を作り直し、ツール一覧・参照・更新・読み取り専用時の更新拒否・token refresh を確認する。確認後に Auth0 の廃止を別変更で行う。

適用確認は次を使う。Ready に加えて revision / generation、実際のログインとバックアップ成功を確認する。

```sh
kubectl --context natsume@soli -n flux-system get kustomization keycloak sui
kubectl --context natsume@soli -n keycloak get helmrelease,statefulset,ingress,clusters.postgresql.cnpg.io,backups
kubectl --context natsume@soli -n sui get helmrelease sui
curl --fail https://keycloak.pstr.space/realms/sui/.well-known/openid-configuration
```

公開ホストの `/admin/` と `/realms/master/` が到達不能で、管理ホストが証明書なしの HTTPS 接続を拒否することも確認する。
DB は単一 instance。Keycloak も StatefulSet の単一 Pod を置き換えるため、更新時に認証が一時停止する。CNPG の週次 backup + WAL archive と復元手順は [CNPG.md](../../CNPG.md) を参照する。
バックアップは `s3://cnpg-backup/keycloak/` を保存先とする。空の DB から作り直す際は、別 DB の WAL と混在させないよう未使用の保存先を使う。バケット直下にある旧 `keycloak-cluster` のバックアップは自動復元しない。

再構築のため Flux の Keycloak 同期を停止している場合は、修正をマージして GitRepository が新しい revision を取得した後に再開する。namespace と DB は Flux が再作成する。

```sh
flux reconcile source git flux-system --context natsume@soli -n flux-system
flux resume kustomization keycloak --context natsume@soli -n flux-system
flux reconcile kustomization keycloak --context natsume@soli -n flux-system
```

## MCP の設定

CIMD と Resource Indicators は Keycloak の experimental feature。更新前には実クライアントで互換性を確認する。
CIMD client ID の受付ホストは `claude.ai` / `grok.com` に限定し、metadata 内の URL に必要なホストは executor の allowlist で別管理する。
Claude の metadata に含まれる public client 向けでない grant は executor で除外する。
ChatGPT は `chatgpt_callback_id` で指定した既存のクライアント ID を事前登録し、公開 JWKS による `private_key_jwt` を使う。この Keycloak 版の CIMD parser は ChatGPT の `token_endpoint_auth_methods_supported` 拡張を拒否するため、ChatGPT は CIMD policy の対象に含めない。
接続を作り直して callback ID が変わる場合は、新しい metadata URL の ID を Terraform へ渡して再適用する。公開 metadata の redirect URI / JWKS の変更も再確認する。
PKCE は S256。read/write scope は optional とし、discovery に掲載する。scope を公開しただけで自動付与はしない。
resource は `https://sui.str08.net/mcp` に限定し、audience を resource URL に束縛する。
JWT は RS256、短い有効期限、標準の `JWT` / 本文 `Bearer` / `azp` を使用し、sui は専用フラグなしで検証する。
Web クライアントは `sui-web`、Authorization Code + PKCE の public client で、MCP 用 scope は割り当てない。

## 切り戻し

Auth0 の設定・ユーザーと既存 `sui-oidc` Secret は接続確認が完了するまで保持する。
切り戻す場合は、sui HelmRelease の `valuesFrom` を外し、issuer・clientIdSecret・許可 subject と image を切替前の定義へ戻す PR を適用する。
Keycloak の DB や Auth0 の Terraform state は削除しない。issuer 変更により Web と MCP の再認証が必要になる。
財務データを認証移行のために削除・再投入する操作は行わない。
