# k3s + external etcd playbook 整備案

## 前提の修正

先に書いた「k3s embedded etcd 前提」は誤り。今回作りたい構成は、etcd を k3s とは別の systemd daemon として構築し、k3s server が external datastore として etcd を参照する構成にする。

ただし、現在の作業ツリーにある `ansible/roles/install-k3s/templates/config.yaml.j2` は external etcd を参照していない。現状は以下のように embedded etcd 用の設定になっている。

```yaml
cluster-init: {{ k3s_cluster_init | lower }}
etcd-s3: {{ k3s_etcd_s3 | lower }}
etcd-snapshot-schedule-cron: "{{ k3s_etcd_snapshot_schedule_cron }}"
```

external etcd にするには、k3s config へ `datastore-endpoint` と TLS credential を追加し、`cluster-init` / `etcd-s3` / `etcd-snapshot-*` は k3s 側から外す。

また、git 上では以下の etcd 系ロールが削除状態になっている。これらは external etcd 構成の土台として再利用または作り直す対象。

- `bootstrap-etcd-certs`
- `etcd-precheck`
- `etcd-maintenance`
- `upgrade-etcd`

## 目標構成

```text
etcd hosts
  └─ systemd etcd
       - client: https://<node-ip>:2379
       - peer:   https://<node-ip>:2380
       - certs:  /etc/etcd/pki

k3s server
  └─ k3s server
       - datastore-endpoint: https://etcd-1:2379,https://etcd-2:2379,...
       - datastore-cafile:   /etc/etcd/pki/ca.crt
       - datastore-certfile: /etc/etcd/pki/k3s-etcd-client.crt
       - datastore-keyfile:  /etc/etcd/pki/k3s-etcd-client.key
```

## 最終的なファイル構成

```text
ansible/
├── site-k3s.yaml
├── upgrade-etcd.yaml
├── upgrade-k3s.yaml
├── inventories/
│   └── kkg/
│       ├── hosts.yaml
│       ├── group_vars/
│       │   ├── all.yaml
│       │   ├── etcd.yaml
│       │   ├── k3s_cluster.yaml
│       │   └── k3s_server.yaml
│       └── host_vars/
│           └── natsume-02.yaml
└── roles/
    ├── all-vm-config/
    ├── configure-ufw/
    ├── setup-etcd/
    ├── etcd-precheck/
    ├── etcd-maintenance/
    ├── upgrade-etcd/
    ├── install-k3s/
    └── upgrade-k3s/
```

## 1. inventory 整理

`ansible/inventories/hosts` は廃止し、`ansible/inventories/hosts.yaml` に寄せる。etcd と k3s は同じ host に同居できるように group を分ける。

単一ノードから始めるなら以下。

```yaml
---
all:
  children:
    etcd:
      hosts:
        natsume-02:
    k3s_cluster:
      children:
        k3s_server:
          hosts:
            natsume-02:
        k3s_agent:
          hosts: {}
```

将来 3 台 etcd にする場合も、`etcd` group に host を足すだけにする。

```yaml
---
all:
  children:
    etcd:
      hosts:
        node-01:
        node-02:
        node-03:
    k3s_cluster:
      children:
        k3s_server:
          hosts:
            node-01:
        k3s_agent:
          hosts:
            node-02:
            node-03:
```

`group_vars/etcd.yaml`:

```yaml
---
etcd_version: "3.5.21"
etcd_data_dir: /var/lib/etcd
etcd_pki_dir: /etc/etcd/pki
etcd_client_port: 2379
etcd_peer_port: 2380
etcd_metrics_port: 2381
etcd_snapshot_dir: /var/lib/etcd/snapshots

etcd_client_url_scheme: https
etcd_peer_url_scheme: https
etcd_listen_client_urls:
  - "https://127.0.0.1:2379"
  - "https://{{ ansible_default_ipv4.address }}:2379"
etcd_listen_peer_urls:
  - "https://{{ ansible_default_ipv4.address }}:2380"
etcd_listen_metrics_urls:
  - "http://127.0.0.1:2381"
```

`group_vars/k3s_cluster.yaml`:

```yaml
---
k3s_version: "v1.35.3+k3s1"
k3s_bind_address: "{{ ansible_default_ipv4.address }}"
k3s_write_kubeconfig_mode: "0600"

k3s_cluster_cidr: "10.9.0.0/16,fd00:10:9::/56"
k3s_service_cidr: "10.7.0.0/16,fd00:10:7::/112"
k3s_cluster_dns: "10.7.0.10,fd00:10:7::a"

k3s_disable_components:
  - helm-controller
  - traefik
```

`group_vars/k3s_server.yaml`:

```yaml
---
k3s_node_role: server
k3s_datastore_cafile: /etc/etcd/pki/ca.crt
k3s_datastore_certfile: /etc/etcd/pki/k3s-etcd-client.crt
k3s_datastore_keyfile: /etc/etcd/pki/k3s-etcd-client.key
```

`k3s_datastore_endpoint` は inventory の `etcd` group から生成する。

```yaml
k3s_datastore_endpoint: >-
  {{
    groups['etcd']
    | map('extract', hostvars, ['ansible_default_ipv4', 'address'])
    | map('regex_replace', '^(.*)$', 'https://\\1:2379')
    | join(',')
  }}
```

`site-k3s.yaml` は etcd 構築から k3s インストールまで一括で通す入口にする。

```yaml
---
- name: Prepare etcd nodes
  hosts: etcd
  become: true
  roles:
    - all-vm-config
    - configure-ufw

- name: Install and configure external etcd
  hosts: etcd
  become: true
  roles:
    - setup-etcd
    - etcd-maintenance

- name: Validate external etcd
  hosts: etcd
  become: true
  roles:
    - etcd-precheck

- name: Prepare k3s nodes
  hosts: k3s_cluster
  become: true
  roles:
    - all-vm-config
    - configure-ufw

- name: Install k3s servers
  hosts: k3s_server
  become: true
  serial: 1
  roles:
    - install-k3s

- name: Install k3s agents
  hosts: k3s_agent
  become: true
  serial: 1
  roles:
    - install-k3s
```

初回構築はこれだけ叩く。

```bash
cd ansible
ansible-playbook -i inventories/hosts.yaml site-k3s.yaml
```

## 2. `setup-etcd` ロール

以前の `bootstrap-etcd-certs` と、存在していたはずの `install-etcd-systemd` 相当をまとめて `setup-etcd` として作る。

責務:

- etcd/etcdctl binary の配置。
- `/etc/etcd/pki` の CA、server、peer、healthcheck client、k3s client 証明書の作成と配布。
- `/etc/etcd/etcd.env` または systemd unit の生成。
- `/var/lib/etcd`、snapshot directory、download directory の作成。
- `etcd` systemd service の enable/start。
- 単一ノードと複数ノードの initial cluster 生成。

作るファイル:

```text
ansible/roles/setup-etcd/
├── defaults/main.yaml
├── handlers/main.yaml
├── tasks/main.yaml
├── templates/etcd.service.j2
├── templates/server-openssl.cnf.j2
├── templates/peer-openssl.cnf.j2
└── templates/k3s-client-openssl.cnf.j2
```

`defaults/main.yaml` の主要変数:

```yaml
---
etcd_version: "3.5.21"
etcd_download_url: "https://github.com/etcd-io/etcd/releases/download/v{{ etcd_version }}/etcd-v{{ etcd_version }}-linux-amd64.tar.gz"
etcd_data_dir: /var/lib/etcd
etcd_pki_dir: /etc/etcd/pki
etcd_snapshot_dir: /var/lib/etcd/snapshots
etcd_download_dir: /var/lib/etcd/downloads
etcd_cluster_state: new
etcd_initial_cluster_token: pke-etcd
```

systemd unit の `ExecStart` は以下の形。

```text
/usr/local/bin/etcd \
  --name={{ inventory_hostname }} \
  --data-dir={{ etcd_data_dir }} \
  --initial-advertise-peer-urls=https://{{ ansible_default_ipv4.address }}:2380 \
  --listen-peer-urls=https://{{ ansible_default_ipv4.address }}:2380 \
  --advertise-client-urls=https://{{ ansible_default_ipv4.address }}:2379 \
  --listen-client-urls=https://127.0.0.1:2379,https://{{ ansible_default_ipv4.address }}:2379 \
  --listen-metrics-urls=http://127.0.0.1:2381 \
  --initial-cluster={{ etcd_initial_cluster }} \
  --initial-cluster-state={{ etcd_cluster_state }} \
  --initial-cluster-token={{ etcd_initial_cluster_token }} \
  --client-cert-auth \
  --trusted-ca-file={{ etcd_pki_dir }}/ca.crt \
  --cert-file={{ etcd_pki_dir }}/server.crt \
  --key-file={{ etcd_pki_dir }}/server.key \
  --peer-client-cert-auth \
  --peer-trusted-ca-file={{ etcd_pki_dir }}/ca.crt \
  --peer-cert-file={{ etcd_pki_dir }}/peer.crt \
  --peer-key-file={{ etcd_pki_dir }}/peer.key
```

`etcd_initial_cluster` は `groups['etcd']` から生成する。

```yaml
etcd_initial_cluster: >-
  {{
    groups['etcd']
    | map('extract', hostvars)
    | map(attribute='inventory_hostname')
  }}
```

実装では上のような途中式ではなく、`set_fact` か Jinja template で `node-01=https://ip:2380,node-02=https://ip:2380` を生成する。

## 3. k3s ロールの修正

現在の `install-k3s` は embedded etcd 用なので、external etcd 用に変更する。

削除する設定:

- `cluster-init`
- `etcd-s3`
- `etcd-s3-*`
- `etcd-snapshot-*`

追加する設定:

```yaml
datastore-endpoint: "{{ k3s_datastore_endpoint }}"
datastore-cafile: "{{ k3s_datastore_cafile }}"
datastore-certfile: "{{ k3s_datastore_certfile }}"
datastore-keyfile: "{{ k3s_datastore_keyfile }}"
```

`templates/config.yaml.j2` の方向性:

```yaml
bind-address: "{{ k3s_bind_address }}"
write-kubeconfig-mode: "{{ k3s_write_kubeconfig_mode }}"

cluster-cidr: "{{ k3s_cluster_cidr }}"
service-cidr: "{{ k3s_service_cidr }}"
cluster-dns: "{{ k3s_cluster_dns }}"

disable:
{% for component in k3s_disable_components %}
  - {{ component }}
{% endfor %}

{% if k3s_node_role == "server" %}
datastore-endpoint: "{{ k3s_datastore_endpoint }}"
datastore-cafile: "{{ k3s_datastore_cafile }}"
datastore-certfile: "{{ k3s_datastore_certfile }}"
datastore-keyfile: "{{ k3s_datastore_keyfile }}"
{% endif %}

{% if k3s_node_role == "agent" %}
server: "{{ k3s_server_url }}"
token: "{{ k3s_token }}"
{% endif %}
```

`tasks/main.yaml` も整理する。

- S3 snapshot 用の 1Password lookup は削除。
- k3s server に k3s client certificate が存在することを `stat/assert` で確認。
- agent の場合は `k3s-agent` service を扱う。
- install script 実行時に `INSTALL_K3S_EXEC=server` または `agent` を明示する。

## 4. k3s 用 etcd client 証明書

k3s が external etcd に接続するため、etcd CA で署名した client cert を k3s server に配る。

配置:

```text
/etc/etcd/pki/ca.crt
/etc/etcd/pki/k3s-etcd-client.crt
/etc/etcd/pki/k3s-etcd-client.key
```

証明書 subject:

```text
CN=k3s-etcd-client
O=system:masters
```

配布方法:

- CA は etcd leader で生成し、etcd/k3s server 全台へ配布。
- `k3s-etcd-client.crt/key` も leader で作成し、k3s server 全台へ配布。
- 秘密鍵を Ansible controller に落とす場合は `no_log: true` を徹底する。

## 5. `etcd-precheck`

external etcd の健全性確認ロールとして残す。

確認項目:

- `/usr/local/bin/etcdctl` が存在する。
- `/etc/etcd/pki/ca.crt`、`healthcheck-client.crt/key` が存在する。
- `https://127.0.0.1:2379` の `endpoint health` が成功する。
- `member list` に inventory host が含まれる。
- 複数台の場合は全 member の peer URL が期待値と一致する。
- leader で snapshot を取得できる。

`etcdctl` の共通オプション:

```bash
ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/etcd/pki/ca.crt \
  --cert=/etc/etcd/pki/healthcheck-client.crt \
  --key=/etc/etcd/pki/healthcheck-client.key
```

## 6. `etcd-maintenance`

etcd の snapshot と compaction/defrag を systemd timer で実行する。

責務:

- `etcd-maintenance.sh` を配置。
- `etcd-maintenance.service` を配置。
- `etcd-maintenance.timer` を enable/start。
- snapshot retention を管理。

処理内容:

```bash
etcdctl snapshot save /var/lib/etcd/snapshots/snapshot-<timestamp>.db
etcdctl endpoint status --write-out=json
etcdctl compact <revision>
etcdctl defrag
find /var/lib/etcd/snapshots -name 'snapshot-*.db' -mtime +<retention_days> -delete
```

単一ノードならその host で実行。複数ノードなら leader だけで snapshot を取る方針にする。

## 7. `upgrade-etcd`

これは必要。k3s のアップグレードとは分ける。

playbook:

```yaml
---
- name: Precheck etcd before upgrade
  hosts: etcd
  become: true
  roles:
    - etcd-precheck

- name: Upgrade etcd
  hosts: etcd
  become: true
  serial: 1
  roles:
    - upgrade-etcd

- name: Validate etcd after upgrade
  hosts: etcd
  become: true
  roles:
    - etcd-precheck
```

ロールの流れ:

1. `etcd_version` が指定されていることを確認。
2. upgrade 前 snapshot を保存。
3. target etcd release を download/extract。
4. `/usr/local/bin/etcd` と `/usr/local/bin/etcdctl` を差し替え。
5. `systemctl restart etcd`。
6. `wait_for: 2379`。
7. `endpoint health`。
8. `member list`。

複数台の場合は `serial: 1` 必須。

## 8. `upgrade-k3s`

k3s binary の upgrade は etcd upgrade と分ける。

```yaml
---
- name: Upgrade k3s servers
  hosts: k3s_server
  become: true
  serial: 1
  roles:
    - upgrade-k3s

- name: Upgrade k3s agents
  hosts: k3s_agent
  become: true
  serial: 1
  roles:
    - upgrade-k3s
```

流れ:

- k3s version 確認。
- target version と一致すれば skip。
- etcd precheck を delegate または別 play で実行。
- install script を `INSTALL_K3S_VERSION={{ k3s_version }}` で実行。
- `k3s` / `k3s-agent` を restart。
- server では `kubectl get nodes` と `/readyz` を確認。

## 9. UFW

external etcd 前提で UFW を整理する。

許可ポート:

| 用途 | port/proto | 許可元 |
| --- | --- | --- |
| SSH | 22/tcp | 管理元 |
| etcd client | 2379/tcp | etcd nodes, k3s_server |
| etcd peer | 2380/tcp | etcd nodes |
| etcd metrics | 2381/tcp | localhost または監視 node |
| Kubernetes API | 6443/tcp | 管理元, k3s_agent |
| kubelet | 10250/tcp | k3s_cluster |
| flannel VXLAN | 8472/udp | k3s_cluster |
| NodePort | 30000:32767/tcp,udp | 必要な場合のみ |

`configure-ufw` は group によって開けるポートを変える。

- `etcd` host: 2379/2380/2381
- `k3s_server` host: 6443/10250/8472
- `k3s_agent` host: 10250/8472

Cilium を使うなら k3s の flannel を無効化し、UFW も Cilium 前提で再設計する。今の k3s config には flannel 無効化がないため、まずは default flannel 前提でよい。

## 10. 実装順

1. inventory を `inventories/hosts.yaml` と `group_vars`/`host_vars` に分割する。
2. `setup-etcd` を作る。既存の `bootstrap-etcd-certs` の考え方は再利用する。
3. `etcd-precheck`、`etcd-maintenance`、`upgrade-etcd` を external etcd 前提で復活させる。
4. `install-k3s` から embedded etcd/S3 snapshot 設定を削除し、`datastore-*` 設定へ変更する。
5. `configure-ufw` を作る。
6. `site-k3s.yaml` を etcd 構築込みの入口として実体化する。
7. `upgrade-etcd.yaml` と `upgrade-k3s.yaml` を分ける。
8. syntax check と check mode で確認する。

## 11. 確認コマンド

```bash
cd ansible
ansible-playbook -i inventories/hosts.yaml site-k3s.yaml --syntax-check
ansible-playbook -i inventories/hosts.yaml upgrade-etcd.yaml --syntax-check
ansible-playbook -i inventories/hosts.yaml upgrade-k3s.yaml --syntax-check
```

初回構築:

```bash
cd ansible
ansible-playbook -i inventories/hosts.yaml site-k3s.yaml
```

etcd upgrade:

```bash
cd ansible
ansible-playbook -i inventories/hosts.yaml upgrade-etcd.yaml -e etcd_version=3.5.21
```

k3s upgrade:

```bash
cd ansible
ansible-playbook -i inventories/hosts.yaml upgrade-k3s.yaml -e k3s_version=v1.32.4+k3s1
```

## 12. 後で決めること

- etcd と k3s server を同居させるか、etcd 専用 host を置くか。
- etcd を最初から 3 台にするか、単一ノードから始めるか。
- etcd CA/key を Ansible 管理にするか、1Password など外部 secret store に逃がすか。
- k3s agent token を 1Password 管理にするか、server から取得して配るか。
- CNI を default flannel にするか、Cilium にするか。
- NodePort を開ける必要があるか。
