# Keycloak の共通認証基盤

Flux は natsume の Keycloak・CNPG・Ingress・監視を、Terraform は共通 realm・ユーザー・管理権限・OIDC クライアント・MCP の scope と CIMD policy を管理する。
定義は [Flux アプリ](../../flux/clusters/natsume/apps/keycloak/) とこのディレクトリに置く。バージョンは HelmRelease と `versions.tf` が正本である。
Keycloak 本体・Service・公開 / 管理 Ingress・ServiceMonitor は codecentric の [keycloakx chart](https://github.com/codecentric/helm-charts/tree/master/charts/keycloakx) を HelmRelease から管理する。
CNPG・バックアップ・1PasswordItem は既存アプリと同じ Flux 構成とし、NetworkPolicy はポート別の接続元制限を表すため別マニフェストで管理する。
Auth0 は切替後の接続確認が完了するまで残す。既存の `terraform/auth0` state は使わない。

## アカウントと権限

| 対象 | realm | 用途と権限 |
|---|---|---|
| bootstrap 管理者 | `master` | Helm の初回起動で作る一時管理者。最初の Terraform apply にだけ使用 |
| `terraform` クライアントのサービスアカウント | `master` | client credentials で Terraform を実行。realm の追加・master の設定も扱うため `admin` ロールを付与 |
| `soli` | `pstr` | sui と今後の OIDC アプリに共通でログイン。`realm-management` の `realm-admin` ロールで pstr 内のユーザー・クライアントを管理 |

`soli` は master や他の realm の全体管理権限を持たない。pstr の管理画面は `https://admin.keycloak.pstr.space/admin/pstr/console/`。
Terraform は人間の `soli` のパスワードや OTP を使わない。専用クライアントはブラウザログイン・password grant を無効にし、管理 API へ mTLS で接続する。
新しいアプリも pstr に OIDC クライアントとして追加する。ユーザーをアプリごとに作成せず、redirect URI・audience・scope・利用権限をアプリ単位で指定する。
新規クライアントの既定 scope は `basic` / `profile` / `email` とし、管理権限を含む `roles` は既定で渡さない。組み込みの管理コンソールは既存の client scopes を維持する。
Terraform が管理するユーザー・ロール・クライアントの設定を管理画面で変更した場合は、次の apply と競合しないよう Git の定義にも反映する。

## 認証ルール

master と pstr の両方に、次のブラウザ認証フローを設定する。

- 新規ユーザーはパスキーと OTP の両方を初回登録する。`soli` は仮パスワード変更も必須。
- 通常の認証は、本人確認（PIN / 生体認証等）が必須のパスキー、またはパスワードと OTP。パスキーの直後に OTP を重ねて要求しない。
- 有効な SSO セッションは再利用する。パスワード認証時は OTP 未登録を理由にスキップせず、登録を要求する。
- pstr の password grant は realm の認証フローで拒否し、組み込み admin-cli からもブラウザ認証を迂回させない。master は初回 bootstrap apply 用の direct grant をこの段階では維持する。
- master のパスキー RP ID は管理ホスト、pstr は公開ホスト。pstr の管理画面も、認証自体は公開ホストの同じ soli アカウントを使う。
- サービスアカウントは対話認証の対象外。既存 bootstrap ユーザーは Terraform に取り込まず、初回 apply の password grant を使える状態で残す。

この設定は両方の初回登録とログイン経路を管理する。本人が後からパスキーを削除しても毎回再登録させる独自処理は追加しない。パスワード経路には引き続き OTP が必要。
Auth0 のパスキーは RP ID が変わるため引き継げない。予備パスキーも登録し、OTP の復旧情報を保管する。pstr のメールによるパスワードリセットは無効。

## 公開範囲

| 用途 | ホスト / パス | 接続条件 |
|---|---|---|
| アプリの共通認証 | `keycloak.pstr.space` の pstr realm の OIDC・login-actions・account と `/resources/` | HTTPS。利用者のクライアント証明書は不要 |
| 管理 UI / API と master realm の認証 | `admin.keycloak.pstr.space` | 既存の `pke-natsume-mtls` によるクライアント証明書必須 |
| health / metrics | Pod の management port | Ingress に公開しない。Alloy から収集 |

公開 Ingress は `/admin/`、`/realms/master/`、匿名 DCR をルーティングしない。
Helm の `KC_HOSTNAME` と `KC_HOSTNAME_ADMIN` は管理ホストに固定する。Terraform 未適用の初回起動から master のログインと Cookie 確認 iframe が管理ホスト内で完結する。
Terraform は master の `frontendUrl` に同じ管理 URL を、pstr の `frontendUrl` に公開 URL を設定する。
NetworkPolicy で HTTP は Traefik、management port は Alloy に接続元を制限する。Keycloak は Traefik の転送ヘッダーを信頼する。
DNS は既存の ExternalDNS、証明書は cert-manager の HTTP-01 で管理し、Cloudflare は DNS-only で使う。

## 先に用意するもの

1Password の Kubernetes vault に次を用意する。値は Git や PR へ書かない。

| Item | フィールド | 用途 |
|---|---|---|
| `keycloak-bootstrap` | `username` / `password` | 現在の一時管理者。初回 apply の認証に使用 |
| `terraform keycloak` | `client-secret` | 初回 apply 前に生成する32文字以上のランダム値。専用クライアントの登録と、その後の認証に同じ値を使用 |
| `pke_natsume_mtls`（既存） | `tls.crt` / `tls.key` | natsume の mTLS クライアント証明書と秘密鍵（PEM） |
| `terraform keycloak` | `soli-initial-password` | soli の初回ログイン用。16文字以上のランダム値 |
| `sui-keycloak` | `values.yaml` | **切替時に** `terraform output -raw sui_helm_values` の出力を登録 |

`terraform keycloak` の `username` / `password` / `sui-initial-password` フィールドは使用しない。
mTLS は既存の `pke_natsume_mtls` を直接参照し、証明書・秘密鍵を `terraform keycloak` に複製しない。
同項目の `ca.crt` はクライアント証明書の検証用 CA。管理ホストのサーバー証明書は Let's Encrypt が発行するため、Terraform provider はシステムの信頼ストアで検証し、`root_ca_certificate` にこの CA は指定しない。
既存の `cnpg-backup-s3-secret`、`cnpg-backup-flux-vars`、R2 state 用の `terraform kkg-pve` を再利用する。バックアップ保存先は変更しない。
専用クライアントの secret と soli の初期パスワードは write-only / ephemeral 引数で渡し、state / plan へ保存しない。再適用時も読み込むが、それぞれの write-only version を変更しない限り再設定しない。
`master`、`pstr`、`soli`、Terraform 用クライアントには `prevent_destroy` を設定している。

## 初回適用と切替

この定義は旧 sui realm の Terraform をまだ apply していない状態を前提とする。適用済みの場合は realm / user の名前変更としてそのまま apply せず、state と issuer の移行を別途計画する。
Keycloak 本体の再構築は不要。既存の bootstrap 管理者をそのまま初回 apply に使う。

1. Git の変更をマージし、Flux に公開 Ingress の `/realms/pstr/` を反映する。sui 対応版の公開済みイメージタグも用意する。
2. このディレクトリで **bootstrap モード**を読み込み、必要な変数を指定して plan / apply する。

```sh
source ./setup.sh bootstrap
export TF_VAR_soli_email='<soli のメールアドレス>'
export TF_VAR_sui_image_tag='<Bearer JWT 対応コードを含む公開済みタグ>'
terraform init
terraform plan -out=keycloak.tfplan
terraform apply keycloak.tfplan
```

`setup.sh bootstrap` は一時管理者で provider を認証し、同時に専用 client secret をリソース作成へ渡す。`master` は import block で取り込む。

3. `https://keycloak.pstr.space/realms/pstr/account/` へ soli と仮パスワードでログインし、パスワード変更・OTP・パスキー登録を完了する。mTLS 証明書付きブラウザで pstr 管理画面にも入り、管理権限を確認する。
4. 同じ端末で **サービスアカウントモード**へ切り替え、以後の Terraform 認証を確認する。

```sh
source ./setup.sh
terraform plan
```

`setup.sh` は既定で `master` の `terraform` クライアントを使う。以前の `KEYCLOAK_USER` / `KEYCLOAK_PASSWORD` を解除し、client credentials に切り替える。bootstrap モードへ戻す場合は逆に `KEYCLOAK_CLIENT_SECRET` を解除する。
新しいシェルでは `TF_VAR_soli_email` と `TF_VAR_sui_image_tag` も再設定する。初回 plan ファイルは、適用時も bootstrap モードのまま使う。

5. soli と専用サービスアカウントの接続確認後、bootstrap 管理者の削除と Helm の bootstrap Secret 参照の撤去を別変更で行う。この定義では bootstrap ユーザーや Secret を削除しない。全体管理はサービスアカウントに移り、soli は master の一時管理者を削除する権限を持たない。
6. discovery に `read:sui` / `write:sui` があることを確認し、`terraform output -raw sui_helm_values` を `sui-keycloak` の `values.yaml` へ登録する。イメージと許可 subject が揃うと Flux が sui を切り替える。
7. soli で Web ログインし、Grok・Claude・ChatGPT を再接続する。ツール一覧・参照・更新・読み取り専用時の更新拒否・refresh を確認してから Auth0 廃止を別変更で行う。

`sui-keycloak` は切替まで作成しない。Secret 未作成時は sui の Flux が NotReady となり、既存 release は Auth0 設定で稼働を続ける。空の values やダミー subject で回避しない。
Flux 差分 CI は `--allow-missing-secrets` で render するため、image.tag と allowedSubjects の実値は差分に含まれない。切替時に Terraform 出力と実際の Deployment 設定を照合する。

## 運用確認

Ready に加えて revision / generation、ログインとバックアップ成功を確認する。

```sh
kubectl --context natsume@soli -n flux-system get kustomization keycloak sui
kubectl --context natsume@soli -n keycloak get helmrelease,statefulset,ingress,clusters.postgresql.cnpg.io,backups
kubectl --context natsume@soli -n sui get helmrelease sui
curl --fail https://keycloak.pstr.space/realms/pstr/.well-known/openid-configuration
```

公開ホストの `/admin/` と `/realms/master/` が到達不能で、管理ホストが証明書なしの接続を拒否することも確認する。
DB は単一 instance。Keycloak も StatefulSet の単一 Pod を置き換えるため、更新時に認証が一時停止する。週次 backup + WAL archive と復元は [CNPG.md](../../CNPG.md) を参照する。

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
