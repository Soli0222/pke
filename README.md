# Polestar Kubernetes Engine

Polestar Kubernetes Engine（PKE）は、オンプレミスと外部 VPS の K3s クラスタを管理するリポジトリです。
Ansible でノードを構成し、Helmfile で基盤を導入した後、Flux CD でアプリを同期します。

## 文書

| 文書 | 用途 |
|---|---|
| [AGENTS.md](AGENTS.md) | リポジトリを変更するときの規約と確認事項 |
| [CNPG.md](CNPG.md) | PostgreSQL の構成、バックアップ、復元 |
| [MONITORING.md](MONITORING.md) | メトリクス・ログの収集、通知、障害調査 |
| [RECOVERY.md](RECOVERY.md) | 短期証明書による緊急操作、復旧データの暗号化保管 |
| [MUSE.md](MUSE.md) | muse の読み取り専用 kubeconfig、Tailscale 接続、更新確認 |
| [監視ルールの README](charts/monitoring-rules/README.md) | アラートの閾値、設定項目、ルール別の調査方法 |

## 管理対象

| クラスタ | ノード | 役割 | kubectl context |
|---|---|---|---|
| natsume | `natsume-03`、`natsume-08` | 本番アプリ、DB、監視基盤 | `soli.natsume` |
| meruto | `meruto-02` | 単一ノードのアプリ・外形監視 | `soli.meruto` |

`natsume-03` と `meruto-02` が各クラスタの K3s server と external etcd を持ち、`natsume-08` は K3s agent です。
各クラスタの etcd は単一 member です。
ノードの所属と接続先は [inventory](ansible/inventories/hosts.yaml) と [host_vars](ansible/inventories/host_vars/) に定義します。
K3s クラスタ名は host_vars の `cluster`、Helmfile environment、Flux のディレクトリ名でそろえます。

## 設定の場所

| パス | 管理するもの |
|---|---|
| [ansible/](ansible/) | OS、ネットワーク、etcd、K3s、ストレージ、ホストの Alloy・Falco |
| [helmfile/](helmfile/) | Cilium、1Password Connect / Operator、Flux Operator の導入 |
| [flux/clusters/natsume/](flux/clusters/natsume/) | natsume のアプリと監視基盤 |
| [flux/clusters/meruto/](flux/clusters/meruto/) | meruto のアプリと外形監視 |
| [charts/](charts/) | PKE 内で管理する Helm chart |
| [terraform/tailscale/](terraform/tailscale/) | Tailscale ACL |
| [terraform/github/](terraform/github/README.md) | GitHub リポジトリ設定と Actions secrets |
| [terraform/keycloak/](terraform/keycloak/README.md) | Keycloak の共通 realm、利用者・管理権限、Web / MCP クライアント、scope |
| [.github/workflows/](.github/workflows/) | CI と Renovate |

バージョンと導入アプリの一覧は設定ファイルを正とします。
ノード用ソフトウェアは Ansible の group_vars / role defaults、基盤 chart は `helmfile/helmfile.yaml.gotmpl`、アプリは各 HelmRelease と `charts/*/Chart.yaml`、Terraform provider は各ディレクトリの定義を参照してください。

### natsume の Pod 配置と再起動

アプリの固定先は各 HelmRelease の `nodeSelector`、DB は CNPG Cluster の `affinity.nodeSelector` で管理します。
Misskey の Web・migration Job・DB・Valkey と Summaly は同じノードに配置します。
bootstrap の固定先は Helmfile environment の `bootstrapNode`、Flux controller は [FluxInstance](helmfile/manifests/flux/natsume/fluxinstance.yaml) の patch で指定します。
DaemonSet は両ノードで稼働させ、Longhorn が生成する CSI controller・instance-manager の配置は Longhorn に任せます。

[k3s-workload-placement](flux/clusters/natsume/apps/k3s-workload-placement/) は K3s 管理の既存 Deployment に、CoreDNS の2 replicas・ノード間分散と metrics-server の固定先だけを SSA で追加します。
image・Corefile・Service・RBAC は K3s の管理を維持します。これらの Deployment は Flux の prune 対象にしません。
K3s 更新後にも CoreDNS が両ノードで Ready になっていることを確認します。

固定はノード停止時の別ノードへの移動を防ぎますが、可用性を保証するものではありません。
唯一の server / etcd の停止中は新規スケジューリングができず、DB の配置ノードが停止すると1 instance の DB も停止します。
Longhorn の Pod 配置とデータ replica の配置は別です。`best-effort` の data locality は同居を保証しないため、移動後は Pod の Ready に加え、volume の `healthy`、attach 先、稼働中 replica の `spec.nodeID` が意図したノードであることを確認してから元ノードを再起動します。
Misskey Valkey は RWO volume の付け替えに `Recreate` を使うため、反映時に短時間の停止が発生します。データ replica が移動しない場合は、元ノードを停止せず Longhorn の空き容量・scheduling 条件を確認します。

## 構築する

作業端末に Ansible、SSH agent、kubectl、Helm / Helmfile、1Password CLI と必要な Ansible collection を用意します。
ノードへの SSH・sudo、対象クラスタの kubeconfig、1Password の参照権限が必要です。
Helmfile は environment に指定した 1Password item から Connect の認証情報を読みます。

対象ノードの inventory と host_vars を整えてから、次の順で実行します。
以下は natsume の例です。meruto は `--limit meruto-02`、context と environment は `meruto` に替えます。

```sh
cd ansible
ansible-playbook -i inventories/hosts.yaml prepare-k3s-nodes.yaml --limit 'natsume-03,natsume-08'
ansible-playbook -i inventories/hosts.yaml site-k3s.yaml --limit 'natsume-03,natsume-08'
cd ../helmfile
helmfile -e natsume apply
```

`prepare-k3s-nodes.yaml` は OS・ネットワーク・ストレージ・Alloy・Falco、`site-k3s.yaml` は etcd・K3s・registry mTLS を構成します。
Helmfile の hook が FluxInstance を適用し、`flux/clusters/<cluster>` の同期を開始します。
Helmfile の release と hook の kubectl は、どちらも environment の `kubeContext` を接続先に使います。

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
| ホスト Falco の設定 | `configure-falco.yaml`。パッケージ更新なしで設定を1台ずつ反映。[検知通知と例外](MONITORING.md#falco) |
| ホスト Falco の更新 | `update-falco.yaml`。APT の最新版と設定を1台ずつ反映 |

Falco の更新は `k3s_cluster` が対象です。
`update-falco` ロールは `install-falco` を再利用し、パッケージまたは設定が変わった場合に Falco を再起動します。
`--limit` で対象ホストを指定し、先に check mode で差分を確認します。
次は natsume（`soli.natsume`）の例です。meruto（`soli.meruto`）は `--limit meruto-02` に替えます。
この操作は SSH 経由で実行し、kubectl context は使いません。

```sh
ansible-playbook -i inventories/hosts.yaml update-falco.yaml --limit 'natsume-03,natsume-08' --check --diff
ansible-playbook -i inventories/hosts.yaml update-falco.yaml --limit 'natsume-03,natsume-08'
```

## ストレージとネットワーク

natsume は Longhorn と TopoLVM、meruto は TopoLVM を使います。
Longhorn の既定 replica 数は1です。CNPG も全 DB が1 instance のため、バックアップと復元手順を含めて運用します。

- `natsume-08`: Ansible が `/dev/vda4` に TopoLVM、`/dev/vda5` に Longhorn の領域を構成します。
- `natsume-03`: Longhorn 600GB と TopoLVM 200GB が存在しますが、Ansible の storage role 対象には含めません。
- `meruto-02`: Ansible が T500（`/dev/disk/by-id/nvme-CT1000T500SSD8_2409474C8886`）の1パーティションに TopoLVM の VG を構成します。`ubuntu-vg` には `/var/lib/rancher`（150G）と `/var/lib/kubelet`（100G）の LV を K3s の導入前に作ります。TopoLVM を既定の StorageClass にするため、K3s の `local-storage` は無効にします。

両クラスタとも dual-stack です。
Pod CIDR は `10.1.0.0/16` / `fd00:10:1::/64`、Service CIDR は `10.2.0.0/16` / `fd00:10:2::/64` を使います。
meruto-02 の IPv6 のノード IP は LAN ではなく WireGuard の ULA（`fd00:10:99::2`）で、`k3s_additional_node_ips` で node-ip に足します。

meruto-02 は public interface を持たず、LAN（`192.168.50.0/24`）に IPv4 / IPv6 のデフォルトルートを置きません。
外への通信は IPv4 / IPv6 とも WireGuard（`wg0`）から lune-02 を通ります。wg0 が落ちると外への経路がなくなり、LAN から直接は出ません。
LAN の経路は、接続済みのサブネット（DNS の `192.168.50.1` を含む）、lune-02 の endpoint への host route、作業端末のセグメント（`192.168.20.0/24`）だけです。
Service の CIDR（`10.2.0.0/16`）は、送信元を `192.168.50.2` にして LAN 側で引かせます。
wg0 側の経路を引くと、ノード自身（hostNetwork の Pod を含む）から ClusterIP への接続の送信元が `10.99.0.2` になります。
転送先がノード自身の Service（`kubernetes` → `192.168.50.2:6443`）だと、NAT を戻した応答（送信元 ClusterIP → 宛先 `10.99.0.2`）が `lo` から入り直し、wg-quick のなりすまし防止のルール（`iifname != "wg0" ip daddr 10.99.0.2 fib saddr type != local drop`）で捨てられます。
転送先が Pod の Service は、masquerade で宛先が `cilium_host` のアドレスになるため影響を受けません。
IPv6 は LAN にアドレスがなく同じ手が使えないため、IPv6 で転送先がノード自身になる Service をノードから使う場合は、wg-quick に任せている経路となりすまし防止を自前の範囲に絞る必要があります（`kubernetes` の Service は IPv4 のみ）。
`prepare-k3s-nodes.yaml` は他の role より先に、wireguard-tools の導入、netplan の適用、WireGuard の起動の順で経路を切り替えます。作業端末への経路を入れてから wg0 を上げるので、Ansible の SSH は切れません。systemd-networkd が wg-quick の ip rule を消さないよう、`wireguard_networkd_keep_foreign_routes` で foreign の経路と rule を管理対象から外します。
ノードの IP・経路・DNS は host_vars、Cilium の設定は Helmfile environment を参照してください。
K3s built-in の Traefik と Helm controller は無効化し、Ingress とアプリの Helm release は Flux で管理します。
meruto は K3s 同梱の `gateway-api-crd` も無効にし、Gateway API の CRD は Flux の Traefik に任せます。

### 外部ゲートウェイ（lune-02）

`lune-02` は inventory の `edge_gateway` グループに属する VPS で、K3s ノードではありません。
WireGuard（`wg0`、`10.99.0.1/24` / `fd00:10:99::1/64`、51820/udp）で meruto のノードとつなぎ、次の2つを担います。

- ingress: HAProxy が IPv4 と IPv6 の 80/443 を TCP のまま受け、PROXY protocol v2 を付けて WireGuard の先の Traefik の NodePort（`10.99.0.2:30080` / `30443`）へ IPv4 で転送します。TLS は Traefik で終端します。
- egress: WireGuard から来た通信のうち、`edge_gateway_egress_sources`（IPv4）と `edge_gateway_egress_sources_ipv6`（IPv6）に定義した送信元だけを公開側の interface へ masquerade します。

ファイアウォールは nftables の `inet edge_gateway` table だけで管理します。
Tailscale が作る table を残すため、`flush ruleset` と nftables.service の stop は使いません。
受け付けるのは Tailscale の interface、WireGuard、Tailscale の直接接続用ポート、80/443 です。公開側の SSH は `edge_gateway_public_ssh_enabled` で切り替えます。
WireGuard を通る TCP の MSS は lune-02 の forward で両方向とも、IPv4 は `edge_gateway_tcp_mss`、IPv6 は `edge_gateway_tcp_mss_ipv6` 以下に下げます。

meruto の Traefik は web / websecure の両方で `10.99.0.1` からの PROXY protocol だけを信頼します。
### meruto の LoadBalancer

meruto は K3s の servicelb を無効にし、[MetalLB](flux/clusters/meruto/apps/metallb-config/) で LoadBalancer の IP を割り当てます。

| pool | アドレス | 広告 | 用途 |
|---|---|---|---|
| `lune` | lune-02 の `133.18.187.207` / `2406:8c00:0:3476:133:18:187:207` | しない | lune-02 経由で公開する Service。`metallb.io/address-pool: lune` を付けたものだけに割り当て、共有するときは `metallb.io/allow-shared-ip: lune` を付ける |
| `lan` | `192.168.50.200-192.168.50.249` | enp3s0 で L2 | LAN 内だけで使う Service。LAN に IPv6 はないため IPv4 の SingleStack にする |

`lune` pool の Service には lune-02 の HAProxy から wg0 経由で NodePort に届きます。
kube-proxy の `nodeport-addresses` を wg0 のアドレス（`10.99.0.2/32`、`fd00:10:99::2/128`）に限定し、NodePort を LAN に出しません。
`lan` pool の Service には L2 で広告した IP に直接届くため、NodePort は使いません。
新しい Service を lune-02 経由で公開するときは、NodePort を固定し、lune-02 の `edge_gateway_haproxy_backends` に転送先を足します。

Traefik は `lune` pool の dual-stack の LoadBalancer で、status は lune-02 の v4 / v6 です。external-dns は各 Ingress のホスト名に lune-02 の A / AAAA を登録します。
[DNSEndpoint](flux/clusters/meruto/apps/external-dns-config/ingress-dnsendpoints.yaml) は natsume と同じく、入口の名前 `meruto.pstr.space` を登録します。
lune-02 のアドレスを変えるときは、lune-02 の host_vars、`lune` pool、この DNSEndpoint をそろえます。

lune-02 にもホストの Alloy と Falco を入れ、`cluster: meruto` として監視します。
HAProxy の指標（組み込みの exporter、`127.0.0.1:8405`）と、WireGuard の handshake と転送量（`pke-wireguard-metrics.timer` が textfile に書き出す。meruto-02 も同じ）を収集し、`pke.edge` のルールで評価します。
lune-02 はコンテナを動かさないため、Falco の K3s 用の再起動 unit（`falco_cri_socket_restart_enabled: false`）と、container plugin の監視（`containerlessHosts`）から外します。

Tailscale は lune-02 への SSH にだけ使い、Ansible では管理しません。sshd はループバック（`127.0.0.1`）だけで待ち受けます。
WireGuard の鍵は 1Password の item（`wireguard_onepassword_item`）に `private_key` と `public_key` の field で置きます。
peer 側の公開鍵も同じ形式の item から読みます。

```sh
cd ansible
ansible-playbook -i inventories/hosts.yaml prepare-edge-gateway.yaml --limit lune-02 --check --diff
ansible-playbook -i inventories/hosts.yaml prepare-edge-gateway.yaml --limit lune-02
```

`network` role は既存の netplan を削除して置き換えます。設定を誤ると SSH できなくなるため、事業者のコンソールに入れることを確認してから適用します。

## 変更を反映する

変更は PR でレビューし、マージ後に Flux の同期とアプリの状態を確認します。
Ansible と Terraform の変更は、それぞれの適用作業が必要です。

```sh
kubectl --context soli.natsume -n flux-system get kustomizations
kubectl --context soli.natsume get helmreleases -A
kubectl --context soli.meruto -n flux-system get kustomizations
kubectl --context soli.meruto get helmreleases -A
```

CI は `flate` による両クラスタの HelmRelease / Kustomization 差分、ローカル chart の version 更新、監視ルールと通知設定を検証します。
対象は workflow の path filter に従います。
CI が取得する OCI chart は匿名 pull 可能にし、認証情報は 1Password や Actions secrets で管理します。
Renovate は依存バージョンを更新し、PKE の chart 内の image 更新には chart version の patch bump も行います。
