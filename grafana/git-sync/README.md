# Git Syncの接続

`repository.json`をGrafanaへ登録すると、`Soli0222/pke`のmainにある`grafana/dashboards/`をPKEフォルダへ同期する。
Git Syncへの接続設定であり、Kubernetesには適用しない。
CIはこの接続を変更せず、初回登録はgcxで行う。

`workflows`は`branch`だけとし、UIからの直接main書き込みを有効にしない。
ConnectionとRepositoryの両方でwebhookを無効にし、60秒のpollingを使う。
mTLS ingressを変更せず、PR preview画像の自動投稿も無効にする。

## GitHub Appと1Password

[GitHub App作成画面](https://github.com/settings/apps/new)で、PKE専用のAppを作成する。

| 設定 | 値 |
|---|---|
| Name | `pke-grafana-git-sync`（重複時は接尾辞を追加） |
| Homepage URL | `https://grafana.str08.net` |
| Webhook / Active | OFF |
| Contents | Read and write |
| Pull requests | Read and write |
| Administration | Read-only |
| Metadata | Read-only（自動付与） |
| インストール先 | Only on this account、`Soli0222/pke`のみ |

polling専用のためWebhooks権限は付けない。
GrafanaのConnection schemaにある`spec.webhook.disabled`に従う。
App作成後に秘密鍵を生成し、App IDとインストール後のURLのInstallation IDを確認する。
ユーザー認可用callback URLやOAuth client secretは不要。

1Passwordの`Kubernetes` vaultにitem `grafana-git-sync`を作り、以下のfieldを保存する。

| field | 内容 |
|---|---|
| `app-id` | 数値のApp ID |
| `installation-id` | 数値のInstallation ID |
| 添付ファイル `private-key.pem` | 生成した秘密鍵ファイルをそのまま添付（推奨） |
| `private-key`（代替） | 秘密鍵PEM全文（改行を保持、concealed field） |

スクリプトは`private-key.pem`添付があればfieldより優先して読み取る。
Appのprivate keyは1Passwordを正とし、Git・ログ・PRへ出力しない。
Grafana自身の暗号化されたsecure storeへ登録し、Kubernetes Secretには複製しない。

## 初回登録

`op`へサインインし、`gcx config check`で対象Grafanaを確認する。
既存の同名Connection / Repositoryがある場合、スクリプトは上書きせず停止する。

```sh
python3 scripts/configure-grafana-git-sync.py --context default
python3 scripts/configure-grafana-git-sync.py --context default --apply
```

既定はvalidateとdry-runのみ。
Provisioning APIはserver-side dry-runに対応しないため、gcxの検証成功だけでは参照先や認証の正しさを保証しない。
設定変更時は実機のOpenAPI schemaにも照合し、作成後にConnectionのhealthとRepositoryの同期statusを確認する。
`--apply`ではConnection、Repositoryの順にvalidate / dry-run / 作成を行う。
private keyを含む一時ファイルはGit管理外に権限0600で作成し、終了時に削除する。
gcxのsecure値を含み得る応答は表示しない。
途中で失敗した場合は、エラー全文を公開せず、gcxでリソースの存在とspec/statusを確認してから不足部分を実行する。

```sh
gcx resources get connections --json metadata.name,spec,status -o json
gcx resources get repositories --json metadata.name,spec,status -o json
gcx dashboards get pke-flux-performance -o json
```

同期commitをmainのSHAと照合し、dashboard UID・folder・変数・queryを確認する。
初回は新規Flux画面だけで追加・更新・Git revertを確認し、その後に既存UIDを移行する。
UIでの見た目・変数切替・リンクは利用者が確認する。

## 接続を更新する

Repositoryの設定変更はJSONをPRで変更し、merge後にgcxで現在値を確認して適用する。
ダッシュボード本体はGit Syncから反映し、接続設定の更新と混同しない。

Appの鍵を更新するときは、新しい鍵を作成して1Passwordへ保存し、現在のConnectionを読み取って`secure.privateKey.create`だけを安全に差し替える。
秘密鍵を引数やログへ出さず、初回と同じ一時ファイル・出力抑制を使う。
既存Connectionのspec・resourceVersion・管理情報を保持し、同期の回復を確認してから旧鍵をGitHubで失効する。
初回作成用スクリプトを鍵更新のために迂回・再実行しない。

参考: [GrafanaのコードによるGit Sync設定](https://grafana.com/docs/grafana/latest/as-code/observability-as-code/git-sync/git-sync-setup/set-up-code/)
