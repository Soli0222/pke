# Polestar Kubernetes Engine

Polestar Kubernetes Engine（PKE）は、オンプレミスと外部 VPS の K3s クラスタを管理するリポジトリです。
Ansible でノードを構成し、Helmfile で基盤を導入した後、Flux CD でアプリを同期します。

## 文書

| 文書 | 用途 |
|---|---|
| [AGENTS.md](AGENTS.md) | リポジトリを変更するときの規約と確認事項 |
| [CNPG.md](CNPG.md) | PostgreSQL の構成、バックアップ、復元 |
| [MONITORING.md](MONITORING.md) | メトリクス・ログの収集、通知、障害調査 |
| [監視ルールの README](charts/monitoring-rules/README.md) | アラートの閾値、設定項目、ルール別の調査方法 |

## 管理対象

| クラスタ | ノード | 役割 | kubectl context |
|---|---|---|---|
| natsume | `natsume-03`、`natsume-08` | 本番アプリ、DB、監視基盤 | `natsume@soli` |
| meruto | `meruto-01` | 単一ノードのアプリ・拠点監視 | `meruto@soli` |

`natsume-03` と `meruto-01` が各クラスタの K3s server と external etcd を持ち、`natsume-08` は K3s agent です。
各クラスタの etcd は単一 member です。
Kubernetes 外の `amemado-01` は、Ansible の `openclaw` グループと `site-openclaw.yaml` で管理します。

ノードの所属と接続先は [inventory](ansible/inventories/hosts.yaml) と [host_vars](ansible/inventories/host_vars/) に定義します。
K3s クラスタ名は host_vars の `cluster`、Helmfile environment、Flux のディレクトリ名でそろえます。

## 設定の場所

| パス | 管理するもの |
|---|---|
| [ansible/](ansible/) | OS、ネットワーク、etcd、K3s、ストレージ、ホストの Alloy・Falco |
| [helmfile/](helmfile/) | Cilium、1Password Connect / Operator、Flux Operator の導入 |
| [flux/clusters/natsume/](flux/clusters/natsume/) | natsume のアプリと監視基盤 |
| [flux/clusters/meruto/](flux/clusters/meruto/) | meruto のアプリと拠点監視 |
| [charts/](charts/) | PKE 内で管理する Helm chart |
| [terraform/tailscale/](terraform/tailscale/) | Tailscale ACL |
| [terraform/github/](terraform/github/README.md) | GitHub リポジトリ設定と Actions secrets |
| [terraform/auth0/](terraform/auth0/README.md) | sui の Auth0 アプリ、DB 接続、ユーザー |
| [.github/workflows/](.github/workflows/) | CI と Renovate |

バージョンと導入アプリの一覧は設定ファイルを正とします。
ノード用ソフトウェアは Ansible の group_vars / role defaults、基盤 chart は `helmfile/helmfile.yaml.gotmpl`、アプリは各 HelmRelease と `charts/*/Chart.yaml`、Terraform provider は各ディレクトリの定義を参照してください。

## 構築する

作業端末に Ansible、SSH agent、kubectl、Helm / Helmfile、1Password CLI と必要な Ansible collection を用意します。
ノードへの SSH・sudo、対象クラスタの kubeconfig、1Password の参照権限が必要です。
Helmfile は environment に指定した 1Password item から Connect の認証情報を読みます。

対象ノードの inventory と host_vars を整えてから、次の順で実行します。
以下は natsume の例です。meruto は `--limit meruto-01`、context と environment は `meruto` に替えます。

```sh
cd ansible
ansible-playbook -i inventories/hosts.yaml prepare-k3s-nodes.yaml --limit 'natsume-03,natsume-08'
ansible-playbook -i inventories/hosts.yaml site-k3s.yaml --limit 'natsume-03,natsume-08'
cd ../helmfile
kubectl config use-context natsume@soli
helmfile -e natsume apply
```

`prepare-k3s-nodes.yaml` は OS・ネットワーク・ストレージ・Alloy・Falco、`site-k3s.yaml` は etcd・K3s・registry mTLS を構成します。
Helmfile の hook が FluxInstance を適用し、`flux/clusters/<cluster>` の同期を開始します。
Helmfile の release は environment の `kubeContext` を使いますが、hook の kubectl も同じ接続先になるよう current context をそろえてください。

Terraform は Kubernetes の構築とは独立しています。
各ディレクトリの `setup.sh` で認証情報を読み込み、plan を確認してから apply します。
state は Cloudflare R2 の S3 互換 backend に保存します。

ノードの保守には専用 playbook を使います。Ansible の作業ディレクトリは `ansible/` です。

| 操作 | Playbook |
|---|---|
| K3s の更新 | `upgrade-k3s.yaml` |
| etcd の更新 | `upgrade-etcd.yaml` |
| etcd member の追加・削除 | `add-etcd-member.yaml` / `remove-etcd-member.yaml`。`-e etcd_member_host=<host>` で対象を指定 |
| ホスト Alloy の監視設定更新 | `update-alloy-monitoring.yaml`。[反映手順](MONITORING.md#ホスト-alloy-の設定) |

## ストレージとネットワーク

natsume は Longhorn と TopoLVM、meruto は Longhorn を使います。
Longhorn の既定 replica 数は1です。CNPG も全 DB が1 instance のため、バックアップと復元手順を含めて運用します。

- `natsume-08`: Ansible が `/dev/vda4` に TopoLVM、`/dev/vda5` に Longhorn の領域を構成します。
- `natsume-03`: Longhorn 600GB と TopoLVM 200GB が存在しますが、Ansible の storage role 対象には含めません。
- `meruto-01`: Longhorn は既存 VG `ubuntu-vg` の空き領域を使います。public interface はありません。

両クラスタとも dual-stack です。
Pod CIDR は `10.1.0.0/16` / `fd00:10:1::/64`、Service CIDR は `10.2.0.0/16` / `fd00:10:2::/64` を使います。
ノードの IP・経路・DNS は host_vars、Cilium の設定は Helmfile environment を参照してください。
K3s built-in の Traefik と Helm controller は無効化し、Ingress とアプリの Helm release は Flux で管理します。

## 変更を反映する

変更は PR でレビューし、マージ後に Flux の同期とアプリの状態を確認します。
Ansible と Terraform の変更は、それぞれの適用作業が必要です。

```sh
kubectl --context natsume@soli -n flux-system get kustomizations
kubectl --context natsume@soli get helmreleases -A
kubectl --context meruto@soli -n flux-system get kustomizations
kubectl --context meruto@soli get helmreleases -A
```

CI は `flate` による両クラスタの HelmRelease / Kustomization 差分、ローカル chart の version 更新、監視ルールと通知設定を検証します。
対象は workflow の path filter に従います。
CI が取得する OCI chart は匿名 pull 可能にし、認証情報は 1Password や Actions secrets で管理します。
Renovate は依存バージョンを更新し、PKE の chart 内の image 更新には chart version の patch bump も行います。
