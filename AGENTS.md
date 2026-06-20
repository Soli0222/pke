# AGENTS.md

このリポジトリは **Polestar Kubernetes Engine (PKE)** を管理する。
PKE はオンプレミスと外部 VPS 上の K3s クラスタを Ansible、Helmfile、Flux CD、Terraform で構成する Kubernetes platform である。
クラスタは host_vars の `cluster: <name>` で識別し、Ansible の inventory、Helmfile environment、Flux の同期 path をクラスタごとに切り替える。

## 現在の管理対象

| クラスタ | ノード | 役割 | 補足 |
|----------|--------|------|------|
| `natsume` | `natsume-03`, `natsume-08` | 本番ワークロード、監視基盤、DB | `natsume-03` は external etcd と K3s server、`natsume-08` は K3s agent、TopoLVM、Longhorn storage |
| `meruto` | `meruto-01` | 単一ノードクラスタ | private interface のみを使い、Longhorn は既存 `ubuntu-vg` の空き領域を使う |

`cluster:` は必須である。
`setup-etcd`、`k3s_datastore_endpoint`、Alloy の remote write label、Flux の同期 path はこの値に依存する。

## リポジトリ構成

```
pke/
├── ansible/                 # OS, network, external etcd, K3s, storage, Alloy, Falco
├── helmfile/                # Cilium, 1Password Connect, Flux Operator の bootstrap
│   ├── helmfile.yaml.gotmpl
│   ├── environments/<cluster>.yaml
│   ├── manifests/flux/<cluster>/fluxinstance.yaml
│   └── values/*.gotmpl
├── flux/clusters/<cluster>/ # Flux CD が同期するクラスタ別アプリ定義
├── terraform/github/        # GitHub repository settings と Actions secrets
├── terraform/tailscale/     # Tailscale ACL
├── .github/workflows/       # Renovate と flux-local diff
└── renovate.json5           # Renovate 設定
```

## デプロイの順序

Ansible でノードと Kubernetes を構築し、Helmfile で CNI と GitOps 基盤を入れ、Flux CD でクラスタアプリを同期する。
Terraform は Kubernetes 構築とは独立し、Tailscale ACL と GitHub 設定を管理する。

1. `ansible/prepare-k3s-nodes.yaml`
2. `ansible/site-k3s.yaml`
3. `helmfile -e <cluster> apply`
4. Flux Operator の `FluxInstance` による `flux/clusters/<cluster>` 同期

`prepare-k3s-nodes.yaml` は OS 設定、SSH sudo 用 authorized_keys、Netplan、sysctl、UFW、TopoLVM、Longhorn backing volume、Alloy、Falco を扱う。
`site-k3s.yaml` は external etcd、etcd maintenance、etcd precheck、K3s server、K3s agent、K3s containerd の private registry mTLS を扱う。

## Ansible

インベントリは `ansible/inventories/hosts.yaml` で管理する。
すべての group は `all.children` 配下に置く。

| グループ | 現在のホスト | 用途 |
|----------|--------------|------|
| `natsume_etcd` | `natsume-03` | natsume の external etcd |
| `meruto_etcd` | `meruto-01` | meruto の external etcd |
| `etcd` | `natsume_etcd`, `meruto_etcd` | etcd role の親グループ |
| `k3s_server` | `natsume-03`, `meruto-01` | K3s server |
| `k3s_agent` | `natsume-08` | K3s agent |
| `topolvm_storage` | `natsume-08` | TopoLVM 用 VG |
| `longhorn_storage` | `natsume-08`, `meruto-01` | Longhorn backing volume |

`setup-etcd` と `ansible/inventories/group_vars/k3s_server.yaml` は `groups[cluster + '_etcd']` を参照する。
新規クラスタを追加するときは `<cluster>_etcd` group を作り、host_vars に同じ `cluster:` を設定する。

`k3s_agent` は `ansible/inventories/group_vars/k3s_agent.yaml` の `k3s_server_host` と `k3s_server_url` で server に join する。
現行 inventory では `natsume-08` が `natsume-03` に join する。

### Playbook

| Playbook | 用途 |
|----------|------|
| `prepare-k3s-nodes.yaml` | OS、network、UFW、storage、Alloy、Falco |
| `site-k3s.yaml` | external etcd、K3s、registry mTLS |
| `add-etcd-member.yaml` | `-e etcd_member_host=<host>` で etcd member を追加 |
| `remove-etcd-member.yaml` | `-e etcd_member_host=<host>` で etcd member を削除 |
| `upgrade-k3s.yaml` | K3s server と agent の rolling upgrade |
| `upgrade-etcd.yaml` | etcd precheck、rolling upgrade、postcheck |
| `install-docker.yaml` | Docker Engine |

### Role Options

`network` は `network_netplan.<global|private>` の `device`、`ipv4`、`ipv6`、`default_route`、`nameservers`、`routes`、`dhcp4`、`dhcp6`、`accept_ra` を host_vars から読む。
`global.device` が空の host は private interface だけを設定する。

`ufw` は global interface がない host では public port rule を作らない。

`install-k3s` は `k3s_version: v1.36.1+k3s1` を使う。
K3s built-in の `helm-controller` と `traefik` は無効化する。
`k3s_external_ip_netplan_source` は `global`、`private`、空文字を受け取り、`node-external-ip` を制御する。
meruto は `k3s_include_tailscale_tls_sans: false` を使い、TLS SAN を host_vars で明示する。

`setup-etcd` は `etcd_version: 3.6.12` を使う。
snapshot は `/var/lib/etcd/snapshots` に置き、`etcd_maintenance_on_calendar: "*-*-* 00/6:00:00"` で maintenance timer を作る。

`topolvm` は natsume の `natsume-08` だけに適用する。
`/dev/vda4` に 300GiB の partition を作り、VG `topolvm` を構成する。

`longhorn-storage` は natsume の `natsume-08` では `/dev/vda5` を使う。
meruto の `meruto-01` は `longhorn_storage_use_existing_vg: true` と `longhorn_storage_vg_name: ubuntu-vg` を使う。

`install-alloy` は natsume の Mimir と Loki に送信する。
remote endpoint は `https://mimir.pstr.space/api/v1/push` と `https://loki.pstr.space/loki/api/v1/push` で、mTLS certificate は 1Password item `pke_natsume_mtls` から読む。

`install-falco` は systemd service と modern eBPF を使う。
K3s containerd の CRI socket は `/run/k3s/containerd/containerd.sock` で、metrics は `127.0.0.1:8765/metrics` に出す。

## Helmfile

`helmfile/helmfile.yaml.gotmpl` は Helmfile v1 の Go template として扱う。
environment は `helmfile/environments/natsume.yaml` と `helmfile/environments/meruto.yaml` である。

| Release | Version |
|---------|---------|
| Cilium | `1.19.5` |
| 1Password Connect | `2.4.1` |
| Flux Operator | `0.52.0` |

`helmDefaults.kubeContext` は environment の `kubeContext` を使う。
`helmfile -e natsume apply` は `natsume@soli`、`helmfile -e meruto apply` は `meruto@soli` に対して動く。
postsync hook は `helmfile/manifests/flux/{{ .Environment.Name }}/fluxinstance.yaml` を apply する。

## Flux CD

Flux root は `flux/clusters/<cluster>/kustomization.yaml` である。
アプリは `apps/<app>/` と `kustomizations/<app>.yaml` の組で追加する。
root `kustomization.yaml` への登録を忘れない。

HelmRelease の chart version は Renovate が追跡できる形で明示する。
Secret は 1Password Operator の `OnePasswordItem` を基本にし、平文 Secret をコミットしない。

### natsume Components

| 分類 | コンポーネント |
|------|----------------|
| 基盤と CRD | `cnpg`, `cnpg-backup-config`, `prometheus-operator-crd`, `cert-manager`, `cert-manager-config` |
| Storage | `longhorn`, `longhorn-config`, `topolvm` |
| Network と Security | `traefik`, `external-dns`, `external-dns-config`, `tetragon`, `tetragon-policies` |
| Observability | `kube-state-metrics`, `grafana`, `mimir`, `loki`, `alloy` |
| Apps | `misskey`, `note-tweet-connector`, `registry`, `spotify-nowplaying`, `spotify-reblend`, `sui`, `summaly` |

`cert-manager-config` は `letsencrypt-dns01`、`letsencrypt-http01`、Traefik mTLS 用 `pke-natsume-mtls` を持つ。
`external-dns-config` は natsume の入口と node record を `DNSEndpoint` で宣言する。
`cnpg-backup-config` は `cnpg-backup-flux-vars` を作り、misskey の `ObjectStore` に R2 endpoint を注入する。

### meruto Components

| 分類 | コンポーネント |
|------|----------------|
| 基盤と CRD | `cnpg`, `prometheus-operator-crd`, `cert-manager`, `cert-manager-config` |
| Storage | `longhorn`, `longhorn-config` |
| Network と Security | `traefik`, `external-dns`, `cloudflare-tunnel-ingress-controller`, `tetragon`, `tetragon-policies` |
| Observability | `alloy`, `kube-state-metrics`, `prometheus-blackbox-exporter`, `blackbox-exporter-probes`, `ix2215-snmp-exporter`, `vector` |
| Apps | `daypassed-bot`, `emoji-service`, `mc-mirror-cronjob`, `mk-stream`, `navidrome`, `rss-fetcher` |

meruto の `cert-manager-config` は `letsencrypt-dns01` のみを定義する。
`external-dns` は `txtPrefix: meruto-` を使う。
`cloudflare-tunnel-ingress-controller` は 1Password item `cloudflared-pke-meruto` から tunnel credential を読む。
`ix2215-snmp-exporter` は `192.168.10.1` を監視し、`vector` は syslog を natsume 側 Loki に送る。

## CNPG

CNPG `Cluster` は natsume 側だけにある。
`misskey`、`grafana`、`sui`、`spotify-reblend`、`spotify-nowplaying` はすべて `instances: 1` である。

`misskey` は `barman-cloud.cloudnative-pg.io` plugin で WAL archive と base backup を使う。
`grafana`、`sui`、`spotify-reblend`、`spotify-nowplaying` は `pg_dump` CronJob で R2 に dump を送る。
運用手順は `CNPG.md` を更新する。

## Terraform

`terraform/tailscale/` は Tailscale ACL を管理する。
provider は `tailscale/tailscale` `0.29.2` である。

`terraform/github/` は `Soli0222/*` の repository settings、default branch、GitHub Actions secrets を管理する。
provider は `integrations/github` `6.12.1` と `hashicorp/external` `2.4.0` である。
1Password から secret を読む helper は `terraform/github/op-read-secret.rb` である。

どちらの Terraform state も Cloudflare R2 の S3 互換 backend に置く。
認証情報は環境変数または `setup.sh` で注入し、リポジトリに置かない。

## Network

| Node | Public IPv4 | Public IPv6 | Private IPv4 | Private IPv6 |
|------|-------------|-------------|--------------|--------------|
| `natsume-03` | `133.18.141.63/23` | `2406:8c00:0:3464:133:18:141:63/64` | `192.168.9.3/24` | `fd00:192:168:9::3/64` |
| `natsume-08` | `133.18.125.154/23` | `2406:8c00:0:3459:133:18:125:154/64` | `192.168.9.8/24` | `fd00:192:168:9::8/64` |
| `meruto-01` | なし | なし | `192.168.10.3/24` | `fd00:192:168:10::3/64` |

| 項目 | 値 |
|------|----|
| Pod CIDR | `10.1.0.0/16`, `fd00:10:1::/64` |
| Service CIDR | `10.2.0.0/16`, `fd00:10:2::/64` |
| Cluster DNS | `10.2.0.10`, `fd00:10:2::a` |

## コーディング規約

YAML は `.yaml` を使う。
Helmfile の Go template だけ `.gotmpl` を使う。

Ansible built-in module は FQCN を優先する。
role boundary を崩さず、クラスタ固有値は host_vars、group_vars、`<cluster>_etcd` group に閉じ込める。

Flux の新規アプリは `apps/<app>/`、`kustomizations/<app>.yaml`、root `kustomization.yaml` を同時に追加する。
複数クラスタへ入れる変更は `flux/clusters/natsume/` と `flux/clusters/meruto/` の差分を明示する。

ノードを増減するときは `ansible/inventories/hosts.yaml` と `ansible/inventories/host_vars/<node>.yaml` を更新する。
natsume で DNS record が必要な場合は `flux/clusters/natsume/apps/external-dns-config/node-dnsendpoints.yaml` も更新する。

新規クラスタ追加は、`hosts.yaml` の `<cluster>_etcd`、host_vars の `cluster: <cluster>`、`helmfile/environments/<cluster>.yaml`、`helmfile/manifests/flux/<cluster>/fluxinstance.yaml`、`flux/clusters/<cluster>/` をそろえて行う。

Git commit message は Conventional Commits を使う。
ドキュメント、コメント、運用メモは日本語でよい。
