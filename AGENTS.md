# AGENTS.md

オンプレミス / 外部 VPS 上の Kubernetes プラットフォーム **Polestar Kubernetes Engine (PKE)** のリポジトリ。

## リポジトリ構成

```
pke/
├── ansible/             # OS・ネットワーク・external etcd・K3s・Alloy の構成管理
├── helmfile/            # Cilium, 1Password Connect, Flux Operator のブートストラップ
├── flux/                # Flux CD で同期するクラスタアプリケーション定義
├── terraform/tailscale/ # Tailscale ACL 管理
├── .github/             # GitHub Actions
└── renovate.json5       # Renovate 設定
```

## デプロイメントパイプライン

Ansible (OS / etcd / K3s) -> Helmfile (CNI / Secret backend / Flux) -> Flux CD (クラスタアプリ GitOps)

- Terraform は現状 `terraform/tailscale/` で Tailscale ACL を管理する用途。
- Kubernetes 本体は `ansible/site-k3s.yaml` で構築する。
- Helmfile は K3s 構築後の初期ブートストラップを担当する。
- Flux Operator が `helmfile/manifests/flux/fluxinstance.yaml` の `FluxInstance` を通じて `flux/clusters/natsume` を同期する。

## Ansible

- インベントリ: `ansible/inventories/hosts.yaml`
- 変数:
  - 共通: `ansible/inventories/group_vars/all.yaml`
  - etcd: `ansible/inventories/group_vars/etcd.yaml`
  - K3s: `ansible/inventories/group_vars/k3s_cluster.yaml`, `k3s_server.yaml`, `k3s_agent.yaml`
  - ホスト個別: `ansible/inventories/host_vars/*.yaml`
- 主要 Playbook:
  - `site-k3s.yaml`: ノード初期設定、ネットワーク、UFW、external etcd、K3s server / agent 構築
  - `install-alloy.yaml`: Grafana Alloy 導入
  - `upgrade-k3s.yaml`: K3s アップグレード
  - `upgrade-etcd.yaml`: etcd アップグレード
- ロールは `ansible/roles/` 配下。タスクは `tasks/main.yaml`、既定値は `defaults/main.yaml`、テンプレートは `templates/` に置く。
- Ansible YAML は `.yaml` 拡張子を使い、組み込みモジュールは FQCN (`ansible.builtin.*`) を優先する。

### 現行ホストグループ

| グループ | 用途 |
|---------|------|
| `etcd` | external etcd ノード |
| `k3s_cluster` | K3s クラスタ全体 |
| `k3s_server` | K3s server ノード |
| `k3s_agent` | K3s agent ノード |

現行インベントリでは `natsume-02` が `etcd` と `k3s_server` を兼ねる。`k3s_agent` は空。

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
- 同期単位:
  - `flux/clusters/natsume/kustomizations/<app>.yaml`
  - `dependsOn`, `wait`, `prune` で適用順と削除を制御する。
- Secret 管理:
  - 既存アプリは `OnePasswordItem` と External Secrets を併用している。
  - 新規 Secret は既存パターンに合わせ、平文 Secret をコミットしない。

## 現行バージョン

| コンポーネント | バージョン |
|---------------|-----------|
| K3s | `v1.35.3+k3s1` |
| etcd | `3.6.10` |
| Cilium | `1.19.3` |
| 1Password Connect | `2.4.1` |
| Flux Operator | `0.48.0` |
| Flux distribution | `2.x` |
| Tailscale Terraform provider | `0.28.0` |

## Terraform

- 現行 Terraform 管理対象は `terraform/tailscale/` の Tailscale ACL。
- State backend は Cloudflare R2 S3 互換 backend。
- `TAILSCALE_API_KEY` などの認証情報は環境変数またはローカル設定で扱い、コミットしない。

## ネットワーク

- 現行 K3s ノード: `natsume-02`
- Public IPv4: `133.18.115.105/23`
- Public IPv6: `2406:8c00:0:3452:133:18:115:105/64`
- Private IPv4: `192.168.9.2/24`
- Private IPv6: `fd00:192:168:9::2/64`
- Pod CIDR: `10.1.0.0/16`, `fd00:10:1::/64`
- Service CIDR: `10.2.0.0/16`, `fd00:10:2::/64`
- Cluster DNS: `10.2.0.10`, `fd00:10:2::a`
- K3s built-in `traefik` と `helm-controller` は無効化し、Ingress / Helm 管理は GitOps 側に寄せる。

## コーディング規約

- YAML は既存のスタイルに合わせ、`.yaml` 拡張子を使う。
- Ansible は FQCN を優先し、ロール境界を崩さない。
- Flux の新規アプリは `apps/<app>` と `kustomizations/<app>.yaml` の両方を追加し、ルート `kustomization.yaml` に登録する。
- HelmRelease の chart version は Renovate が追跡できる形で明示する。
- Git コミットメッセージは Conventional Commits (`feat`, `fix`, `chore` など) を使う。
- ドキュメント、コメント、運用メモは日本語可。
