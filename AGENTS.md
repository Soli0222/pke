# AGENTS.md

オンプレミス / 外部 VPS 上の Kubernetes プラットフォーム **Polestar Kubernetes Engine (PKE)** のリポジトリ。複数の独立した K3s クラスタ (`natsume` / `meruto`) を `cluster:` host variable で識別し、Ansible / Helmfile / Flux すべてをクラスター別に切り替えて運用する。

## リポジトリ構成

```
pke/
├── ansible/             # OS・ネットワーク・external etcd・K3s・Alloy・Longhorn ディスクの構成管理
├── helmfile/            # Cilium, 1Password Connect, Flux Operator のブートストラップ
│   ├── helmfile.yaml.gotmpl
│   ├── environments/<cluster>.yaml
│   ├── manifests/flux/<cluster>/fluxinstance.yaml
│   └── values/*.gotmpl
├── flux/clusters/<cluster>/ # Flux CD で同期するクラスタアプリケーション定義
├── terraform/tailscale/ # Tailscale ACL 管理
├── .github/             # GitHub Actions
└── renovate.json5       # Renovate 設定
```

## 管理対象クラスタ

| クラスタ | ノード | 役割 | 特記事項 |
|----------|--------|------|----------|
| `natsume` | `natsume-03` (control-plane), `natsume-06` / `natsume-07` (worker) | 本番ワークロード | Public IP 付き。`natsume-03` が external etcd / K3s server、`natsume-06` のみ Longhorn ディスクを持つ |
| `meruto` | `meruto-01` | 単一ノード | Private インターフェースのみ。Longhorn は既存 `ubuntu-vg` の空き領域を利用。Tailscale なし |

各ホストは `host_vars/<host>.yaml` に `cluster: <name>` を必ず設定する。Alloy / etcd / Flux などはこの値を起点にクラスター固有の設定を組み立てる。

## デプロイメントパイプライン

Ansible (OS / etcd / K3s / Longhorn ディスク) -> Helmfile `-e <cluster>` (CNI / Secret backend / Flux) -> Flux CD (クラスタアプリ GitOps)

- Terraform は現状 `terraform/tailscale/` で Tailscale ACL を管理する用途。
- Kubernetes 本体は `ansible/site-k3s.yaml` で構築する。`-l <cluster>_etcd` などでクラスター単位に流せる。
- Longhorn 用バッキングディスクは `ansible/prepare-longhorn-storage.yaml` で個別に整備する。
- Helmfile は K3s 構築後の初期ブートストラップを担当し、`-e natsume` / `-e meruto` で対応する kubeconfig context (`<cluster>@soli`) に対して実行する。
- Flux Operator が `helmfile/manifests/flux/<cluster>/fluxinstance.yaml` の `FluxInstance` を通じて `flux/clusters/<cluster>` を同期する。

## Ansible

- インベントリ: `ansible/inventories/hosts.yaml` (すべてのグループは `all.children` 配下に配置)
- 変数:
  - 共通: `ansible/inventories/group_vars/all.yaml`
  - etcd: `ansible/inventories/group_vars/etcd.yaml`
  - K3s: `ansible/inventories/group_vars/k3s_cluster.yaml`, `k3s_server.yaml`, `k3s_agent.yaml`
  - Longhorn: `ansible/inventories/group_vars/longhorn_storage.yaml`
  - ホスト個別: `ansible/inventories/host_vars/<host>.yaml` (`cluster:`、ネットワーク、必要に応じて `k3s_tls_sans` / `longhorn_storage_use_existing_vg` など)
- 主要 Playbook:
  - `site-k3s.yaml`: ノード初期設定、ネットワーク、UFW、external etcd、K3s server / agent 構築
  - `prepare-k3s-nodes.yaml`: OS / ネットワーク / sysctl / UFW のみを適用
  - `install-k3s-servers.yaml`: 既存ノードへ K3s server だけを展開
  - `prepare-longhorn-storage.yaml`: Longhorn 用パーティション / LV の作成・初期化
  - `add-etcd-member.yaml` / `remove-etcd-member.yaml`: 稼働中 etcd クラスタの member 追加・削除 (`-e etcd_member_host=<host>`)
  - `configure-k3s-registry-mtls.yaml`: K3s containerd の private registry mTLS 設定 (新規構築時のデッドロックを避けるため `site-k3s.yaml` には含めない)
  - `install-alloy.yaml`: Grafana Alloy 導入 (Mimir / Loki に `cluster=<host_vars cluster>` ラベルで送信)
  - `install-docker.yaml`: Docker Engine 導入
  - `upgrade-k3s.yaml`: K3s アップグレード
  - `upgrade-etcd.yaml`: etcd アップグレード
- ロールは `ansible/roles/` 配下。タスクは `tasks/main.yaml`、既定値は `defaults/main.yaml`、テンプレートは `templates/` に置く。
- Ansible YAML は `.yaml` 拡張子を使い、組み込みモジュールは FQCN (`ansible.builtin.*`) を優先する。

### ホストグループ

| グループ | 用途 |
|---------|------|
| `<cluster>_etcd` | クラスター別 etcd メンバー (例: `natsume_etcd`, `meruto_etcd`) |
| `etcd` | `<cluster>_etcd` の親 (children のみで構成) |
| `k3s_cluster` | K3s クラスタ全体 (children: `k3s_server`, `k3s_agent`) |
| `k3s_server` | K3s server ノード |
| `k3s_agent` | K3s agent ノード |
| `longhorn_storage` | Longhorn 用ディスクを切り出すノード |

`setup-etcd` ロールと `k3s_datastore_endpoint` (`group_vars/k3s_server.yaml`) は `groups[cluster + '_etcd']` を参照する。新規クラスター追加時は `<name>_etcd` 子グループを作り、host に `cluster: <name>` を設定すれば自動的にクラスター固有の etcd / k3s 設定が組み上がる。

`k3s_agent` の `k3s_token` は server (`groups['k3s_server'][0]`) の `/var/lib/rancher/k3s/server/node-token` を `install-k3s` ロールが自動で読み出して join する。`k3s_server` 側は新規構築時 `k3s_token` 未指定で k3s が自動生成する (`config.yaml` には `token` を書かない)。

### ロールごとの主要オプション

- `network`: `network_netplan.<global|private>` の `device` / `ipv4` / `ipv6` / `default_route` / `nameservers` / `routes` / `dhcp4` / `dhcp6` / `accept_ra` をホスト別に指定可能。`global.device` が空のホストは private のみ書き出される
- `ufw`: `ufw_global_interface` が空のホスト (private-only) では public ポート (80/443) のルールを自動スキップ
- `setup-etcd`: `etcd_leader_host` と `etcd_initial_cluster` は `groups[cluster + '_etcd']` から構築
- `install-k3s`: `k3s_token` 未指定なら自動生成、`k3s_external_ip_netplan_source` (`global` / `private` / `""`) と `k3s_external_ip_include_ipv4` / `k3s_external_ip_include_ipv6` で `node-external-ip` を制御。`k3s_include_tailscale_tls_sans: false` で Tailscale 不在ホストに対応。`k3s_tls_sans` で追加 SAN を host_vars 側で明示可能
- `longhorn-storage`: 既定では `longhorn_storage_parent_disk` のパーティションを切って新規 VG を作る。`longhorn_storage_use_existing_vg: true` を指定すると既存 VG (`longhorn_storage_vg_name`) の空き領域に LV を切るモードになり、パーティション/PV/VG 操作はスキップ
- `install-alloy`: 旧来の `cluster_name` ハードコードは廃止。Mimir / Loki への送信ラベルは host_vars の `cluster` を参照する

## Helmfile

- 定義: `helmfile/helmfile.yaml.gotmpl` (helmfile v1 / Go テンプレート対応のため `.gotmpl` 拡張子)
- 環境: `helmfile/environments/<cluster>.yaml` で Cilium CIDR、1Password アイテム名、`kubeContext` (`<cluster>@soli`) などクラスター固有の値を保持
- ブートストラップ対象 (全クラスター共通):
  - Cilium `1.19.3`
  - 1Password Connect `2.4.1`
  - Flux Operator `0.48.0`
- 実行: `helmfile -e natsume apply` / `helmfile -e meruto apply`。`helmDefaults.kubeContext` は env の `kubeContext` を流用するため、コンテキストを意識せず切り替えられる
- Flux Operator の postsync hook は `helmfile/manifests/flux/{{ .Environment.Name }}/fluxinstance.yaml` を適用する
- シークレット値は `helmfile/values/*.gotmpl` から `op` CLI 経由で参照する。実シークレットをリポジトリへ置かない

## Flux CD

- クラスタ定義: `flux/clusters/<cluster>/` (`natsume` / `meruto` の 2 クラスター)
- ルート: `flux/clusters/<cluster>/kustomization.yaml`
- アプリ単位:
  - `flux/clusters/<cluster>/apps/<app>/`
  - `namespace.yaml`, `kustomization.yaml`, `helmrelease-*.yaml`, `helmrepository-*.yaml` を基本形にする
  - Postgres を使うアプリでは CNPG `Cluster` (`cluster.yaml`) と、必要に応じて `objectstore.yaml` / `scheduledbackup.yaml` / `onepassworditem-cnpg-backup.yaml` を同じディレクトリに同梱する
- 同期単位:
  - `flux/clusters/<cluster>/kustomizations/<app>.yaml`
  - 既定で `interval: 10m`, `prune: true`, `wait: true`、適用順は `dependsOn` で制御する
- Secret 管理:
  - 既存アプリは `OnePasswordItem` と External Secrets を併用している
  - 新規 Secret は既存パターンに合わせ、平文 Secret をコミットしない

### natsume クラスターの管理コンポーネント

| 分類 | コンポーネント |
|------|---------------|
| 基盤 / CRD | `cnpg`, `cnpg-backup-config`, `cert-manager`, `cert-manager-config`, `external-secrets`, `prometheus-operator-crd` |
| ストレージ | `longhorn`, `longhorn-config` |
| ネットワーク | `traefik`, `external-dns`, `external-dns-config` |
| 監視 | `grafana`, `mimir`, `loki`, `alloy`, `kube-state-metrics`, `prometheus-blackbox-exporter`, `blackbox-exporter-probes`, `uptime-kuma` |
| アプリ | `daypassed-bot`, `emoji-service`, `mc-mirror-cronjob`, `misskey`, `misskey-stg`, `mk-stream`, `navidrome`, `note-tweet-connector`, `registry`, `rss-fetcher`, `spotify-nowplaying`, `spotify-reblend`, `sui`, `summaly` |
| 運用 | `renovate-operator` |

CNPG `Cluster` を持つアプリ: `grafana` / `misskey` / `spotify-nowplaying` / `spotify-reblend` / `sui` (いずれも `instances: 2`)。`grafana` と `misskey` は `barman-cloud.cloudnative-pg.io` plugin で R2 互換ストレージへ日次バックアップを取得する。`misskey` クラスタは pgroonga 拡張入りの `ghcr.io/soli0222/pgroonga-cnpg` イメージを使用、永続ストレージは 150Gi。

`cert-manager-config` には `letsencrypt-dns01` / `letsencrypt-http01` の ClusterIssuer に加え、Traefik mTLS 用の自己署名 CA / Certificate / TLSOption (`pke-natsume-mtls`) が含まれる。

### meruto クラスターの管理コンポーネント

| 分類 | コンポーネント |
|------|---------------|
| 基盤 / CRD | `cnpg`, `cert-manager`, `cert-manager-config` (dns01 ClusterIssuer のみ), `external-secrets`, `prometheus-operator-crd` |
| ストレージ | `longhorn`, `longhorn-config` |
| ネットワーク | `traefik`, `external-dns` |
| 監視 | `alloy`, `kube-state-metrics` |

natsume との主な差分:

- **alloy**: クラスター内に Mimir / Loki が無いため、natsume 側の外部公開エンドポイント `https://mimir.pstr.space` / `https://loki.pstr.space` へ mTLS 経由で送信。クライアント証明書は `OnePasswordItem` (`vaults/Kubernetes/items/pke_natsume_mtls`、ansible の `install-alloy` ロールと同じ参照先) で `Secret pke-natsume-mtls` を生成し、`/etc/cert/pke-natsume/` にマウント。Ingress 無効。`cluster = "meruto"` ラベルで送信
- **longhorn**: 単一ノード構成のため `defaultClassReplicaCount: 1`、Ingress 無効
- **external-dns**: `txtPrefix: meruto-` で natsume レコードと衝突回避
- **cert-manager-config**: `letsencrypt-dns01` ClusterIssuer + `cloudflare-api-token` `OnePasswordItem` のみ。`letsencrypt-http01` と Traefik mTLS 用の CA / TLSOption は含まない
- 含まれないコンポーネント: `cnpg-backup-config`, `external-dns-config`, Mimir / Loki / Grafana 等の監視スタック、blackbox-exporter、各種アプリケーション、`renovate-operator`

## 現行バージョン

| コンポーネント | バージョン |
|---------------|-----------|
| K3s | `v1.35.4+k3s1` |
| etcd | `3.6.11` |
| Cilium | `1.19.3` |
| 1Password Connect | `2.4.1` |
| Flux Operator | `0.48.0` |
| Flux distribution | `2.x` |
| Longhorn | `1.11.1` |
| external-dns | `1.21.1` |
| Tailscale Terraform provider | `0.28.0` |

## Terraform

- 現行 Terraform 管理対象は `terraform/tailscale/` の Tailscale ACL。
- State backend は Cloudflare R2 S3 互換 backend。
- `TAILSCALE_API_KEY` などの認証情報は環境変数またはローカル設定で扱い、コミットしない。

## ネットワーク

### natsume クラスター

- ノード: `natsume-03` (control-plane), `natsume-06` / `natsume-07` (worker)
- Public IPv4: `133.18.141.63/23` (03), `133.18.141.179/23` (06), `133.18.124.51/23` (07)
- Public IPv6: `2406:8c00:0:3464:133:18:141:63/64` (03), `2406:8c00:0:3464:133:18:141:179/64` (06), `2406:8c00:0:3459:133:18:124:51/64` (07)
- Private IPv4: `192.168.9.3/24` (03), `192.168.9.6/24` (06), `192.168.9.7/24` (07)
- Private IPv6: `fd00:192:168:9::3/64` (03), `fd00:192:168:9::6/64` (06), `fd00:192:168:9::7/64` (07)

### meruto クラスター

- ノード: `meruto-01` (single-node, private only)
- Private IPv4: `192.168.10.3/24`
- Private IPv6: `fd00:192:168:10::3/64`

### 共通

- Pod CIDR: `10.1.0.0/16`, `fd00:10:1::/64`
- Service CIDR: `10.2.0.0/16`, `fd00:10:2::/64`
- Cluster DNS: `10.2.0.10`, `fd00:10:2::a`
- K3s built-in `traefik` と `helm-controller` は無効化し、Ingress / Helm 管理は GitOps 側に寄せる。
- natsume クラスターでは各ノードの DNS レコード (`natsume-0X.str08.net` / `pstr.space` / `tailscale.str08.net`) を `flux/clusters/natsume/apps/external-dns-config/node-dnsendpoints.yaml` の `DNSEndpoint` として宣言する。meruto クラスターは Public IP を持たず `external-dns-config` を持たない (Ingress / Service による DNS 同期のみ)。

## コーディング規約

- YAML は既存のスタイルに合わせ、`.yaml` 拡張子を使う (helmfile の Go テンプレート対象だけ `.gotmpl`)。
- Ansible は FQCN を優先し、ロール境界を崩さない。クラスター固有の値は `host_vars` / 子グループ (`<cluster>_etcd` 等) に閉じ込める。
- Flux の新規アプリは `apps/<app>` と `kustomizations/<app>.yaml` の両方を追加し、ルート `kustomization.yaml` に登録する。複数クラスター対象の変更は両 `flux/clusters/<cluster>/` を揃える。
- HelmRelease の chart version は Renovate が追跡できる形で明示する。
- ノードを増減する場合は `hosts.yaml` / `host_vars/<node>.yaml` の追記と、natsume の場合は `flux/clusters/natsume/apps/external-dns-config/node-dnsendpoints.yaml` のレコード更新をセットで行う (meruto には `external-dns-config` が無いため不要)。
- 新規クラスター追加は: `hosts.yaml` に `<name>_etcd` 子グループ作成 → host_vars に `cluster: <name>` 設定 → `helmfile/environments/<name>.yaml` と `helmfile/manifests/flux/<name>/fluxinstance.yaml` 追加 → `flux/clusters/<name>/` 作成、の順。
- Git コミットメッセージは Conventional Commits (`feat`, `fix`, `chore` など) を使う。
- ドキュメント、コメント、運用メモは日本語可。
