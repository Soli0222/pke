# 管理経路と復旧データ

個人・クラスタ・発行回を分けた短期証明書で緊急操作を行い、使用後に RBAC を解除する。
実施者、理由、対象、開始・終了、保管物の所在は非公開の運用記録に残す。
秘密値、kubeconfig 本文、token、復号鍵を Git や記録へ貼らない。

## 短期証明書による操作

作業端末に kubectl、Python 3.9 以降、PyYAML、cryptography 42 以降が必要。
`scripts/emergency-access.py` は指定した管理 context で CSR を作成・承認し、別の証明書で kubectl を1回実行する。
API と証明書署名機能が利用可能であることが前提になる。
Keycloak は使わない。API・署名経路も停止した場合は、非公開手順の最終管理資格情報またはホストコンソールへ切り替える。

```sh
python3 scripts/emergency-access.py \
  --cluster natsume --context emergency-natsume-legacy-admin \
  --kubeconfig /path/to/private/emergency-kubeconfig \
  --person soli --reason '管理経路の復旧確認' --ttl 600 \
  -- get nodes -o name
```

meruto は `--cluster meruto` と `--context emergency-meruto-legacy-admin` を指定する。
SSH トンネル使用時は `--server` と `--tls-server-name` をラッパー側に指定し、CA と SAN の検証を維持する。
管理 context として `natsume@soli` / `meruto@soli` も指定できる。

既定の `observe` は Node の get/list と `/readyz`・`/version` の GET だけを許可する。
修復に管理権限が必要な場合は、予定操作を記録して `--role cluster-admin` を明示する。
CN は `pke:breakglass:<cluster>:<person>:<発行回>`、要求 TTL は600〜3600秒とし、発行された証明書の実際の期限・主体・用途を検査する。
`system:masters` は付与しない。`--person` は利用者の申告であり、本人認証の代わりにはならない。
共有の管理資格情報を使う初動は、保管先の利用記録・本人の作業記録と対応付ける。

専用 kubeconfig は0700の一時ディレクトリ内に0600で作り、終了時に削除する。
コマンド失敗時も専用 ClusterRoleBinding → ClusterRole（observe の場合）→ CSR の順に削除し、Node の認可が `no` に戻ったことを確認する。
証明書期限で実行中の kubectl も終了させる。
`cleanup` の `failed` / `uncertain` が空でなければ、開始時に表示された `cleanup_name` を元の管理経路で調べる。
SIGKILL・端末喪失では後処理を保証できないため、同じ確認を手動で行う。

CSR の削除は証明書の失効ではない。
RBAC 解除後も証明書の認証と `system:authenticated` の基本権限は期限まで残る。
cluster-admin の操作で別の資格情報・権限を作成した場合、それらはこの処理で自動解除されない。
監査ログの username、操作、対象、時刻、結果を作業記録と照合し、作成した権限等も個別に確認する。

## external etcd の復旧一式を暗号化保管する

`scripts/backup-k3s-recovery.py` は server 上で snapshot を取り、対応する server token と設定を tar stream にまとめ、SSH から端末の age へ直接渡す。
対象は natsume が natsume-03、meruto が meruto-01。
サービス停止・再起動・restore・compact は行わない。
設定変更や token rotation と同時に実行しない。

端末には age、SSH、Python が、server には root 権限、Python、etcdctl と同じ版の etcdutl が必要。
etcdutl は `/usr/local/bin`、または Ansible が展開した該当版の `/var/lib/etcd/downloads/` から探す。
バージョンが一致しなければ取得を開始しない。

暗号化対象は次のとおり。

- external etcd snapshot と取得前後で一致を確認した K3s server token
- `/etc/etcd/pki`、`/etc/rancher/k3s`（audit policy、registry 設定・証明書等を含む）
- etcd / K3s の systemd unit、存在する drop-in と K3s の service.env
- ホスト・クラスタ・実ソフトウェア版・snapshot status・各ファイルのサイズと SHA-256 を含む manifest

このアーカイブには管理 kubeconfig や秘密鍵も含まれる。
server の0700一時ディレクトリで snapshot と設定のコピーを作り、正常終了・通常の例外時に削除する。
強制終了やホスト障害時には root が `pke-recovery-*` 一時ディレクトリの残存を確認する。
端末に保存するのは0600の暗号文だけで、既存ファイルは上書きしない。
PV のアプリデータ、DB の個別バックアップ、ホスト全体の再構成は別途必要。

復号鍵をクラスタと独立した保管先へ保存し、読み戻せることを確認してから、対応する公開受信者を指定する。
出力先は Git 管理外の0700ディレクトリを用意する。

```sh
python3 scripts/backup-k3s-recovery.py \
  --cluster natsume --recipient age1... \
  --output /path/to/private/natsume-recovery.tar.age
```

SSH は既知ホスト検証を必須とし、agent 転送を無効にする。
既存の sudo 認証が agent 転送を必要とする場合に限り、接続先 root が接続中の agent を利用できることを理解した実施者が `--forward-agent` を明示する。
この処理のために sudoers や既存 SSH 設定を書き換えない。

暗号文をクラスタ外へ保存した後、その保管先から読み戻した暗号文と復号鍵で検証する。
1Password から鍵を読む場合は本文を端末に表示せず、メモリから age へ渡せる。

```sh
OP_BIOMETRIC_UNLOCK_ENABLED=true python3 scripts/verify-k3s-recovery.py \
  --cluster natsume --archive /path/to/private/natsume-recovery.tar.age \
  --identity-ref 'op://Personal/PKE Recovery - age identity/identity'
```

`--identity /path/to/private/age-key` で0600の秘密鍵ファイルも指定できる。
検証処理は tar を展開せず、age の認証と全ファイルのサイズ・SHA-256、クラスタ一致、必須ファイルの存在を確認する。
`decryption_verified` / `file_integrity_verified` が true でも、K3s の復元試験を済ませたことにはならない。
snapshot、token、TLS の組とソフトウェア版を維持し、隔離環境の復元手順で確認する。
external etcd に embedded etcd 用の `k3s --cluster-reset` を適用しない。

参考：[K3s Backup and Restore](https://docs.k3s.io/datastore/backup-restore)、[Kubernetes CSR](https://kubernetes.io/docs/reference/access-authn-authz/certificate-signing-requests/)。
