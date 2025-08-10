# KKG Kubernetes Cluster - Terraform (Multi-Stack)

この構成は、Proxmox のホスト単位で独立した state を持つ「マルチスタック方式」です。
トポロジは YAML で一元管理し、認証や SSH 公開鍵は環境変数から供給します。

## ディレクトリ
- `terraform/cluster_topology.yaml` クラスタ全体のトポロジ（秘匿情報なし）
- `terraform/modules/proxmox-host/` 単一 Proxmox ホスト上の VM 群を作る汎用モジュール
- `terraform/stacks/kkg-pve{1,2,3}/` 各ホスト用スタック（独立 state）

## 事前準備（環境変数）
各スタック実行時に、その Proxmox ホストの環境変数を設定してください。

```bash
# 認証（例: kkg-pve1 実行時）
export PM_API_URL="https://192.168.20.2:8006/api2/json"
export PM_API_TOKEN_ID="root@pam!terraform-token"
export PM_API_TOKEN_SECRET="<token>"
export PM_TLS_INSECURE=1

# SSH 公開鍵
export TF_VAR_ssh_public_key="ssh-ed25519 AAAA... user@example.com"
```

## 実行（ホスト単位）
```bash
cd terraform/stacks/kkg-pve1
terraform init
terraform plan
terraform apply
```

同様に `kkg-pve2`, `kkg-pve3` でも実行できます。

## トポロジ（YAML）
- VM 定義（name, host, vmid, ip, cpu, memory_mb, disk_gb, role）を `cluster_topology.yaml` に記述
- 各スタックは自分の `host` に一致する VM のみを作成
- cloud image は Terraform 外で各 Proxmox ノードに配置し、`defaults.disk_image_path` で参照

## 旧構成からの変更点
- ルート直下のプロバイダ/変数/モジュール呼び出しは廃止
- `modules/kkg` は不要となり、`modules/proxmox-host` に置き換え
- tfvars は不要（SSH 鍵も環境変数で注入）

## 注意
- 既存 VM を取り込む場合は `terraform import` をご利用ください（for_each のキーは VM 名）。
- `lifecycle.ignore_changes` は最小限に設定しています。必要に応じて見直してください。
