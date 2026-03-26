# AGENTS.md

オンプレミス Kubernetes プラットフォーム **Polestar Kubernetes Engine (PKE)** のリポジトリ。

## リポジトリ構成

```
pke/
├── terraform/kkg/       # Proxmox VM プロビジョニング
├── ansible/             # OS・K8s・LB・監視の構成管理
├── helmfile/            # ブートストラップ (Cilium, 1Password, ArgoCD)
├── argocd/              # GitOps アプリケーション定義 (App of Apps)
├── scripts/             # CI/CD ヘルパー
└── .github/             # GitHub Actions
```

## デプロイメントパイプライン

Terraform (VM作成) → Ansible (OS・K8s構築) → Helmfile (CNI・ArgoCD) → ArgoCD (全アプリ GitOps)

## Ansible

- インベントリ: `ansible/inventories/kkg`
- 変数: `group_vars/{all,internal,external,lb}.yaml`, `host_vars/*.yaml`
- Playbook 命名規則:
  - `site-*.yaml`: 主要コンポーネント群 (site-all, site-lb, site-k8s, site-monitoring, site-misskey)
  - `install-*.yaml`: 単体コンポーネント (install-frp, install-frr)
  - `upgrade-*.yaml`: アップグレード用
- ロール: `ansible/roles/` 配下。タスクは `tasks/main.yaml`、テンプレートは `templates/`
- Docker 系ロール (frr, frp) は Docker Compose テンプレート + `community.docker.docker_compose_v2` で管理
- シークレットは `community.general.onepassword` lookup で 1Password から取得

### ホストグループ

| グループ | 用途 |
|---------|------|
| internal | kkg クラスタ内部ノード (lb + k8s) |
| external | 外部サーバー (meruto-01, natsume-01) |
| lb | HAProxy + Keepalived ロードバランサー |
| k8s / k8s-cp | Kubernetes コントロールプレーン |
| frp | frps リバースプロキシ (meruto-01) |
| misskey | Misskey サーバー (natsume-01) |

## ArgoCD アプリケーション

- ディレクトリ: `argocd/<app-name>/`
- 構成: `application.yaml` + オプションで `values.yaml` + `manifests/`
- root-app が `argocd/**/application.yaml` を自動検出 (App of Apps)
- Namespace は `syncOptions: [CreateNamespace=true]` で自動作成
- sync-wave で依存順序を制御 (0: CRD → 2: ネットワーク → 3: アプリ → 5: 監視)
- シークレットは `OnePasswordItem` CRD で 1Password Connect 経由
- Helm + plain manifests のマルチソース構成が標準パターン

## ネットワーク

- 外部トラフィック: Internet → meruto-01 (frps) ← frpc (k8s) → traefik-external → サービス
- 内部 LB: HAProxy + Keepalived (VIP: 192.168.20.10)
- Pod Network: 10.26.0.0/16 (Cilium)
- Service LB: 192.168.21.100-254 (Cilium BGP)
- BGP: iX2215 (ASN 65000) ↔ LB (ASN 65002) ↔ CP (ASN 65001)
- ドメイン: *.pstr.space, *.str08.net, *.soli0222.com
- 内部監視エンドポイント: mimir.str08.net, loki.str08.net
- 外部監視エンドポイント: mimir.pstr.space, loki.pstr.space (mTLS)

## コーディング規約

- Ansible: YAML で統一 (.yaml 拡張子)。FQCN (`ansible.builtin.*`) を使用
- ArgoCD: application.yaml は `finalizers: [resources-finalizer.argocd.argoproj.io]` を付与
- Git: コミットメッセージは Conventional Commits (`feat`, `fix`, `chore` 等)
- 言語: ドキュメント・コメントは日本語可
