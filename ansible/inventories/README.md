# Ansible Inventory Structure

## ディレクトリ構造

```
inventories/
├── kkg                    # メインインベントリファイル（ホスト定義のみ）
├── group_vars/
│   ├── all.yml           # 全ホスト共通設定
│   └── lb.yml            # ロードバランサーグループ設定
└── host_vars/
    ├── kkg-lb1.yml       # lb1固有設定
    └── kkg-lb2.yml       # lb2固有設定
```

## 設計原則

### 1. DRY (Don't Repeat Yourself)
- IPアドレスは各ホストで一度だけ定義
- HAProxyバックエンドは`k8s-cp`グループから動的生成
- Keepalivedピアは`lb`グループから自動検出

### 2. 分離された設定
- **グローバル設定**: `group_vars/all.yml`
- **グループ固有設定**: `group_vars/<group>.yml`
- **ホスト固有設定**: `host_vars/<hostname>.yml`

### 3. 動的変数生成
- HAProxyバックエンドサーバーリストを手動維持する必要なし
- ホストを追加/削除すると自動的にロードバランサー設定に反映

## 主要な変数

### グローバル変数 (group_vars/all.yml)
- `base_network`: ネットワークベース (192.168.20)
- `lb_virtual_ip`: ロードバランサーVIP
- `controlplane_endpoint`: Kubernetesコントロールプレーンエンドポイント

### ロードバランサー変数 (group_vars/lb.yml)
- `lb_interface`: ネットワークインターフェース
- `lb_virtual_router_id`: VRRP ID
- `haproxy_backend_port`: バックエンドポート

### ホスト変数 (host_vars/)
- `keepalived_priority`: Keepalived優先度
- `keepalived_state`: Keepalived状態 (MASTER/BACKUP)

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

## 利点

1. **保守性**: IPアドレス変更は1箇所のみ
2. **スケーラビリティ**: ホスト追加時に設定ファイル変更不要
3. **可読性**: 設定が論理的に分離されている
4. **再利用性**: 他の環境への適用が容易
