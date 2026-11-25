# muse の Kubernetes 接続

muse が Claude Code から PR マージ後の更新を確認し、詰まった反映をやり直すための接続を管理する。
構成の変更は GitHub と Flux を通す。Kubernetes API では読み取りと、reconcile・rollout restart・scale・Pod と Job の削除だけを許可する。
初回と kubeconfig の再発行は「接続を準備する」「kubeconfig を生成する」を管理者が実施する。
日常の操作は「更新を確認する」、漏えい・廃止時は「アクセスを停止する」を参照する。

## 権限の範囲

両クラスタに `muse-system/muse-operator` ServiceAccount を作成する。
RBAC は [共通定義](flux/bases/muse-access/clusterrole.yaml) で管理し、natsume だけに [CNPG の読み取り権限](flux/clusters/natsume/apps/muse-access/kustomization.yaml)、meruto だけに [KubeVirt・CDI の読み取り権限](flux/clusters/meruto/apps/muse-access/kustomization.yaml) を追加する。
対象 namespace は限定せず、扱えるリソースの種類と操作を列挙する。

| 読み取りを許可するもの | 用途 |
|---|---|
| Flux の source、Kustomization、HelmRelease、FluxInstance など | revision、Ready、同期失敗の確認 |
| workload、Pod、Events、Pod ログ | rollout と失敗原因の確認 |
| Node、Service、Ingress、volume、metrics など | 基盤・ネットワーク・ストレージの状態確認 |
| Traefik の TLSOption、Middleware | 両クラスタの Ingress が参照する TLS 設定とミドルウェアの確認（get/list のみ） |
| 証明書・監視・TopoLVM の指定した CR | 各 controller の反映状況確認 |
| CNPG の Cluster、Backup、ScheduledBackup、Pooler | natsume の DB・バックアップ状態確認 |
| KubeVirt の VM・VMI・移行・snapshot・export・backup・pool・instancetype と CDI の指定した CR | meruto の VM・ディスク・バックアップ状態確認（get/list/watch のみ） |

| 許可する操作 | コマンドの例 |
|---|---|
| Flux の source、Kustomization、HelmRelease の reconcile | `flux reconcile ks <name> --with-source`、`flux reconcile hr <name> --force` |
| Deployment、StatefulSet、DaemonSet の再起動 | `kubectl rollout restart` |
| Deployment、StatefulSet の replicas の変更 | `kubectl scale` |
| Pod と Job の削除 | `kubectl delete pod`、`kubectl delete job` |

RBAC の patch だけでは Pod テンプレートや HelmRelease の values も書き換えられ、privileged Pod を通じて実質 cluster-admin になる。
このため [ValidatingAdmissionPolicy](flux/bases/muse-access/validatingadmissionpolicy.yaml) で muse-operator からの更新を次の範囲に限定する。

- workload：Pod テンプレートの spec とラベルを変更しない。注釈は `kubectl.kubernetes.io/restartedAt` だけを変更できる。replicas は変更できる。
- StatefulSet：PVC の保持設定と `volumeClaimTemplates` を変更しない。
- Flux の CR：spec とラベルを変更しない。注釈は `reconcile.fluxcd.io/requestedAt`・`forceAt`・`resetAt` だけを変更できる。

spec の `suspend` だけを変更させる条件は CEL で安全に書けないため、`flux suspend` / `flux resume` は拒否される。止める必要があれば管理者に引き継ぐ。
`kubectl rollout undo`、`kubectl set image`、`kubectl edit` での変更も拒否される。変更は PR にする。

Secret、ConfigMap、OnePasswordItem は読み取り対象に含めない。
exec、attach、port-forward、proxy、リソースの作成、workload と Flux の CR の削除、トークン発行、権限の委譲も許可しない。
`helm list` / `helm get` は Secret を読むため使えない。
Pod 定義・HelmRelease の values・ログなど、許可したリソースに直接書かれた秘密値は見えるため、出力を無条件に PR へ貼らない。

Pod の削除と再起動はサービスを止めうる。
CNPG の DB は1 instance のため、Pod を消すと再作成まで DB が止まる。TopoLVM などストレージの Pod は削除しない。

GitHub のマージ権限は kubeconfig とは別に管理する。
RBAC や ValidatingAdmissionPolicy を変更する PR もマージできる場合、Flux 経由でこの制限を変更できる。
Grafana の参照やアプリの疎通確認も別の認証・接続経路を使う。

## 接続を準備する

[Tailscale の設定](terraform/tailscale/main.tf) は `tag:muse-server` から `tag:service` の TCP 6443 だけを追加で許可する。
この許可は `tag:service` が付いた全デバイスに及ぶ。muse server に `tag:operation` は付けない。
Tailscale のタグや ACL は加算されるため、別のタグで広い権限を持っていないことも確認する。
SSH、etcd、kubelet のポートと UDP 6443 を許可しないことを policy tests に定義している。

Terraform の適用前に、muse server の送信元タグと、接続先 server の `tag:service` を確認する。
接続先で Tailscale が動作していることも必要になる。
`terraform/tailscale` で `setup.sh` を読み込み、`terraform plan` で既存 ACL を削除しないことを確認してから apply する。

```sh
cd terraform/tailscale
source ./setup.sh
terraform init
terraform plan -out=muse.tfplan
terraform apply muse.tfplan
cd ../..
```

RBAC、ValidatingAdmissionPolicy、token 用 Secret の定義は `flux/clusters/<cluster>/apps/muse-access/` から同期する。
PR マージ後、管理 context `soli.natsume` / `soli.meruto` で Flux の `muse-access` Kustomization を確認する。
Ready に加え、適用 revision がマージした commit を含むことと、`status.observedGeneration` が `metadata.generation` に一致することを確認する。

```sh
kubectl --context soli.natsume -n flux-system get kustomization muse-access \
  -o jsonpath='{.status.lastAppliedRevision} {.metadata.generation}/{.status.observedGeneration}{"\n"}'
kubectl --context soli.natsume -n muse-system get serviceaccount,secret
kubectl --context soli.natsume get clusterrole,clusterrolebinding muse-operator
kubectl --context soli.natsume get validatingadmissionpolicies -o custom-columns=NAME:.metadata.name,TYPECHECK:.status.typeChecking
```

ValidatingAdmissionPolicy の `TYPECHECK` が空であることを確認する。型エラーがあると、該当する更新はすべて拒否される。
token が生成されたことは、値を表示せずに長さだけで確認する。

```sh
kubectl --context soli.natsume -n muse-system get secret muse-operator-token \
  -o go-template='{{len .data.token}}{{"\n"}}'
```

Secret の token 値は Kubernetes が生成する。Git に値は保存しない。

## kubeconfig を生成する

管理端末に Python 3.9 以降と kubectl、両クラスタの管理 context を用意する。
生成先はリポジトリ外の0700ディレクトリとし、既存ファイルは上書きしない。
次の例は natsume の管理 context に設定された API endpoint をそのまま使う。
この endpoint に muse server からも到達できることを確認する。

```sh
mkdir -m 700 /path/to/private/muse-kubeconfigs
python3 scripts/export-muse-kubeconfig.py \
  --cluster natsume \
  --output /path/to/private/muse-kubeconfigs/natsume.yaml
```

meruto は LAN IP が管理 kubeconfig と証明書の SAN に設定されている。
Tailscale から接続する場合は、実機で確認した Tailscale endpoint を `--server` に指定する。
`--tls-server-name` には [meruto の host_vars](ansible/inventories/host_vars/meruto-02.yaml) の `k3s_tls_sans` にある IP を指定し、CA と SAN の検証を維持する。

```sh
python3 scripts/export-muse-kubeconfig.py \
  --cluster meruto \
  --server 'https://<meruto の Tailscale DNS 名または IP>:6443' \
  --tls-server-name '<証明書の SAN にある IP>' \
  --output /path/to/private/muse-kubeconfigs/meruto.yaml
```

`--server` と `--tls-server-name` は生成処理の API 接続にも使う。
管理用の user、証明書、exec plugin、proxy 設定は出力に含めない。
作成したファイルだけを安全な経路で muse server に配置し、親ディレクトリ0700・ファイル0600を維持する。
管理用 kubeconfig は muse server に渡さない。
既存の kubeconfig を置き換えるときは、新しいファイルで接続を確認してから古いファイルを muse server と保管先の両方から削除する。

認証には手動作成した ServiceAccount token Secret を使い、有効期限は設けない。
ServiceAccount の `secrets` フィールドには登録しない。
これは Kubernetes が生成する API 資格情報のため、OnePasswordItem から値を供給しない。
生成した kubeconfig を保管する場合は専用の 1Password item などを使い、本文をログや Git に残さない。

## 更新を確認する

muse server では専用 kubeconfig と context を明示する。
以下は natsume の例。meruto ではファイルと context を `meruto.yaml` / `meruto@muse` に替える。

```sh
export KUBECONFIG=/path/to/private/muse-kubeconfigs/natsume.yaml
kubectl --context natsume@muse get nodes
kubectl --context natsume@muse -n flux-system get gitrepositories,kustomizations
kubectl --context natsume@muse get helmreleases -A
kubectl --context natsume@muse get pods -A
kubectl --context natsume@muse auth can-i get pods --subresource=log -A
kubectl --context natsume@muse auth can-i patch kustomizations.kustomize.toolkit.fluxcd.io -A
kubectl --context natsume@muse auth can-i delete pods -A
kubectl --context natsume@muse auth can-i list secrets -A
kubectl --context natsume@muse auth can-i create pods --subresource=exec -A
kubectl --context natsume@muse auth can-i create pods -A
```

最初の5つは `yes`、最後の3つは `no` が正常で、`no` のときコマンドの終了コードは1になる。
`auth can-i` は RBAC だけを判定する。ValidatingAdmissionPolicy で拒否される変更も `yes` と表示される。

マージした commit が Flux の取得・適用 revision に含まれることを照合する。
反映が遅れているときは `flux reconcile` で取得と適用をやり直す。
対象リソースの generation と処理済み generation、Ready、workload の rollout、Pod、Events、必要なログを確認する。
Grafana の実際の収集・評価とアプリの状態も確認し、API 上の Ready だけで成功と判断しない。
直らなければ修正・revert を PR にし、ホストの変更や suspend などの直接操作は管理者へ引き継ぐ。

## アクセスを停止する

通常の廃止は、両クラスタの `muse-access` 登録を Git から削除し、Flux の prune を確認する。
トークンの漏えい時は、対象クラスタの管理 context で Flux の `muse-access` Kustomization を suspend し、`muse-operator` ClusterRoleBinding と `muse-operator-token` Secret を削除する。
自動再作成を防ぐため、Git 側の修正を反映してから同期を再開する。
認証が拒否されることと、不要な権限が残っていないことを確認する。

再発行する場合は、Secret を再作成して token の生成を待ち、別ファイルへ kubeconfig を出力する。
配布先・保管先を差し替え、古い kubeconfig を廃棄する。
監査ログでは `system:serviceaccount:muse-system:muse-operator` を主体として確認できる。
監査設定と調査経路は [MONITORING.md](MONITORING.md)、緊急管理経路は [RECOVERY.md](RECOVERY.md) を参照する。

参考: [ServiceAccount token の管理](https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/)、[ValidatingAdmissionPolicy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)、[Tailscale grants](https://tailscale.com/docs/reference/syntax/grants)。
