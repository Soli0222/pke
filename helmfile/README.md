# PKE Helmfile Configuration

このディレクトリは、高可用性Kubernetesクラスター上にアプリケーションスタックをデプロイするためのHelmfile設定を含んでいます。

## 概要

PKE Helmfileは、Kubernetes上に完全なアプリケーションプラットフォームを構築するために、以下のコンポーネントを管理・デプロイします：

### ネットワーク・セキュリティレイヤー
- **Cilium** (v1.18.0): CNI（Container Network Interface）、NetworkPolicy、LoadBalancer
- **cert-manager** (v1.18.2): Let's Encrypt等による自動TLS証明書管理
- **external-dns** (v1.17.0): Cloudflare等のDNSプロバイダーとの自動レコード同期
- **Tailscale Operator** (v1.86.2): ゼロトラストVPNネットワーク管理

### シークレット・設定管理
- **1Password Connect** (v2.0.2): 中央集権的シークレット管理とKubernetes統合
- **Cloudflare Tunnel** (v0.3.2): セキュアな外部アクセストンネル

### Ingress・ロードバランシング
- **Traefik** (v37.0.0): パブリックHTTP/HTTPSロードバランサー・リバースプロキシ
- **Traefik Tailscale**: Tailscaleネットワーク専用のIngress Controller

### 監視・オブザーバビリティスタック
- **Victoria Metrics Cluster** (v0.27.1): 高性能メトリクス収集・保存・クエリエンジン
- **Grafana** (v9.3.1): 統合可視化ダッシュボード・アラート管理
- **Uptime Kuma** (v2.22.0): Webサービス・API監視・通知

### ストレージ・データ管理
- **NFS Subdir External Provisioner** (v4.0.18): NFS動的ボリュームプロビジョニング
- **MinIO Operator** (v7.1.1): S3互換オブジェクトストレージ基盤
- **MinIO Tenant** (v7.1.1): マルチテナント対応オブジェクトストレージインスタンス

## ディレクトリ構造

```
helmfile/
├── helmfile.yaml                    # メインHelmfile設定（リリース定義・依存関係）
├── 1password-credentials.json       # 1Password Connect認証情報（秘匿情報）
├── README.md                       # 本ドキュメント
│
├── values/                         # Helm Values設定（Go Template）
│   ├── 1password-connect.gotmpl    # 1Password Connect設定
│   ├── cilium.gotmpl              # Cilium CNI設定
│   ├── cloudflare-tunnel.gotmpl    # Cloudflare Tunnel設定
│   ├── external-dns.gotmpl         # External DNS設定
│   ├── grafana.gotmpl             # Grafana設定
│   ├── minio-tenant.gotmpl         # MinIO Tenant設定
│   ├── nfs-subdir-external-provisioner.gotmpl  # NFS Provisioner設定
│   ├── traefik.gotmpl             # Traefik（パブリック）設定
│   ├── traefik-tailscale.gotmpl    # Traefik（Tailscale）設定
│   ├── uptime-kuma.gotmpl          # Uptime Kuma設定
│   └── victoria-metrics-cluster.gotmpl  # Victoria Metrics設定
│
└── manifests/                      # 追加Kubernetesマニフェスト
    ├── cert-manager/
    │   └── clusterissuer.yaml      # Let's Encrypt ClusterIssuer
    ├── cilium/
    │   └── default-pool.yaml       # Cilium LoadBalancer Pool
    ├── cloudflare-tunnel/
    │   └── onepassworditem.yaml    # Cloudflare API Token Secret
    ├── external-dns/
    │   └── onepassworditem.yaml    # DNS Provider Secret
    ├── grafana/
    │   └── ingress-tailscale.yaml  # Tailscale専用Ingress
    ├── minio-tenant/
    │   └── onepassworditem.yaml    # MinIO認証情報
    ├── tailscale-operator/
    │   └── onepassworditem.yaml    # Tailscale認証情報
    ├── traefik/
    │   └── certificate.yaml        # パブリックドメイン証明書
    ├── traefik-tailscale/
    │   └── certificate.yaml        # Tailscaleドメイン証明書
    └── victoriametrics/
        └── ingress-tailscale.yaml  # VictoriaMetrics Tailscale Ingress
```

## アーキテクチャと依存関係

### デプロイメント順序とサービス依存

```mermaid
graph TD
    A[1Password Connect] --> B[cert-manager]
    A --> C[external-dns]
    A --> D[tailscale-operator]
    A --> E[cloudflare-tunnel]
    A --> F[minio-tenant]
    
    B --> G[traefik]
    C --> G
    B --> H[traefik-tailscale]
    C --> H
    
    G --> I[grafana]
    H --> I
    G --> J[vmcluster]
    H --> J
    
    K[cilium] --> L[nfs-subdir-external-provisioner]
    M[minio-operator] --> F
    
    style A fill:#e1f5fe
    style B fill:#f3e5f5
    style C fill:#f3e5f5
    style G fill:#fff3e0
    style H fill:#fff3e0
    style I fill:#e8f5e8
    style J fill:#e8f5e8
```

### 主要設定パラメータ

#### グローバル設定 (values/*.gotmpl)

**ネットワーク設定**:
```yaml
# 共通ドメイン設定
domain: "example.com"
tailscale_domain: "tail12345.ts.net"

# Load Balancer設定
loadbalancer_ip_range: "192.168.20.100-192.168.20.110"

# Certificate管理
cert_manager_email: "admin@example.com"
```

**セキュリティ設定**:
```yaml
# 1Password Connect
onepassword_vault: "kubernetes"
onepassword_server: "PKE-kkg"

# Tailscale設定
tailscale_authkey_secret: "tailscale-authkey"
```

## 前提条件

### 必要なツール・環境
- **Kubernetes Cluster**: 高可用性構成（Ansibleで構築）
- **Helmfile** (v0.165.0+): [インストールガイド](https://helmfile.readthedocs.io/en/latest/installation/)
- **Helm** (v3.10+): [インストールガイド](https://helm.sh/docs/intro/install/)
- **kubectl**: Kubernetesクラスターへの接続設定済み
- **1Password CLI**: [1Password CLI](https://developer.1password.com/docs/cli/)

### 前提インフラ要件
1. **ロードバランサー**: HAProxy + Keepalived（Ansibleで構築）
2. **DNS設定**: 外部DNSプロバイダー（Cloudflare推奨）アクセス
3. **TLS証明書**: Let's Encryptまたは自己証明書
4. **ストレージ**: NFSサーバーまたは他の永続ストレージ

## 環境設定・初期セットアップ

### 1. 1Password Connect サーバー設定

```bash
cd pke/helmfile

# 1Password Connectサーバーを作成（Kubernetes専用Vault付き）
op connect server create PKE-kkg --vaults kubernetes

# 認証トークンの生成と環境変数設定
export ONEPASSWORD_TOKEN=$(op connect token create kkg --server PKE-kkg --vault kubernetes)

# 認証情報ファイルの生成（必須）
op connect server get PKE-kkg --format json > 1password-credentials.json
```

### 2. Cloudflare 外部アクセス設定

Cloudflare Tunnelによる安全な外部アクセス設定：

```bash
# Cloudflare Dashboard操作
# 1. Cloudflare Zero Trust > Access > Tunnels
# 2. 新しいトンネル "pke-kkg" を作成
# 3. Connector Token を取得して1Password Vaultに保存

# 詳細設定: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/deployment-guides/kubernetes/
```

### 3. シークレット事前準備

1Password Vault "kubernetes" に以下のアイテムを作成：

| Item Name | Type | Fields | Usage |
|-----------|------|--------|-------|
| `cloudflare-tunnel` | API Credential | `token` | Cloudflare Tunnel認証 |
| `external-dns-cloudflare` | API Credential | `api-token` | DNS管理 |
| `tailscale-authkey` | API Credential | `authkey` | Tailscale VPN |
| `minio-root-credentials` | Login | `username`, `password` | MinIOルート認証 |

### 4. DNS・ネットワーク設定

**外部DNSレコード設定例**:
```bash
# Cloudflare等で以下を設定
*.kkg.example.com    A    <LoadBalancer_VIP>
kkg.example.com      A    <LoadBalancer_VIP>
```

**Tailscale MagicDNS設定**:
```bash
# Tailscale Admin Console
# DNS > MagicDNS > 有効化
# ドメイン例: tail12345.ts.net
```

## デプロイメント手順

### 基本フロー

#### 1. 設定検証・プリフライトチェック

```bash
cd pke/helmfile

# Kubernetesクラスター接続確認
kubectl cluster-info
kubectl get nodes

# Helmfile設定検証
helmfile -f helmfile.yaml list
helmfile -f helmfile.yaml template | head -50

# 1Password Connect接続テスト
curl -H "Authorization: Bearer $ONEPASSWORD_TOKEN" \
     https://connect-api.1password.com/v1/health
```

#### 2. 段階的デプロイメント

**Phase 1: 基盤コンポーネント**
```bash
# CNI・基盤ネットワーク
helmfile -l name=cilium apply

# シークレット管理基盤
helmfile -l name=connect apply

# 証明書・DNS管理
helmfile -l name=cert-manager apply
helmfile -l name=external-dns apply
```

**Phase 2: ネットワーク・Ingress**
```bash
# VPN・プライベートネットワーク
helmfile -l name=tailscale-operator apply

# ロードバランサー・Ingress
helmfile -l name=traefik apply
helmfile -l name=traefik-tailscale apply

# 外部アクセス
helmfile -l name=cloudflare-tunnel apply
```

**Phase 3: アプリケーション・サービス**
```bash
# ストレージ
helmfile -l name=nfs-subdir-external-provisioner apply
helmfile -l name=minio-operator apply
helmfile -l name=minio-tenant apply

# 監視・ダッシュボード
helmfile -l name=vmcluster apply
helmfile -l name=grafana apply
helmfile -l name=uptime-kuma apply
```

#### 3. 完全自動デプロイ

```bash
# 全コンポーネント一括デプロイ（依存関係順）
helmfile apply

# 並列実行（高速化）
helmfile apply --concurrency 5
```

### 操作コマンド詳細

#### 状態確認・管理

```bash
# 全リリース状態確認
helmfile status

# 特定リリースの詳細確認
helmfile -l name=grafana status
helm -n grafana status grafana

# 設定差分確認
helmfile diff
helmfile -l name=traefik diff

# リリース履歴
helm -n grafana history grafana
```

#### 更新・ロールバック

```bash
# 設定変更の適用
helmfile apply

# 特定コンポーネントの強制再デプロイ
helmfile -l name=grafana sync

# ロールバック
helm -n grafana rollback grafana 1  # リビジョン1にロールバック
```

#### クリーンアップ

```bash
# 全リリース削除
helmfile destroy

# 特定リリースのみ削除
helmfile -l name=uptime-kuma destroy

# ネームスペース確認・手動削除
kubectl get namespaces
kubectl delete namespace <namespace> --force --grace-period=0
```

## トラブルシューティング・運用ガイド

### 診断・ヘルスチェック

#### システム全体の健全性確認

```bash
# クラスター基本状態
kubectl cluster-info
kubectl get nodes -o wide
kubectl get pods --all-namespaces | grep -v Running

# 重要サービスの稼働確認
kubectl -n kube-system get pods -l k8s-app=cilium
kubectl -n cert-manager get pods
kubectl -n 1password get pods
kubectl -n traefik get pods
```

#### ネットワーク・接続性診断

```bash
# CNI（Cilium）状態確認
kubectl -n kube-system exec ds/cilium -- cilium status --brief

# LoadBalancer・Ingress確認
kubectl get svc -A --field-selector spec.type=LoadBalancer
kubectl get ingress -A

# DNS解決テスト
kubectl run -it --rm debug --image=busybox --restart=Never -- nslookup kubernetes.default
```

#### 証明書・TLS確認

```bash
# cert-manager証明書状態
kubectl get certificates -A
kubectl get certificaterequests -A
kubectl get clusterissuers

# 証明書詳細確認
kubectl describe certificate <cert-name> -n <namespace>
```

### よくある問題・解決方法

#### 1. 1Password Connect関連

**症状**: OnePasswordItem CRDでシークレット取得エラー
```bash
# Connect Server接続確認
kubectl -n 1password logs deployment/connect-api

# 認証トークン確認
kubectl -n 1password describe secret onepassword-token

# 手動テスト
export OP_CONNECT_HOST=https://connect-api.1password.svc.cluster.local:8080
curl -H "Authorization: Bearer $ONEPASSWORD_TOKEN" $OP_CONNECT_HOST/v1/vaults
```

**解決方法**:
- `ONEPASSWORD_TOKEN`環境変数の再設定
- `1password-credentials.json`の再生成
- Connectサーバーの再起動

#### 2. 証明書・DNS関連

**症状**: Let's Encrypt証明書発行失敗
```bash
# cert-manager詳細ログ
kubectl -n cert-manager logs deployment/cert-manager -f

# ACME Challenge確認
kubectl get challenges -A
kubectl describe challenge <challenge-name> -n <namespace>

# DNS確認
dig TXT _acme-challenge.example.com @8.8.8.8
```

**解決方法**:
- External-DNS認証情報確認
- Rate Limit確認（Let's Encrypt）

#### 3. Ingress・ロードバランサー

**症状**: TraefikでService到達不可
```bash
# Traefik設定確認
kubectl -n traefik logs deployment/traefik -f

# Service・Endpoint確認
kubectl get svc,ep -n <target-namespace>

# IngressRoute確認（Traefik CRD）
kubectl get ingressroute -A
kubectl describe ingressroute <name> -n <namespace>
```

**解決方法**:
- Service selector・ラベル確認
- NetworkPolicy確認
- Traefik middleware設定確認

#### 4. ストレージ関連

**症状**: PVC作成・マウントエラー
```bash
# StorageClass確認
kubectl get storageclass

# PV・PVC状態
kubectl get pv,pvc -A

# NFS Provisioner確認
kubectl -n kube-system logs deployment/nfs-subdir-external-provisioner
```

**解決方法**:
- NFSサーバー接続確認
- アクセス権限確認
- StorageClass annotation確認

### 監視・アラート設定

#### Victoria Metrics + Grafana

**重要メトリクス監視**:
```bash
# VictoriaMetrics接続確認
kubectl port-forward -n victoria-metrics svc/vmcluster-vmselect 8481:8481
curl http://localhost:8481/select/0/prometheus/api/v1/query?query=up

# Grafana初期設定
kubectl -n grafana get secret grafana -o jsonpath="{.data.admin-password}" | base64 -d
```

#### ログ集約（推奨）

```yaml
# 追加推奨: Loki Stack
# - Promtail (log collection)
# - Loki (log aggregation)  
# - Grafana (log visualization)
```

### 運用ベストプラクティス

#### 定期メンテナンス

**週次チェックリスト**:
```bash
# 1. システム全体ヘルスチェック
helmfile status

# 2. 証明書有効期限確認
kubectl get certificates -A -o custom-columns=NAME:.metadata.name,NAMESPACE:.metadata.namespace,READY:.status.conditions[0].status,EXPIRY:.status.notAfter

# 3. ディスク使用量確認
kubectl top nodes
kubectl top pods -A --sort-by=memory

# 4. バックアップ確認（推奨：etcd、persistent volume）
```

**月次アップデート**:
```bash
# Helm Chart更新確認
helmfile diff

# セキュリティアップデート
# - Kubernetes版数確認
# - コンテナイメージ脆弱性スキャン
```

#### セキュリティ考慮事項

1. **シークレット管理**:
   - 1Password Vault定期監査
   - 不要なシークレット削除
   - アクセス権限最小化

2. **ネットワークセキュリティ**:
   - NetworkPolicy適用
   - Tailscale ACL設定
   - 不要ポート閉鎖

3. **証明書管理**:
   - 証明書ローテーション確認
   - 期限切れアラート設定

### スケーリング・カスタマイズ

#### 新しい環境への適用

1. **values設定のカスタマイズ**:
```bash
# values/を新環境用にコピー
cp -r values/ values-production/

# 環境固有設定の更新
# - ドメイン名
# - IPアドレス範囲  
# - リソースサイズ
```

2. **環境別helmfile.yaml**:
```yaml
# helmfile-production.yaml
environments:
  production:
    values:
      - values-production/{{`{{ .Release.Name }}`}}.gotmpl
```

#### パフォーマンス調整

**高負荷環境向け設定例**:
```yaml
# values/victoria-metrics-cluster.gotmpl
vmcluster:
  vmselect:
    replicaCount: 3
    resources:
      requests: { cpu: 500m, memory: 1Gi }
  vmstorage:
    replicaCount: 3
    resources:
      requests: { cpu: 1, memory: 2Gi }
```

## 注意事項・制限事項

### セキュリティ要件
- **機密ファイル**: `1password-credentials.json`はリポジトリ除外（`.gitignore`設定済み）
- **環境変数**: `ONEPASSWORD_TOKEN`は永続化禁止
- **アクセス制御**: kubectl権限最小化、RBAC適用

### 運用制限
- **順次デプロイ**: 依存関係のため一部コンポーネントは順次実行必須
- **外部依存**: CloudflareDNS、1Password Connect Server等の外部サービス依存
- **リソース要件**: 最小構成でも8GB RAM、4 CPU推奨

### アップグレード注意点
- **CRD変更**: cert-manager、Cilium等のCRD互換性確認必須
- **データ移行**: Victoria Metrics、MinIO等のデータ移行計画
- **ダウンタイム**: 一部コンポーネントは短時間のダウンタイム発生可能性