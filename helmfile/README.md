# PKE Helmfile Configuration

このディレクトリは、高可用性Kubernetesクラスターの **ブートストラップ（初期構築）** を行うためのHelmfile設定を含んでいます。

## 概要

以前はすべてのアプリケーションを Helmfile で管理していましたが、現在は **GitOps (ArgoCD)** への移行に伴い、Helmfile の役割は以下の「ブートストラップコンポーネント」のデプロイに限定されています。

アプリケーションやその他のプラットフォームコンポーネント（Traefik, Cert Manager, 監視スタック等）は、ArgoCD によって `argocd/` ディレクトリの設定に基づき自動管理されます。

### 管理対象コンポーネント（Helmfile）

以下のコンポーネントのみ、Helmfile で直接デプロイ・管理します配置順序もこの通りです：

1. **Cilium** (v1.18.5)
   - CNI (Container Network Interface)
   - NetworkPolicy, LoadBalancer (L2 Announcement)

2. **1Password Connect** (v2.1.1)
   - シークレット管理基盤
   - ArgoCD やその他のアプリがシークレットを取得するために必須

3. **ArgoCD**
   - GitOps CD エンジン
   - 以降の全アプリケーションデプロイを担当

## ディレクトリ構造

```
helmfile/
├── helmfile.yaml                    # メインHelmfile設定
├── 1password-credentials.json       # 1Password Connect認証情報（秘匿情報、要手動配置）
├── README.md                       # 本ドキュメント
│
├── values/                         # Helm Values設定（Go Template）
│   ├── 1password-connect.gotmpl    # 1Password Connect設定
│   ├── cilium.gotmpl              # Cilium CNI設定
│   └── argocd.gotmpl              # ArgoCD設定
│
└── manifests/                      # 追加Kubernetesマニフェスト
    ├── argocd/
    │   └── root-app.yaml           # ArgoCD App of Apps (ブートストラップ用)
    └── cilium/
        └── default-pool.yaml       # Cilium LoadBalancer IPPool
```

## 前提条件

- **Kubernetes Cluster**: Ansible で構築済みであること
- **Helmfile** (v0.165.0+)
- **Helm** (v3.10+)
- **kubectl**: クラスターへの接続設定済み
- **1Password CLI**: `op` コマンドが利用可能であること

## デプロイメント手順

### 1. 1Password Connect 準備

1Password Connect サーバーの認証情報ファイル `1password-credentials.json` をルートに配置します。
（作成方法は `README.md` または 1Password ドキュメントを参照）

### 2. 環境変数設定

```bash
# 1Password Connect トークン（初期デプロイ時のみ必要）
export ONEPASSWORD_TOKEN="<your-token>"
```

### 3. デプロイ実行

依存関係順に適用します。

```bash
# 1. CNI (Cilium) のデプロイ - 最優先
helmfile -l name=cilium apply

# 2. シークレット基盤 (1Password Connect)
helmfile -l name=connect apply

# 3. GitOps基盤 (ArgoCD)
helmfile -l name=argocd apply
```

または一括実行（依存関係は `needs` で定義済み）：

```bash
helmfile apply
```

## ArgoCD への移行について

ArgoCD デプロイ後、`manifests/argocd/root-app.yaml` が適用され、GitOps ループが開始します。
これにより、リポジトリの `argocd/` ディレクトリ以下の設定が自動的にクラスターに同期されます。

以下のコンポーネントは **ArgoCD 管理下** に移行しました（Helmfile では管理しません）：

- cert-manager
- external-dns
- Traefik / Traefik External
- MinIO (Operator/Tenant)
- Mimir / Loki / Grafana / Alloy
- Uptime Kuma
- 各種カスタムアプリケーション (Misskey Bot等)

## トラブルシューティング

### ArgoCD UI へのアクセス

```bash
# ポートフォワードでアクセス
kubectl port-forward svc/argocd-server -n argocd 8080:443
```
ブラウザで `https://localhost:8080` にアクセス。初期パスワードは Secret `argocd-initial-admin-secret` に格納されています。

### ブートストラップの再実行

Cilium や ArgoCD 自体のアップグレードが必要な場合は、再度 Helmfile を実行してください。

```bash
helmfile apply
```