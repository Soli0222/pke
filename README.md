# Polestar Kubernetes Engine (PKE)

オンプレミスのKubernetesプラットフォーム Polestar Kubernetes Engine (PKE) をコードで構築・運用するためのリポジトリです。

## アーキテクチャ概要

### kkgクラスタ（Proxmox VE × HA Kubernetes）

物理ノード3台のProxmox VEクラスタ上に、HA構成のKubernetesクラスターを運用しています。

#### ハードウェア構成

| ホスト名  | CPU             | メモリ | ストレージ | IPアドレス    |
|-----------|-----------------|--------|-----------|---------------|
| kkg-pve1  | Intel N100      | 16GB   | 512GB SSD | 192.168.20.2  |
| kkg-pve2  | Intel N100      | 16GB   | 512GB SSD | 192.168.20.3  |
| kkg-pve3  | Ryzen 5 3400G   | 32GB   | 512GB SSD | 192.168.20.4  |

#### 仮想マシン構成

##### ロードバランサ（HAProxy + Keepalived）
| VM名     | CPU | メモリ | ディスク | IPアドレス     | ホストマシン |
|----------|-----|--------|----------|---------------|-------------|
| kkg-lb1  | 4   | 2GB    | 20GB     | 192.168.20.11 | kkg-pve1    |
| kkg-lb2  | 4   | 2GB    | 20GB     | 192.168.20.12 | kkg-pve2    |

##### Kubernetesコントロールプレーン
| VM名     | CPU | メモリ | ディスク | IPアドレス     | ホストマシン |
|----------|-----|--------|----------|---------------|-------------|
| kkg-cp1  | 4   | 4GB    | 50GB     | 192.168.20.13 | kkg-pve1    |
| kkg-cp2  | 4   | 4GB    | 50GB     | 192.168.20.14 | kkg-pve2    |
| kkg-cp3  | 4   | 4GB    | 50GB     | 192.168.20.15 | kkg-pve3    |

##### Kubernetesワーカーノード
| VM名     | CPU | メモリ | ディスク | IPアドレス     | ホストマシン |
|----------|-----|--------|----------|---------------|-------------|
| kkg-wk1  | 4   | 8GB    | 50GB     | 192.168.20.16 | kkg-pve1    |
| kkg-wk2  | 4   | 8GB    | 50GB     | 192.168.20.17 | kkg-pve2    |
| kkg-wk3  | 8   | 24GB   | 100GB    | 192.168.20.18 | kkg-pve3    |

## リポジトリ構成と役割

- `terraform/` Proxmox 上に VM 群をプロビジョニング（マルチスタック方式）。詳細は `terraform/README.md`。
- `ansible/` VM の OS 設定、containerd、Kubernetes、LB（HAProxy/Keepalived）、監視エージェントなどを自動化。詳細は `ansible/README.md`。
- `helmfile/` クラスター上のプラットフォーム/アプリ群を Helmfile でデプロイ（Cilium, cert-manager, Traefik, 1Password Connect, external-dns, Tailscale, VictoriaMetrics, Grafana, MinIO ほか）。詳細は `helmfile/README.md`。
- `manifest/` 一部の自作/個別アプリ用の Helm チャートや追加マニフェスト（例: loki, navidrome, spotify-nowplaying など）。

## エンドツーエンド手順（概要）

1. インフラ作成（Proxmox 上に VM を作成）
   - `terraform/stacks/kkg-pve{1,2,3}` を各ホストで適用
   - トポロジは `terraform/cluster_topology.yaml` で集中管理
2. 基本セットアップ（OS・Kubernetes・LB 構築）
   - `ansible/site.yaml` で全自動、または `site-all.yaml` → `site-lb.yaml` → `site-k8s.yaml` の順に実行
   - バージョンやネットワークは `ansible/inventories/kkg/group_vars/*.yml` で管理
3. プラットフォーム/アプリのデプロイ
   - `helmfile/helmfile.yaml` を適用
   - 1Password Connect、Cloudflare、Tailscale、DNS などの事前準備は `helmfile/README.md` を参照
4. 追加アプリ
   - `manifest/` 配下の各チャート/マニフェストを用途に応じて適用

## 主要コンポーネント（抜粋）

- ネットワーク/CNI: Cilium
- Ingress/Proxy: Traefik（Public/Tailscale）
- 証明書/DNS: cert-manager, external-dns（Cloudflare）
- シークレット: 1Password Connect（OnePasswordItem CRD）
- 監視: VictoriaMetrics, Grafana, Alloy（エージェント）
- ストレージ: NFS Subdir External Provisioner, MinIO（Operator/Tenant）

## 参照

- Terraform: `terraform/README.md`
- Ansible: `ansible/README.md`
- Helmfile: `helmfile/README.md`

## 注意事項

- 構築・運用に必要なシークレット（Cloudflare/Tailscale/1Password など）は 1Password Vault に保管し、`helmfile/` の手順に従って参照してください。
- バージョンアップは Ansible の `upgrade-*.yaml` を使用できます（Kubernetes / containerd）。
