# Polestar Kubernetes Engine (PKE)

オンプレミスのKubernetesプラットフォーム Polestar Kubernetes Engine (PKE) をコードで構築・運用するためのリポジトリです。

## アーキテクチャ概要

### kkgクラスタ（Proxmox VE × HA Kubernetes）

物理ノード2台のProxmox VEクラスタ上に、HA構成のKubernetesクラスターを運用しています。

#### ハードウェア構成

| ホスト名  | CPU             | メモリ | ストレージ | IPアドレス    |
|-----------|-----------------|--------|-----------|---------------|
| kkg-pve3  | Ryzen 5 3400G   | 32GB   | 512GB SSD | 192.168.20.4  |
| kkg-pve4  | Ryzen 3 3300X   | 32GB   | 512GB SSD | 192.168.20.5  |

#### 仮想マシン構成

##### ロードバランサ（HAProxy + Keepalived）
| VM名     | CPU | メモリ | ディスク | IPアドレス     | ホストマシン |
|----------|-----|--------|----------|---------------|-------------|
| kkg-lb1  | 8   | 2GB    | 20GB     | 192.168.20.11 | kkg-pve3    |
| kkg-lb2  | 8   | 2GB    | 20GB     | 192.168.20.12 | kkg-pve4    |

##### Kubernetesコントロールプレーン
| VM名     | CPU | メモリ | ディスク | IPアドレス     | ホストマシン |
|----------|-----|--------|----------|---------------|-------------|
| kkg-cp1  | 8   | 32GB   | 100GB    | 192.168.20.13 | kkg-pve3    |
| kkg-cp2  | 8   | 16GB   | 100GB    | 192.168.20.14 | kkg-pve4    |
| kkg-cp3  | 8   | 16GB   | 100GB    | 192.168.20.15 | kkg-pve4    |

##### Kubernetesワーカーノード
| VM名     | CPU | メモリ | ディスク | IPアドレス     | ホストマシン |
|----------|-----|--------|----------|---------------|-------------|
| kkg-wk1  | 8   | 11GB   | 50GB     | 192.168.20.16 | kkg-pve3    |
| kkg-wk2  | 8   | 11GB   | 50GB     | 192.168.20.17 | kkg-pve4    |
| kkg-wk3  | 8   | 11GB   | 50GB     | 192.168.20.18 | kkg-pve3    |

## リポジトリ構成と役割

- `terraform/` Proxmox 上に VM 群をプロビジョニング。詳細は `terraform/kkg/README.md`。Tailscale ACL 管理は `terraform/tailscale/README.md`。
- `ansible/` VM の OS 設定、containerd、Kubernetes、LB（HAProxy/Keepalived）、監視エージェント、Tailscale などを自動化。詳細は `ansible/README.md`。
- `helmfile/` クラスターのブートストラップ（Cilium, 1Password Connect, ArgoCD）。詳細は `helmfile/README.md`。
- `argocd/` アプリケーションとプラットフォームコンポーネントのGitOps管理（ArgoCD App of Apps）。
- `vps/` VPSサーバー上のアプリケーション設定（Misskey など）。

## エンドツーエンド手順（概要）

1. インフラ作成（Proxmox 上に VM を作成）
   - `terraform/kkg/` ディレクトリでTerraformを実行
   - クラスター設定は `terraform/kkg/cluster-config.yaml` で管理
2. 基本セットアップ（OS・Kubernetes・LB 構築）
   - `ansible/site.yaml` で全自動、または `site-all.yaml` → `site-lb.yaml` → `site-k8s.yaml` の順に実行
   - バージョンやネットワークは `ansible/inventories/group_vars/internal.yaml` で管理
   - 現在のKubernetesバージョン: 1.35.0、containerdバージョン: 2.2.1
3. ブートストラップ（CNI & GitOps基盤）
   - `helmfile/helmfile.yaml` を適用して Cilium, 1Password Connect, ArgoCD をデプロイ
   - 詳細は `helmfile/README.md` を参照
4. アプリケーション同期（GitOps）
   - ArgoCDが `argocd/` ディレクトリの設定に基づき、その他の全コンポーネント（Cert Manager, Traefik, 監視スタック等）とアプリケーションを自動同期
5. 追加VPSアプリ
   - `vps/` 配下のVPSアプリケーション設定を適用

## 主要コンポーネント（抜粋）

### 基盤インフラ・ネットワーク
- ネットワーク/CNI: Cilium (v1.18.5)
- GitOps: ArgoCD
- Ingress/Proxy: Traefik (ArgoCD管理)
- 証明書/DNS: cert-manager, external-dns (ArgoCD管理)
- シークレット: 1Password Connect (v2.1.1)

### 監視・オブザーバビリティ（ArgoCD管理）
- メトリクス: Mimir Distributed
- ログ: Loki
- 可視化: Grafana
- 監視エージェント: Alloy
- アップタイム監視: Uptime Kuma

### ストレージ・データ（ArgoCD管理）
- 永続ストレージ: NFS Subdir External Provisioner
- オブジェクトストレージ: MinIO（Operator/Tenant）

### 外部接続・セキュリティ
- Zero Trust アクセス: Cloudflare Tunnel Ingress Controller

## 参照

- Terraform: `terraform/kkg/README.md`, `terraform/tailscale/README.md`
- Ansible: `ansible/README.md`
- Helmfile: `helmfile/README.md`

## アプリケーション・サービス

### カスタムアプリケーション（helmfile経由でデプロイ）
- **daypassed-bot** (v0.1.0): 日付関連Bot
- **mc-mirror-cronjob** (v0.2.0): MinecraftミラーCronJob
- **mk-stream** (v1.0.0): ストリーミングサービス用Helmチャート
- **navidrome** (v1.0.0): 音楽ストリーミングサーバー
- **note-tweet-connector** (v0.3.0): Note投稿連携サービス
- **spotify-nowplaying** (v1.0.0): Spotify再生状況表示サービス
- **subscription-manager** (v1.1.0): サブスクリプション管理サービス

### VPSアプリケーション（vps/）
- **Misskey**: 分散SNSプラットフォーム（PostgreSQL設定含む）

## 注意事項

- 構築・運用に必要なシークレット（Cloudflare/1Password など）は 1Password Vault に保管し、`helmfile/` の手順に従って参照してください。
- バージョンアップは Ansible の `upgrade-*.yaml` を使用できます（Kubernetes / containerd）。
- Terraform状態ファイルはCloudflare R2に保存されます。
- Load Balancer VIP: 192.168.20.10（HAProxy + Keepalived）
- ネットワークセグメント: 192.168.20.0/23
