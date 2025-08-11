# PKE Ansible Automation

このディレクトリには、高可用性Kubernetesクラスターのセットアップと管理を自動化するAnsibleプレイブックとロールが含まれています。

## 概要

PKE Ansibleは以下のコンポーネントを自動化します：

- **ロードバランサー**: HAProxy + Keepalived による高可用性LB
- **Kubernetesクラスター**: containerd ランタイムを使用したマルチマスター構成
- **ベースシステム**: セキュリティ設定、システム最適化、必要パッケージのインストール
- **監視**: Alloy エージェントの配布

## ディレクトリ構造

```
ansible/
├── ansible.cfg                 # Ansible設定ファイル
├── site.yaml                   # メインプレイブック
├── site-all.yaml              # 全VM基本設定
├── site-lb.yaml               # ロードバランサー設定
├── site-k8s.yaml              # Kubernetesクラスター設定
├── site-monitoring.yaml       # 監視エージェント設定
├── upgrade-k8s.yaml           # Kubernetesアップグレード
├── upgrade-containerd.yaml    # containerdアップグレード
│
├── inventories/
│   ├── kkg                     # メインインベントリファイル
│   ├── group_vars/
│   │   ├── all.yml            # 全ホスト共通設定
│   │   ├── lb.yml             # ロードバランサーグループ設定
│   │   └── monitoring.yml     # 監視グループ設定
│   └── host_vars/
│       ├── kkg-lb1.yml        # lb1固有設定
│       └── kkg-lb2.yml        # lb2固有設定
│
└── roles/
    ├── all-vm-config/          # 全VM共通設定
    ├── init-cp-kubernetes/     # Kubernetesクラスター初期化
    ├── install-alloy/          # Alloy監視エージェント
    ├── install-containerd/     # containerdコンテナランタイム
    ├── install-haproxy/        # HAProxyロードバランサー
    ├── install-keepalived/     # Keepalived高可用性
    ├── install-kubernetes/     # Kubernetesコンポーネント
    ├── join-cp-kubernetes/     # コントロールプレーン参加
    ├── join-wk-kubernetes/     # ワーカーノード参加
    └── upgrade-kubernetes/     # Kubernetesアップグレード
```

## プレイブック詳細

### メインプレイブック

- **`site.yaml`**: すべてのプレイブックを順次実行するマスタープレイブック
- **`site-all.yaml`**: 全VMの基本設定（システム設定、パッケージ、セキュリティ）
- **`site-lb.yaml`**: ロードバランサーの設定（HAProxy + Keepalived）
- **`site-k8s.yaml`**: Kubernetesクラスターの構築

### 専用プレイブック

- **`site-monitoring.yaml`**: 監視エージェント（Alloy）の配布
- **`upgrade-k8s.yaml`**: Kubernetesのアップグレード
- **`upgrade-containerd.yaml`**: containerd（必要に応じてrunc/CNI含む）のアップグレード

## ロール詳細

| ロール | 説明 | 対象ホスト |
|--------|------|------------|
| `all-vm-config` | カーネルモジュール、sysctl、パッケージ更新 | all |
| `install-containerd` | containerdコンテナランタイムのインストール | k8s |
| `install-kubernetes` | kubelet、kubeadm、kubectlのインストール | k8s |
| `init-cp-kubernetes` | Kubernetesクラスターの初期化 | k8s-cp-leader |
| `join-cp-kubernetes` | 追加コントロールプレーンノードの参加 | k8s-cp-follower |
| `join-wk-kubernetes` | ワーカーノードの参加 | k8s-wk |
| `install-haproxy` | HAProxyロードバランサーの設定 | lb |
| `install-keepalived` | Keepalived高可用性の設定 | lb |
| `install-alloy` | Alloy監視エージェントの配布 | all |
| `upgrade-kubernetes` | Kubernetesコンポーネントのアップグレード | k8s |

## インベントリ構造

### ホストグループ定義

```ini
[all]
kkg-lb1 ansible_host=192.168.20.11
kkg-lb2 ansible_host=192.168.20.12
kkg-cp1 ansible_host=192.168.20.13
kkg-cp2 ansible_host=192.168.20.14
kkg-cp3 ansible_host=192.168.20.15
kkg-wk1 ansible_host=192.168.20.16
kkg-wk2 ansible_host=192.168.20.17
kkg-wk3 ansible_host=192.168.20.18

[lb]              # ロードバランサーノード
[k8s]             # 全Kubernetesノード
[k8s-cp]          # コントロールプレーンノード
[k8s-cp-leader]   # コントロールプレーンリーダー
[k8s-cp-follower] # コントロールプレーンフォロワー
[k8s-wk]          # ワーカーノード
```

### 設計原則

#### 1. DRY (Don't Repeat Yourself)
- IPアドレスは各ホストで一度だけ定義
- HAProxyバックエンドは`k8s-cp`グループから動的生成
- Keepalivedピアは`lb`グループから自動検出

#### 2. 分離された設定
- **グローバル設定**: `group_vars/all.yml`
- **グループ固有設定**: `group_vars/<group>.yml`
- **ホスト固有設定**: `host_vars/<hostname>.yml`

#### 3. 動的変数生成
- HAProxyバックエンドサーバーリストを手動維持する必要なし
- ホストを追加/削除すると自動的にロードバランサー設定に反映

## 主要な設定変数

### グローバル変数 (group_vars/all.yml)

```yaml
# Global Ansible Configuration
ansible_port: 22
ansible_user: ubuntu
ansible_ssh_private_key_file: /Users/soli/.ssh/id_ed25519
username: ubuntu

# Software Versions
containerd_version: "2.1.4"
runc_version: "1.3.0"
cni_plugins_version: "1.7.1"
kubernetes_version: 1.33.3

# Network Configuration
pod_network_cidr: 10.244.0.0/16

# Infrastructure Configuration
cluster_name: kkg
base_network: 192.168.20

# Derived Variables
lb_virtual_ip: "192.168.20.10"
controlplane_endpoint: "192.168.20.10:6443"
```

### ロードバランサー変数 (group_vars/lb.yml)

```yaml
# Load Balancer Configuration
lb_interface: eth0
lb_virtual_router_id: 10
haproxy_backend_port: 6443
```

### ホスト固有変数 (host_vars/)

```yaml
# Keepalived Configuration
keepalived_priority: 100  # kkg-lb1=100, kkg-lb2=90
keepalived_state: MASTER  # kkg-lb1=MASTER, kkg-lb2=BACKUP
```

## ネットワークアドレス配置

```
192.168.20.10   - Load Balancer VIP
192.168.20.11   - kkg-lb1 (MASTER)
192.168.20.12   - kkg-lb2 (BACKUP)
192.168.20.13   - kkg-cp1 (Control Plane Leader)
192.168.20.14   - kkg-cp2 (Control Plane)
192.168.20.15   - kkg-cp3 (Control Plane)
192.168.20.16   - kkg-wk1 (Worker)
192.168.20.17   - kkg-wk2 (Worker)
192.168.20.18   - kkg-wk3 (Worker)
```

## 使用方法

### 前提条件

1. **Ansible環境**: Ansible 2.9以上
2. **SSH接続**: 全ノードへのSSH鍵認証設定
3. **権限**: sudo権限を持つユーザー

### 基本的な実行手順

#### 1. 完全セットアップ

```bash
# すべてのコンポーネントをセットアップ
ansible-playbook -i inventories/kkg site.yaml

# 特定のコンポーネントのみ
ansible-playbook -i inventories/kkg site-lb.yaml      # ロードバランサーのみ
ansible-playbook -i inventories/kkg site-k8s.yaml     # Kubernetesのみ
ansible-playbook -i inventories/kkg site-monitoring.yaml # 監視のみ
```

#### 2. ステップバイステップ実行

```bash
# 1. 基本設定
ansible-playbook -i inventories/kkg site-all.yaml

# 2. ロードバランサー設定
ansible-playbook -i inventories/kkg site-lb.yaml

# 3. Kubernetesクラスター構築
ansible-playbook -i inventories/kkg site-k8s.yaml

# 4. 監視エージェント配布
ansible-playbook -i inventories/kkg site-monitoring.yaml
```

#### 3. アップグレード

```bash
# Kubernetesのアップグレード
ansible-playbook -i inventories/kkg upgrade-k8s.yaml

# コンテナランタイム（containerd）のアップグレード
ansible-playbook -i inventories/kkg upgrade-containerd.yaml
```

#### 4. 特定ロールの実行

```bash
# 特定のロールのみ実行
ansible-playbook -i inventories/kkg site-k8s.yaml --tags "install-kubernetes"
ansible-playbook -i inventories/kkg site-lb.yaml --tags "keepalived"
```

### トラブルシューティング

#### 接続確認

```bash
# 全ホストへの接続確認
ansible -i inventories/kkg all -m ping

# 特定グループへの接続確認
ansible -i inventories/kkg k8s -m ping
ansible -i inventories/kkg lb -m ping
```

#### 設定確認

```bash
# インベントリ確認
ansible-inventory -i inventories/kkg --list

# 変数確認
ansible -i inventories/kkg all -m debug -a "var=hostvars[inventory_hostname]"
```

#### ログ確認

```bash
# 詳細ログ付きで実行
ansible-playbook -i inventories/kkg site.yaml -vvv

# 特定のタスクのみ実行
ansible-playbook -i inventories/kkg site.yaml --start-at-task="タスク名"
```

## カスタマイズガイド

### 新しい環境への適用

1. **インベントリファイルをコピー**: `inventories/kkg` → `inventories/new-env`
2. **IPアドレスを更新**: 新しい環境のIPアドレスに変更
3. **group_vars/all.ymlを調整**: ネットワーク設定とバージョンを更新
4. **SSH鍵パスを設定**: `ansible_ssh_private_key_file`を適切なパスに設定

### スケールアウト

#### ワーカーノード追加

1. インベントリに新しいホストを追加
2. `k8s`と`k8s-wk`グループに追加
3. プレイブック実行（新しいノードのみが処理される）

#### コントロールプレーン追加

1. インベントリに新しいホストを追加
2. `k8s`、`k8s-cp`、`k8s-cp-follower`グループに追加
3. プレイブック実行

### バージョン管理

`group_vars/all.yml`で以下のバージョンを管理：

```yaml
containerd_version: "2.1.4"      # containerdバージョン
runc_version: "1.3.0"            # runcバージョン
cni_plugins_version: "1.7.1"     # CNI Pluginsバージョン
kubernetes_version: 1.33.3        # Kubernetesバージョン
```

## セキュリティ考慮事項

1. **SSH鍵管理**: 適切なSSH鍵のローテーション
2. **sudo権限**: 最小権限の原則に従った設定
3. **ファイアウォール**: 必要なポートのみ開放
4. **定期更新**: セキュリティパッチの定期適用

## 利点

1. **保守性**: IPアドレス変更は1箇所のみ
2. **スケーラビリティ**: ホスト追加時に設定ファイル変更不要
3. **可読性**: 設定が論理的に分離されている
4. **再利用性**: 他の環境への適用が容易
5. **自動化**: 手動作業を最小限に抑制
6. **一貫性**: 環境間での設定の一貫性を保証
