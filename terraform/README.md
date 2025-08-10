# KKG Kubernetes Cluster - Terraform Configuration

このTerraform構成は、3つのProxmoxホストに分散したKubernetesクラスターを作成します。

## 🚀 **新機能: テンプレート不要のCloud Image直接使用**

この構成では、**Proxmoxテンプレートを作成する必要がありません**。Ubuntu cloud imageを直接使用してVMを作成します：

- ✅ テンプレート管理不要
- ✅ Cloud imageを直接ダウンロード・使用
- ✅ 完全なTerraform管理下
- ✅ 差分追跡が容易
- ✅ 冪等性を保証

## 構成概要

### kkg-pve1 - Proxmox Host 1
- **kkg-cp1**: Kubernetes Control Plane 1 (VM: 4vCPU, 4GB RAM, 50GB) - 192.168.20.13
- **kkg-wk1**: Kubernetes Worker Node 1 (VM: 4vCPU, 8GB RAM, 50GB) - 192.168.20.16
- **kkg-lb1**: Load Balancer 1 (VM: 4vCPU, 2GB RAM, 20GB) - 192.168.20.11

### kkg-pve2 - Proxmox Host 2  
- **kkg-cp2**: Kubernetes Control Plane 2 (VM: 4vCPU, 4GB RAM, 50GB) - 192.168.20.14
- **kkg-wk2**: Kubernetes Worker Node 2 (VM: 4vCPU, 8GB RAM, 50GB) - 192.168.20.17
- **kkg-lb2**: Load Balancer 2 (VM: 4vCPU, 2GB RAM, 20GB) - 192.168.20.12

### kkg-pve3 - Proxmox Host 3
- **kkg-cp3**: Kubernetes Control Plane 3 (VM: 8vCPU, 4GB RAM, 50GB) - 192.168.20.15
- **kkg-wk3**: Kubernetes Worker Node 3 (VM: 8vCPU, 24GB RAM, 100GB) - 192.168.20.18

## 前提条件

1. **Proxmoxプロバイダー**: `telmate/proxmox` ~> 2.9
2. **SSH アクセス**: 各ProxmoxノードへのSSHアクセス（cloud image自動ダウンロード用）
3. **ネットワーク**: `vmbr0` ブリッジが各ノードで利用可能であること
4. **ストレージ**: `local-lvm` ストレージが各ノードで利用可能であること
5. **Ubuntu 24.04 Cloud Image**: 自動ダウンロードされます（2025年7月27日版）

### ⚡ **自動Cloud Image管理**

- Ubuntu 24.04 minimal cloud imageを自動ダウンロード
- 各Proxmoxノードの `/var/lib/vz/template/iso/` に配置
- 既に存在する場合はスキップ（冪等性）
- Terraformが完全に管理

## セットアップ手順

### 1. SSH鍵の設定

```bash
# 各Proxmoxノードに公開鍵を配置（cloud imageダウンロード用）
ssh-copy-id root@192.168.20.2  # kkg-pve1
ssh-copy-id root@192.168.20.3  # kkg-pve2
ssh-copy-id root@192.168.20.4  # kkg-pve3
```

### 2. 設定ファイルの準備

```bash
# terraform.tfvars ファイルを作成
cp terraform.tfvars.example terraform.tfvars

# 実際の値に編集
vim terraform.tfvars
```

### 3. 必要な値の設定

`terraform.tfvars` で以下を設定：

- **SSH公開鍵**: `ssh_public_key`
- **Proxmox認証情報**: 各ホストの `password`
- **APIエンドポイント**: 各ホストの `api_url`
- **ノード名**: 各ホストの `node_name`
- **ストレージ**: 各ホストの `storage`
- **Cloud Image URL**: `cloud_image_url`（オプション、デフォルト値あり）

### 4. 環境変数での認証（推奨）

セキュリティのため、パスワードは環境変数で設定することを推奨：

```bash
export TF_VAR_proxmox_hosts='{
  "kkg-pve1": {"password": "your_kkg_pve1_password"},
  "kkg-pve2": {"password": "your_kkg_pve2_password"},
  "kkg-pve3": {"password": "your_kkg_pve3_password"}
}'
```

### 5. Terraformの実行

```bash
# 初期化
terraform init

# プランの確認
terraform plan

# 適用（Cloud imageダウンロード → VM作成の順で実行）
terraform apply
```

### 🚀 **実行フロー**

1. **Cloud Image準備**: 各Proxmoxノードでcloud imageを自動ダウンロード
2. **VM作成**: Cloud imageを直接使用してKubernetesクラスター構築
3. **Cloud-Init実行**: 自動的にネットワーク設定・SSH鍵設定

## 🎯 **新アプローチの利点**

- **✅ テンプレート管理不要**: 複雑なテンプレート作成プロセスを排除
- **✅ 完全なTerraform管理**: すべてがTerraformコードで管理
- **✅ 差分追跡**: `terraform plan` で完全な差分確認可能
- **✅ 冪等性**: 何度実行しても安全
- **✅ シンプル**: 設定がより直接的で理解しやすい
- **✅ 最新**: 常に最新のcloud imageを使用可能

## 出力

Terraform適用後、以下の情報が出力されます：

1. **kkg_cluster_info**: 作成されたすべてのVMの詳細情報（IPアドレス、VMID、ホスト情報など）

## 技術仕様

### Cloud Image処理
- **ダウンロード**: 各Proxmoxノードで自動実行
- **配置場所**: `/var/lib/vz/template/iso/ubuntu-24.04-minimal-cloudimg-amd64.img`
- **冪等性**: 既存ファイルがある場合はスキップ
- **フォーマット**: qcow2形式で直接使用
- **バージョン**: Ubuntu 24.04 Minimal（2025年7月27日リリース版）

### VM作成方式
- **テンプレート**: 使用しない（直接cloud image使用）
- **ディスク**: qcow2形式でcloud imageを直接マウント
- **Cloud-Init**: 自動的にネットワーク・SSH設定を実行
- **起動順序**: SCSI0ディスクから直接ブート

## ネットワーク構成

- **ネットワーク範囲**: 192.168.20.0/24
- **ゲートウェイ**: 192.168.20.1
- **DNS**: 192.168.20.1
- **Control Plane Endpoint**: 192.168.20.10:6443 (VIP)

## VMID割り当て

- **kkg-cp1**: 2013 (kkg-pve1)
- **kkg-cp2**: 2014 (kkg-pve2)
- **kkg-cp3**: 2015 (kkg-pve3)
- **kkg-wk1**: 2016 (kkg-pve1)
- **kkg-wk2**: 2017 (kkg-pve2)
- **kkg-wk3**: 2018 (kkg-pve3)
- **kkg-lb1**: 2011 (kkg-pve1)
- **kkg-lb2**: 2012 (kkg-pve2)

## 注意事項

1. **Cloud Images**: Ubuntu 24.04 cloud imageを直接使用（テンプレート不要）
   - 各Proxmoxノードで自動ダウンロード・配置される
   - Cloud-Init設定が自動的に適用される
2. **リソース**: 各Proxmoxホストに十分なリソースがあることを確認
3. **ネットワーク**: 指定したIPアドレス範囲が利用可能であることを確認
4. **ストレージ**: 十分なストレージ容量があることを確認
5. **SSH鍵**: 有効なSSH公開鍵を設定すること

## トラブルシューティング

### よくある問題

1. **Terraform validate失敗**
   ```bash
   terraform validate
   ```
   - プロバイダー設定の確認
   - 変数定義の重複チェック

2. **cloud imageダウンロード失敗**
   ```bash
   # Proxmoxノードでの手動確認
   ssh root@192.168.20.2 "ls -la /var/lib/vz/template/iso/"  # kkg-pve1
   ssh root@192.168.20.3 "ls -la /var/lib/vz/template/iso/"  # kkg-pve2  
   ssh root@192.168.20.4 "ls -la /var/lib/vz/template/iso/"  # kkg-pve3
   ```

3. **VMが起動しない**
   ```bash
   # Proxmox WebUIでVMログを確認
   # または直接ログ確認
   ssh root@192.168.20.2 "journalctl -u pve-manager"  # kkg-pve1
   ssh root@192.168.20.3 "journalctl -u pve-manager"  # kkg-pve2
   ssh root@192.168.20.4 "journalctl -u pve-manager"  # kkg-pve3
   ```

4. **ネットワーク設定が反映されない**
   - Cloud-Initの設定を確認
   - DNSとゲートウェイの設定をチェック

### 設定検証

```bash
# Terraform設定の検証
terraform fmt
terraform validate

# リソース作成のドライラン
terraform plan

# 実際のリソース作成
terraform apply
```

### ネットワーク設定の確認
```bash
# ブリッジの確認
pvesh get /nodes/{node}/network
```

### ストレージの確認
```bash
# ストレージの確認
pvesh get /nodes/{node}/storage
```
