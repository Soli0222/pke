# Keycloak の共通認証基盤

Flux は natsume の Keycloak・CNPG・Ingress・監視を、Terraform は共通 realm・ユーザー・管理権限・OIDC クライアント・MCP の scope と CIMD policy を管理する。
定義は [Flux アプリ](../../flux/clusters/natsume/apps/keycloak/) とこのディレクトリに置く。バージョンは HelmRelease と `versions.tf` が正本である。
Keycloak 本体・Service・公開 / 管理 Ingress・ServiceMonitor は codecentric の [keycloakx chart](https://github.com/codecentric/helm-charts/tree/master/charts/keycloakx) を HelmRelease から管理する。
CNPG・バックアップ・1PasswordItem は既存アプリと同じ Flux 構成とし、NetworkPolicy はポート別の接続元制限を表すため別マニフェストで管理する。

## アカウントと権限

| 対象 | realm | 用途と権限 |
|---|---|---|
| bootstrap 管理者 | `master` | Helm の初回起動で作る一時管理者。最初の Terraform apply にだけ使用 |
| `terraform` クライアントのサービスアカウント | `master` | client credentials で Terraform を実行。realm の追加・master の設定も扱うため `admin` ロールを付与 |
| `soli` | `pke` | sui と今後の OIDC アプリに共通でログイン。`realm-management` の `realm-admin` ロールで pke 内のユーザー・クライアントを管理 |

`soli` は master や他の realm の全体管理権限を持たない。pke の管理画面は `https://admin.keycloak.pstr.space/admin/pke/console/`。
Terraform は人間の `soli` のパスワードや OTP を使わない。専用クライアントはブラウザログイン・password grant を無効にし、管理 API へ mTLS で接続する。
新しいアプリも pke に OIDC クライアントとして追加する。ユーザーをアプリごとに作成せず、redirect URI・audience・scope・利用権限をアプリ単位で指定する。
新規クライアントの既定 scope は `basic` / `profile` / `email` とし、管理権限を含む `roles` は既定で渡さない。組み込みの管理コンソールは既存の client scopes を維持する。
Terraform が管理するユーザー・ロール・クライアントの設定を管理画面で変更した場合は、次の apply と競合しないよう Git の定義にも反映する。

## 認証ルール

master と pke の両方に、次のブラウザ認証フローを設定する。

- 新規ユーザーはパスキーと OTP の両方を初回登録する。`soli` は仮パスワード変更も必須。
- 通常の認証は、本人確認（PIN / 生体認証等）が必須のパスキー、またはパスワードと OTP。パスキーの直後に OTP を重ねて要求しない。
- 有効な SSO セッションは再利用する。パスワード認証時は OTP 未登録を理由にスキップせず、登録を要求する。
- pke の password grant は realm の認証フローで拒否し、組み込み admin-cli からもブラウザ認証を迂回させない。master は初回 bootstrap apply 用の direct grant をこの段階では維持する。
- master のパスキー RP ID は管理ホスト、pke は公開ホスト。pke の管理画面も、認証自体は公開ホストの同じ soli アカウントを使う。
- サービスアカウントは対話認証の対象外。既存 bootstrap ユーザーは Terraform に取り込まず、初回 apply の password grant を使える状態で残す。

この設定は両方の初回登録とログイン経路を管理する。本人が後からパスキーを削除しても毎回再登録させる独自処理は追加しない。パスワード経路には引き続き OTP が必要。
予備パスキーも登録し、OTP の復旧情報を保管する。pke のメールによるパスワードリセットは無効。

## 公開範囲

| 用途 | ホスト / パス | 接続条件 |
|---|---|---|
| アプリの共通認証 | `keycloak.pstr.space` の pke realm の OIDC・login-actions・account と `/resources/` | HTTPS。利用者のクライアント証明書は不要 |
| 管理 UI / API と master realm の認証 | `admin.keycloak.pstr.space` | 既存の `pke-natsume-mtls` によるクライアント証明書必須 |
| health / metrics | Pod の management port | Ingress に公開しない。Alloy から収集 |

公開 Ingress は `/admin/`、`/realms/master/` をルーティングしない。
認可サーバー discovery は従来の `/realms/pke/.well-known/` に加え、RFC 8414 の `/.well-known/oauth-authorization-server/realms/pke` を完全一致で公開する。パスを含む issuer を扱う MCP クライアントはこの標準 URL を先に探索する。Keycloak が返す metadata をそのまま配信し、書き換えやリダイレクトは行わない。`/.well-known/` 全体や master の metadata は公開しない。
匿名 DCR は専用 Ingress で `/realms/pke/clients-registrations/openid-connect` とその配下だけを公開する。作成は匿名、個別クライアントの取得・更新・削除には Keycloak が Registration Access Token 等を要求する。`default` / `install` / `saml2-entity-descriptor` の登録 API は公開しない。
専用 Ingress と RateLimit Middleware は keycloakx chart の `extraManifests` で管理し、通常の公開 Ingress の TLS 証明書を共用する。DCR 経路だけに平均10要求/分・burst 5の制限を設ける。公開ホスト単位で集計し、カウンターは Traefik インスタンスごとに独立する。ログイン・token・discovery はこの制限の対象外。
この Ingress を公開する前に、DCR 登録ポリシーの Terraform apply を完了する。ポリシー未適用のまま公開する PR を先行マージしない。停止する場合は DCR 用 Ingress と Middleware を Git から除去する。既存クライアントの認証と token 更新は継続できる。
Helm の `KC_HOSTNAME` と `KC_HOSTNAME_ADMIN` は管理ホストに固定する。Terraform 未適用の初回起動から master のログインと Cookie 確認 iframe が管理ホスト内で完結する。
Terraform は master の `frontendUrl` に同じ管理 URL を、pke の `frontendUrl` に公開 URL を設定する。
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
`master` / `pke` realm、Terraform 用クライアント、共通ユーザー `soli` は `prevent_destroy` で保護する。

## 初回構築

1. Git の変更をマージし、Flux に公開 Ingress の `/realms/pke/` を反映する。sui HelmRelease は Bearer JWT 対応コードを含む appVersion の chart を指定する。
2. このディレクトリで **bootstrap モード**を読み込み、必要な変数を指定して plan / apply する。

```sh
source ./setup.sh bootstrap
export TF_VAR_soli_email='<soli のメールアドレス>'
terraform init
# pke realm がまだ存在しない新規構築時だけ、標準登録ポリシーの import 前に realm を作る。
terraform plan -target=keycloak_realm.pke -out=keycloak-realm.tfplan
terraform apply keycloak-realm.tfplan
terraform plan -out=keycloak.tfplan
terraform apply keycloak.tfplan
```

`setup.sh bootstrap` は一時管理者で provider を認証し、同時に専用 client secret をリソース作成へ渡す。`master` は import block で取り込む。
realm だけの適用は新規構築時の import 対象作成に限る。続けて全体 plan / apply を完了する。既存の pke への通常適用では `-target` を使わない。

3. `https://keycloak.pstr.space/realms/pke/account/` へ soli と仮パスワードでログインし、パスワード変更・OTP・パスキー登録を完了する。mTLS 証明書付きブラウザで pke 管理画面にも入り、管理権限を確認する。
4. 同じ端末で **サービスアカウントモード**へ切り替え、以後の Terraform 認証を確認する。

```sh
source ./setup.sh
terraform plan
```

`setup.sh` は既定で `master` の `terraform` クライアントを使う。以前の `KEYCLOAK_USER` / `KEYCLOAK_PASSWORD` を解除し、client credentials に切り替える。bootstrap モードへ戻す場合は逆に `KEYCLOAK_CLIENT_SECRET` を解除する。
新しいシェルでは `TF_VAR_soli_email` も再設定する。初回 plan ファイルは、適用時も bootstrap モードのまま使う。

5. soli と専用サービスアカウントの接続確認後、bootstrap 管理者の削除と Helm の bootstrap Secret 参照の撤去を別変更で行う。この定義では bootstrap ユーザーや Secret を削除しない。全体管理はサービスアカウントに移り、soli は master の一時管理者を削除する権限を持たない。
6. discovery に `read:sui` / `write:sui` があることを確認し、`terraform output -raw sui_helm_values` を `sui-keycloak` の `values.yaml` へ登録する。許可 subject が揃うと Flux が sui を切り替える。
7. soli で Web ログインし、Grok・Claude・ChatGPT を接続する。ツール一覧・参照・更新・読み取り専用時の更新拒否・refresh を確認する。

`sui-keycloak` には実際の許可 subject を登録する。Secret 未作成時は sui の Flux が NotReady となる。空の values やダミー subject で回避しない。
Flux 差分 CI は `--allow-missing-secrets` で render するため、allowedSubjects の実値は差分に含まれない。切替時に Terraform 出力と実際の Deployment 設定を照合する。

## sui のバージョン更新

sui のイメージは HelmRelease の chart version と、その chart の appVersion で管理する。通常の更新は Renovate の chart 更新 PR をマージすればよく、Keycloak の Terraform apply や 1Password の更新は不要。
`sui_helm_values` は許可 subject だけを出力し、`image.tag` は含めない。1Password の `sui-keycloak/values.yaml` にもイメージタグを設定しない。
既存の値に `image` が残っている場合は、そのブロックを削除して `auth` を残す。Git の修正だけでは登録済みの 1Password の値は変わらず、タグが残ると chart 更新後も古いイメージに固定される。
Terraform 出力から更新する場合は、この定義を apply して state 内の出力を更新した後、`terraform output -raw sui_helm_values` の出力で `values.yaml` 全体を置き換える。既存 YAML へ追記しない。

## 運用確認

Ready に加えて revision / generation、ログインとバックアップ成功を確認する。

```sh
kubectl --context natsume@soli -n flux-system get kustomization keycloak sui
kubectl --context natsume@soli -n keycloak get helmrelease,statefulset,ingress,clusters.postgresql.cnpg.io,backups
kubectl --context natsume@soli -n sui get helmrelease sui
curl --fail https://keycloak.pstr.space/realms/pke/.well-known/openid-configuration
curl --fail https://keycloak.pstr.space/.well-known/oauth-authorization-server/realms/pke
```

公開ホストの `/admin/` と `/realms/master/` が到達不能で、管理ホストが証明書なしの接続を拒否することも確認する。
DB は単一 instance。Keycloak も StatefulSet の単一 Pod を置き換えるため、更新時に認証が一時停止する。週次 backup + WAL archive と復元は [CNPG.md](../../CNPG.md) を参照する。

運用状況は [PKE / Keycloak](https://grafana.str08.net/d/pke-keycloak) で確認する。HTTP・DCR/token・JVM・DB接続プールと認証拒否ログを表示し、DB本体の詳細は CloudNativePG へ移動する。保存定義と表示上の制約は [Grafanaの運用](../../grafana/README.md#keycloak) を参照する。

## MCP の設定

CIMD と Resource Indicators は Keycloak の experimental feature。更新前には実クライアントで互換性を確認する。
CIMD client ID の受付ホストは `claude.ai` / `grok.com` に限定し、metadata 内の URL に必要なホストは executor の allowlist で別管理する。
Claude の metadata に含まれる public client 向けでない grant は executor で除外する。
ChatGPT は共通 client ID の `https://chatgpt.com/oauth/client.json` を事前登録し、公開 JWKS による `private_key_jwt` を使う。この Keycloak 版の CIMD parser は ChatGPT の `token_endpoint_auth_methods_supported` 拡張を拒否するため、ChatGPT は CIMD policy の対象に含めない。
共通 ID の redirect URI は [公開 metadata](https://chatgpt.com/oauth/client.json) に合わせて `https://chatgpt.com/connector_platform_oauth_redirect` のみを許可する。接続ごとの callback ID の設定は不要。公開 metadata の redirect URI / JWKS の変更は再確認する。
PKCE は S256。read/write scope は optional とし、discovery に掲載する。scope を公開しただけで自動付与はしない。
resource は `https://sui.str08.net/mcp` に限定する。read/write scope の audience mapper は `included_custom_audience` で同じ URL を access token に直接付与し、内部 client ID の `sui-mcp` からの変換に依存しない。
Resource Indicators 用の `sui-mcp` クライアントと `resource_url` 属性は維持する。要求された resource は token の URL audience と直接照合できる。
Keycloak の offline refresh は処理用 context を再作成する経路があり、Resource Indicators が使う一時属性を引き継がないと、内部 client ID から URL への変換が行われない。scope 自体の mapper に URL を指定して、この経路でも audience を維持する。CIMD が自動追加する resource 用 mapper も URL を直接付与する。
mapper の変更は次に発行・更新する access token に反映される。既発行の token は変わらないため、適用後は MCP 接続元で再認証または token の更新を行う。DCR クライアントの登録し直しは不要。
JWT は RS256、短い有効期限、標準の `JWT` / 本文 `Bearer` / `azp` を使用し、sui は専用フラグなしで検証する。
Web クライアントは `sui-web`、Authorization Code + PKCE の public client で、MCP 用 scope は割り当てない。

### DCR

MCP クライアントには匿名の OIDC Dynamic Client Registration（DCR）を提供する。CIMD と ChatGPT の事前登録は併用する。
Terraform は `dcr.tf` で登録ルールを管理する。DCR で作られた個々のクライアントは Terraform に取り込まず、Client ID・Client Secret・Registration Access Token は Keycloak と接続元クライアントで管理する。1Password への転記は不要。

- Keycloak が作った既定の匿名登録ポリシーのうち5件を名前で import する。既定ポリシーを残したまま同種のポリシーを追加しない。
- DCR のホスト許可リストは設けず、接続元サービスごとの Terraform 変更を不要にする。匿名登録の Trusted Hosts ポリシーは削除する。redirect URI は共通ポリシーで通常の HTTPS と loopback IP の HTTP を受け付け、外部 HTTP・独自 scheme・ワイルドカード・fragment は拒否する。
- loopback callback は `http://127.0.0.1/callback` / `http://[::1]/callback` の形式で登録する。Keycloak の OAuth 2.1 検査は登録時のポート指定と `localhost` を拒否するため、loopback IP をポートなしで登録し、認可要求で実際のポートを指定する。
- ユーザーの同意を必須にし、Full Scope Allowed を無効にする。登録者による protocol mapper の追加を許可せず、管理済みの client scopes で claims を付与する。
- 許可する scope は `openid` / `basic` / `profile` / `email` / `offline_access` / `read:sui` / `write:sui`。realm の既定 optional scopes も `offline_access` と sui の2 scope に絞り、scope 省略時に別の権限が付かないようにする。他アプリの scope はそのクライアントへ個別に割り当てる。
- 登録・Registration Access Token による更新時に PKCE S256 を設定し、implicit / password grant を無効化する。`ByAnonymous` 条件は CIMD の登録・更新にも一致するため、この共通ポリシーの redirect URI 検査は CIMD にも適用する。CIMD 自体の metadata 取得先・client ID のホスト制限は `mcp.tf` で別管理する。Terraform の管理 API 更新には適用しない。
- Max Clients Limit は realm 全体で50件。DCR だけでなく組み込み・事前登録・CIMD のクライアントも含む。到達後は新しい匿名登録が拒否され、既存クライアントのログインは継続する。不要な動的クライアントは接続元との対応を確認して管理画面から削除する。

DCR は任意の接続元から登録できるが、登録できても sui の利用権限は得られず、ユーザーの認証・同意に加え、sui が issuer / subject / audience / scope を検証する。管理ロールの scope は渡さない。

#### 公開する順序

登録ポリシーと公開 Ingress は別 PR で適用する。Ingress を公開する PR は、次の Terraform apply 完了後にマージする。

```sh
# 登録ポリシーの PR をマージして最新の main を取得した後、terraform/keycloak で実行
source ./setup.sh
export TF_VAR_soli_email='<soli のメールアドレス>'
terraform plan -out=keycloak-dcr.tfplan
terraform apply keycloak-dcr.tfplan
```

plan では既定登録ポリシーの import / 更新、MCP 登録 profile / policy の追加、realm の optional scopes の変更を確認する。既存の Terraform state にある `dcr_trusted_hosts` は削除対象になる。realm・ユーザー・既存クライアントを作り直す変更は不要。
新規 realm では Keycloak が Trusted Hosts ポリシーを自動生成する。全体 apply を終えた後、DCR Ingress を公開する前に、管理画面の Client registration → Client registration policies → Anonymous から Trusted Hosts だけを削除する。残り5件のポリシーは維持する。既存環境では Terraform apply が削除するため、この操作は不要。
公開後、Grok のカスタムコネクタに `https://sui.str08.net/mcp` を指定する。`validate-url` の `oauthStrategy` が `OAUTH_STRATEGY_DCR` になり、Client ID / Secret の手入力なしで認証へ進むことを確認する。続けて soli の認証と同意を完了し、ツール一覧と参照が成功することを確認する。
公開直後に BYO のままなら、DCR の HTTP status と Keycloak の登録拒否ログを確認する。登録時に返される secret / Registration Access Token をログや PR へ貼らない。
