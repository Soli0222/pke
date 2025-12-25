# KKG Cluster Terraform Configuration

ProxmoxサーバーでKubernetes（KKG）クラスター用のVMを管理するためのTerraform設定です。

## 概要

このTerraform設定では、以下のVMを自動作成・管理します：

- **Load Balancer**: 2台（kkg-lb1、kkg-lb2）
- **Control Plane**: 3台（kkg-cp1、kkg-cp2、kkg-cp3）
- **Worker Node**: 3台（kkg-wk1、kkg-wk2、kkg-wk3）

## ファイル構成

```
terraform/kkg/
├── main.tf                 # メインのTerraform設定
├── variables.tf            # 変数定義
├── cluster-config.yaml     # クラスター設定（VM仕様、ネットワーク設定等）
├── setup.sh               # 初回セットアップスクリプト
├── template.sh            # テンプレート作成スクリプト
└── README.md              # このファイル
```

## 前提条件

### 必要なツール

- Terraform (>= 1.3)
- ProxmoxサーバーへのAPIアクセス
- SSH公開鍵

### Proxmoxサーバー準備

1. VM テンプレートの作成:
   ```bash
   ./template.sh
   ```

2. API トークンの作成（Proxmox Web UI で実行）:
   - ユーザー: `root@pam` または適切なユーザー
   - 権限: `PVEVMAdmin`, `PVEDatastoreUser` など

## 設定

### 1. 環境変数の設定

```bash
export TF_VAR_proxmox_api_url="https://your-proxmox-server:8006/api2/json"
export TF_VAR_proxmox_api_id="your-token-id"
export TF_VAR_proxmox_api_secret="your-token-secret"
export TF_VAR_ssh_public_key="$(cat ~/.ssh/id_rsa.pub)"
```

### 2. cluster-config.yaml の編集

必要に応じてクラスター設定を変更します：

- **ネットワーク設定**: `cluster.network` セクション
- **VMスペック**: `vm_types` セクション
- **個別VM設定**: `vms` セクション

## 使用方法

### 初回セットアップ

```bash
# 初期化とセットアップ
./setup.sh

# Terraformプランの確認
terraform plan

# リソースの作成
terraform apply
```

### VM管理

```bash
# 現在の状態確認
terraform show

# 設定変更の適用
terraform apply

# 特定のVMのみ作成/更新
terraform apply -target="proxmox_vm_qemu.vms[\"kkg-cp1\"]"

# リソースの削除
terraform destroy
```

## VM仕様

### Load Balancer
- **メモリ**: 2GB
- **CPU**: 8コア
- **ディスク**: 20GB
- **用途**: HAProxy + Keepalived

### Control Plane
- **メモリ**: 6GB (cp1は32GB, cp2/cp3は16GB)
- **CPU**: 8コア
- **ディスク**: 100GB (cp1/cp2/cp3ともに)
- **用途**: Kubernetes API Server, etcd, Controller Manager, Scheduler

### Worker Node
- **メモリ**: 11GB
- **CPU**: 8コア
- **ディスク**: 50GB
- **用途**: アプリケーション実行

## ネットワーク構成

- **ネットワーク**: 192.168.20.0/23
- **ゲートウェイ**: 192.168.20.1
- **DNS**: 192.168.20.1

### IPアドレス割り当て

| VM名 | IPアドレス | 用途 |
|------|------------|------|
| kkg-lb1 | 192.168.20.11 | Load Balancer |
| kkg-lb2 | 192.168.20.12 | Load Balancer |
| kkg-cp1 | 192.168.20.13 | Control Plane |
| kkg-cp2 | 192.168.20.14 | Control Plane |
| kkg-cp3 | 192.168.20.15 | Control Plane |
| kkg-wk1 | 192.168.20.16 | Worker |
| kkg-wk2 | 192.168.20.17 | Worker |
| kkg-wk3 | 192.168.20.18 | Worker Large |

## バックエンド設定

Terraform状態ファイルはCloudflare R2に保存されます：

- **バケット**: `tfstate`
- **キー**: `pke/kkg/terraform.tfstate`
- **エンドポイント**: Cloudflare R2

## トラブルシューティング

### よくある問題

1. **VM作成失敗**:
   - Proxmox API認証情報の確認
   - テンプレートの存在確認
   - ストレージ容量の確認

2. **ネットワーク接続問題**:
   - IPアドレス競合の確認
   - ブリッジ設定の確認
   - ファイアウォール設定の確認

3. **SSH接続できない**:
   - SSH鍵の設定確認
   - cloud-init の動作確認

### ログ確認

```bash
# Terraformデバッグログ
export TF_LOG=DEBUG
terraform apply

# Proxmox ログ
tail -f /var/log/pve/tasks/index
```

## 関連ドキュメント

- [../ansible/](../ansible/) - VM設定後のKubernetesクラスター構築
- [../helmfile/](../helmfile/) - Kubernetesアプリケーションデプロイ
- [Proxmox Provider Documentation](https://registry.terraform.io/providers/Telmate/proxmox/latest/docs)

## 注意事項

- `lifecycle.ignore_changes` でMACアドレスやディスクなどの変更を無視しています
- VM削除時はProxmox上でも確実に削除されることを確認してください
- バックアップは別途Proxmoxの機能を使用してください