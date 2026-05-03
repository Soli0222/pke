# AGENTS.md

オンプレミス / 外部 VPS 上の Kubernetes プラットフォーム **Polestar Kubernetes Engine (PKE)** のリポジトリ。

## リポジトリ構成

```
pke/
├── ansible/             # OS・ネットワーク・external etcd・K3s・Alloy・Longhorn ディスクの構成管理
├── helmfile/            # Cilium, 1Password Connect, Flux Operator のブートストラップ
├── flux/                # Flux CD で同期するクラスタアプリケーション定義
├── terraform/tailscale/ # Tailscale ACL 管理
├── .github/             # GitHub Actions
└── renovate.json5       # Renovate 設定
```

## デプロイメントパイプライン

Ansible (OS / etcd / K3s / Longhorn ディスク) -> Helmfile (CNI / Secret backend / Flux) -> Flux CD (クラスタアプリ GitOps)

- Terraform は現状 `terraform/tailscale/` で Tailscale ACL を管理する用途。
- Kubernetes 本体は `ansible/site-k3s.yaml` で構築する。
- Longhorn 用バッキングディスクは `ansible/prepare-longhorn-storage.yaml` で個別に整備する。
- Helmfile は K3s 構築後の初期ブートストラップを担当する。
- Flux Operator が `helmfile/manifests/flux/fluxinstance.yaml` の `FluxInstance` を通じて `flux/clusters/natsume` を同期する。

## Ansible

- インベントリ: `ansible/inventories/hosts.yaml`
- 変数:
  - 共通: `ansible/inventories/group_vars/all.yaml`
  - etcd: `ansible/inventories/group_vars/etcd.yaml`
  - K3s: `ansible/inventories/group_vars/k3s_cluster.yaml`, `k3s_server.yaml`, `k3s_agent.yaml`
  - Longhorn: `ansible/inventories/group_vars/longhorn_storage.yaml`
  - ホスト個別: `ansible/inventories/host_vars/*.yaml`
- 主要 Playbook:
  - `site-k3s.yaml`: ノード初期設定、ネットワーク、UFW、external etcd、K3s server / agent 構築
  - `prepare-k3s-nodes.yaml`: OS / ネットワーク / sysctl / UFW のみを適用
  - `install-k3s-servers.yaml`: 既存ノードへ K3s server だけを展開
  - `prepare-longhorn-storage.yaml`: Longhorn 用パーティションの作成・初期化
  - `add-etcd-member.yaml` / `remove-etcd-member.yaml`: 稼働中 etcd クラスタの member 追加・削除 (`-e etcd_member_host=<host>`)
  - `configure-k3s-registry-mtls.yaml`: K3s containerd の private registry mTLS 設定 (新規構築時のデッドロックを避けるため `site-k3s.yaml` には含めない)
  - `install-alloy.yaml`: Grafana Alloy 導入
  - `upgrade-k3s.yaml`: K3s アップグレード
  - `upgrade-etcd.yaml`: etcd アップグレード
- ロールは `ansible/roles/` 配下。タスクは `tasks/main.yaml`、既定値は `defaults/main.yaml`、テンプレートは `templates/` に置く。
- Ansible YAML は `.yaml` 拡張子を使い、組み込みモジュールは FQCN (`ansible.builtin.*`) を優先する。

### 現行ホストグループ

| グループ | 用途 |
|---------|------|
| `etcd` | external etcd ノード |
| `k3s_cluster` | K3s クラスタ全体 (children: `k3s_server`, `k3s_agent`) |
| `k3s_server` | K3s server ノード |
| `k3s_agent` | K3s agent ノード |
| `longhorn_storage` | Longhorn 用ディスクを切り出すノード |

現行インベントリでは control-plane と worker を分離した 3 ノード構成。`natsume-03` が `etcd` / `k3s_server` / `longhorn_storage`、`natsume-06` が `k3s_agent` / `longhorn_storage`、`natsume-07` が `k3s_agent` (Longhorn ディスクなし) に所属する。`k3s_agent` の `k3s_token` は server (`groups['k3s_server'][0]`) の `/var/lib/rancher/k3s/server/node-token` を `install-k3s` role が自動で読み出して join する。

## Helmfile

- 定義: `helmfile/helmfile.yaml`
- ブートストラップ対象:
  - Cilium `1.19.3`
  - 1Password Connect `2.4.1`
  - Flux Operator `0.48.0`
- Flux Operator の postsync hook で `helmfile/manifests/flux/fluxinstance.yaml` を適用する。
- シークレット値は `helmfile/values/*.gotmpl` から参照する。実シークレットをリポジトリへ置かない。

## Flux CD

- クラスタ定義: `flux/clusters/natsume/`
- ルート: `flux/clusters/natsume/kustomization.yaml`
- アプリ単位:
  - `flux/clusters/natsume/apps/<app>/`
  - `namespace.yaml`, `kustomization.yaml`, `helmrelease-*.yaml`, `helmrepository-*.yaml` を基本形にする。
  - Postgres を使うアプリでは CNPG `Cluster` (`cluster.yaml`) と、必要に応じて `objectstore.yaml` / `scheduledbackup.yaml` / `onepassworditem-cnpg-backup.yaml` を同じディレクトリに同梱する。
- 同期単位:
  - `flux/clusters/natsume/kustomizations/<app>.yaml`
  - 既定で `interval: 10m`, `prune: true`, `wait: true`、適用順は `dependsOn` で制御する。
- Secret 管理:
  - 既存アプリは `OnePasswordItem` と External Secrets を併用している。
  - 新規 Secret は既存パターンに合わせ、平文 Secret をコミットしない。

### 管理コンポーネント (現行)

| 分類 | コンポーネント |
|------|---------------|
| 基盤 / CRD | `cnpg`, `cnpg-backup-config`, `cert-manager`, `cert-manager-config`, `external-secrets`, `prometheus-operator-crd` |
| ストレージ | `longhorn` |
| ネットワーク | `traefik`, `external-dns`, `external-dns-config` |
| 監視 | `grafana`, `mimir`, `loki`, `alloy`, `kube-state-metrics`, `prometheus-blackbox-exporter`, `blackbox-exporter-probes`, `uptime-kuma` |
| アプリ | `daypassed-bot`, `emoji-service`, `mc-mirror-cronjob`, `misskey`, `mk-stream`, `navidrome`, `note-tweet-connector`, `registry`, `rss-fetcher`, `spotify-nowplaying`, `spotify-reblend`, `sui`, `summaly` |
| 運用 | `renovate-operator` |

CNPG `Cluster` を持つアプリ: `grafana` / `misskey` / `spotify-nowplaying` / `spotify-reblend` / `sui` (いずれも `instances: 2`)。`grafana` と `misskey` は `barman-cloud.cloudnative-pg.io` plugin で R2 互換ストレージへ日次バックアップを取得する。

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

- 現行 K3s ノード: `natsume-03` (control-plane), `natsume-06` / `natsume-07` (worker)
- Public IPv4: `133.18.141.63/23` (03), `133.18.141.179/23` (06), `133.18.124.51/23` (07)
- Public IPv6: `2406:8c00:0:3464:133:18:141:63/64` (03), `2406:8c00:0:3464:133:18:141:179/64` (06), `2406:8c00:0:3459:133:18:124:51/64` (07)
- Private IPv4: `192.168.9.3/24` (03), `192.168.9.6/24` (06), `192.168.9.7/24` (07)
- Private IPv6: `fd00:192:168:9::3/64` (03), `fd00:192:168:9::6/64` (06), `fd00:192:168:9::7/64` (07)
- Pod CIDR: `10.1.0.0/16`, `fd00:10:1::/64`
- Service CIDR: `10.2.0.0/16`, `fd00:10:2::/64`
- Cluster DNS: `10.2.0.10`, `fd00:10:2::a`
- K3s built-in `traefik` と `helm-controller` は無効化し、Ingress / Helm 管理は GitOps 側に寄せる。
- 各ノードの DNS レコード (`natsume-0X.str08.net` / `pstr.space` / `tailscale.str08.net`) は `flux/clusters/natsume/apps/external-dns-config/node-dnsendpoints.yaml` で `DNSEndpoint` として宣言する。

## コーディング規約

- YAML は既存のスタイルに合わせ、`.yaml` 拡張子を使う。
- Ansible は FQCN を優先し、ロール境界を崩さない。
- Flux の新規アプリは `apps/<app>` と `kustomizations/<app>.yaml` の両方を追加し、ルート `kustomization.yaml` に登録する。
- HelmRelease の chart version は Renovate が追跡できる形で明示する。
- ノードを増減する場合は `hosts.yaml` / `host_vars/<node>.yaml` の追記と、`flux/clusters/natsume/apps/external-dns-config/node-dnsendpoints.yaml` のレコード更新をセットで行う。
- Git コミットメッセージは Conventional Commits (`feat`, `fix`, `chore` など) を使う。
- ドキュメント、コメント、運用メモは日本語可。
