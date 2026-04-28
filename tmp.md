# natsume multi-node / Longhorn migration plan

## 前提

- 既存クラスタ `natsume` は `natsume-02` の single node。
- 新規に `natsume-03`, `natsume-04`, `natsume-05` を追加し、最終的に `natsume-02` は退役する。
- 新規 node はすべて global/private の dual-homed 構成にする。
- public/global 側で受ける inbound は既存 node と同じく `80/tcp`, `443/tcp` のみ。
- k3s / etcd / Cilium / Longhorn / kubelet / apiserver などのクラスタ内部通信はすべて private 側で完結させる。
- Ansible の k3s 設定は private netplan address を `node-ip` / `advertise-address` に使い、global netplan address は `node-external-ip` にだけ使う。
- Traefik は public ingress として全 node の `node-external-ip` で受ける。
- private-only node は作らない。

## 目標構成

```text
natsume-03  global/private  etcd + k3s_server + Longhorn storage node
natsume-04  global/private  etcd + k3s_server + Longhorn storage node
natsume-05  global/private  etcd + k3s_server + Longhorn storage node

natsume-02  existing node -> cordon/drain -> etcd/k3s から除去 -> 退役
```

`k3s_agent` は今回の最終構成では使わず、3台とも `k3s_server` にする。control-plane と external etcd の failure domain を3つにする。

## Network / Firewall policy

- global interface:
  - allow inbound: `80/tcp`, `443/tcp`
  - deny other inbound
- private interface:
  - allow inbound cluster traffic
  - etcd peer/client, k3s apiserver, kubelet, Cilium, Longhorn replication は private address で通信させる
- DNS / external-dns:
  - Traefik Service の `EXTERNAL-IP` は各 node の global IP が並ぶ想定
  - public DNS に private IP を載せない
- K3s:
  - `node-ip`: private IPv4/IPv6
  - `advertise-address`: private IPv4
  - `node-external-ip`: global IPv4/IPv6
- etcd:
  - `listen-peer-urls`, `initial-advertise-peer-urls`, `listen-client-urls`, `advertise-client-urls` は private IPv4 を使う
- Longhorn:
  - storage network / data path は private 側で完結させる

## Storage policy

- `local-path` は残すが default から外す。
- Longhorn を default StorageClass にする。
- Longhorn は V1 data engine 前提。
- 初期 replica count は `2`。
- 重要 volume だけ実測後に replica `3` を検討する。
- 各新規 node は OS 領域を 100GB 程度にし、残りを Longhorn 用 LV/PV として mount する。

```text
root / OS LV:       100GB
Longhorn LV/PV:     remaining NVMe
mount point:        /var/lib/longhorn or /mnt/longhorn
filesystem:         ext4 or xfs
```

## CNPG policy

- 基本は `instances: 1`。
- Misskey だけ最終的に `instances: 2`。
- `instances: 3` は 8GB x3 では避ける。
- Longhorn replica と CNPG instances は別物として扱う。
- backup は object storage に継続する。

移行時は、必要に応じて一時的に `instances: 2` にして Longhorn PVC 側の standby を作り、catch-up 後に switchover する。

## Data migration policy

消失してよいもの:

- Mimir local PVC
- Loki local PVC
- Valkey PVC

移行したいもの:

- Navidrome
- Uptime Kuma
- Grafana
- CNPG managed PostgreSQL data

SQLite 系の Grafana / Uptime Kuma は停止コピーを必須にする。Navidrome も停止してコピーする。

## Phase 0: Preflight

1. 現状確認。

```text
kubectl get nodes -o wide
kubectl get sc
kubectl get pv,pvc -A
kubectl get pods -A -o wide
```

2. etcd snapshot を取得。

```text
etcdctl snapshot save ...
etcdctl endpoint status --cluster -w table
etcdctl member list -w table
```

3. local-path PVC の棚卸し。

```text
kubectl get pvc -A -o wide
kubectl get pv -o yaml
```

4. Flux の reconcile が正常であることを確認。

```text
flux get kustomizations -A
flux get helmreleases -A
```

## Phase 1: Add natsume-03/04/05 to Ansible inventory

1. `ansible/inventories/hosts.yaml` に `natsume-03/04/05` を追加する。
2. 各 host_vars に global/private netplan を定義する。
3. 最終的には `etcd` と `k3s_server` に `natsume-03/04/05` を入れる。
4. `natsume-02` は移行完了まで `etcd` / `k3s_server` に残す。

注意:

- `k3s_server_url` を agent 向けに使う予定はない。
- `k3s_datastore_endpoint` は `groups['etcd']` から生成されるため、移行中の endpoint list を意識する。

## Phase 2: Prepare new nodes

1. OS 初期設定、network、UFW を適用する。
2. global 側は 80/443 のみ inbound を許可する。
3. private interface で node 間疎通を確認する。

```text
ping <private-ip>
curl -k https://<private-ip>:2379/health
```

4. Longhorn 用 partition / LV / PV を作成し、永続 mount する。

OS installer で `/dev/vda3` までを root/OS 用 LVM PV (約 100GB) として切り、`/dev/vda` の残り領域は未割り当てのままにしておく。Longhorn 用 partition (`/dev/vda4`) と VG/LV/filesystem/mount は Ansible 側で作成する。

`longhorn_storage` group の共通設定は `ansible/inventories/group_vars/longhorn_storage.yaml` に置く。

```yaml
longhorn_storage_parent_disk: /dev/vda
longhorn_storage_partition_number: 4
longhorn_storage_devices:
  - /dev/vda4
```

実行前に各 node の `lsblk` で、`/dev/vda4` が存在しないか、もしくは存在する場合は未使用の Longhorn 用領域であることを確認する。partition 4 が既に存在する場合、role は data 保護のため再作成しない。

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/prepare-longhorn-storage.yaml
```

この playbook は `longhorn_storage` group の `natsume-03/04/05` のみを対象にする。role は次を順に行う。

- 必要 package (`lvm2`, `open-iscsi`, `nfs-common`, `cryptsetup`, `parted`, xfs 利用時は `xfsprogs`) の install
- `longhorn_storage_parent_disk` 上に `longhorn_storage_partition_number` の partition を、既存 partition の末尾以降から `100%` まで作成 (既存ならスキップ)
- `iscsid` の enable/start
- VG / LV / filesystem の作成
- `/var/lib/longhorn` への mount

## Phase 3: Expand external etcd cluster

ここは通常の宣言的な Ansible 適用だけで済ませない。既存 etcd cluster に対する member add/remove が必要。

推奨手順:

```text
02 only
-> add 03
-> add 04
-> add 05
-> remove 02
=> 03/04/05
```

一時的に 4 member になる期間を短く保つ。

新規 member は `etcdctl member add` で得た `ETCD_INITIAL_CLUSTER` を使い、`initial-cluster-state=existing` で起動する。

追加は1台ずつ実施する。

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/add-etcd-member.yaml -e etcd_member_host=natsume-03
ansible-playbook -i ansible/inventories/hosts.yaml ansible/add-etcd-member.yaml -e etcd_member_host=natsume-04
ansible-playbook -i ansible/inventories/hosts.yaml ansible/add-etcd-member.yaml -e etcd_member_host=natsume-05
```

`natsume-02` を抜くときは、生き残る member を leader endpoint として指定して実行する。

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/remove-etcd-member.yaml -e etcd_member_host=natsume-02 -e etcd_member_leader_host=natsume-03
```

注意:

- 現在の `setup-etcd` role は `groups['etcd']` から `initial-cluster` を作り、`etcd_cluster_state: new` を前提にしている。
- 既存 cluster 拡張用の task/role を別途用意するか、移行手順として明示的に `member add/remove` を実行する。
- `add-etcd-member.yaml` / `remove-etcd-member.yaml` を使い、`site-k3s.yaml` の `setup-etcd` 一括適用で既存クラスタ拡張を代替しない。
- etcd peer/client URL は private IP のみを使う。

確認:

```text
etcdctl endpoint status --cluster -w table
etcdctl endpoint health --cluster -w table
etcdctl member list -w table
```

## Phase 4: Join natsume-03/04/05 as k3s servers

1. 新規 node を `k3s_server` として構成する。
2. `datastore-endpoint` は etcd private IP の endpoint list を使う。
3. `node-ip` / `advertise-address` が private 側になっていることを確認する。
4. `node-external-ip` が global 側になっていることを確認する。

確認:

```text
kubectl get nodes -o wide
kubectl describe node natsume-03
kubectl describe node natsume-04
kubectl describe node natsume-05
```

期待:

- InternalIP は private IP。
- ExternalIP は global IP。
- kubelet / apiserver / Cilium の node 間通信は private IP で行われる。

## Phase 5: Verify multi-node networking

1. Cilium が全 node で Ready になることを確認する。
2. Pod-to-Pod が node を跨いで private network 経由で通ることを確認する。
3. Traefik DaemonSet が各 node に配置されることを確認する。
4. Traefik Service の `EXTERNAL-IP` に各 node の global IP が並ぶことを確認する。
5. public 側では 80/443 以外が閉じていることを確認する。

```text
kubectl -n kube-system get pods -o wide
kubectl -n traefik get pods -o wide
kubectl -n traefik get svc traefik -o wide
```

## Phase 6: Install Longhorn

1. Flux に Longhorn app を追加する。
2. Longhorn default data path を dedicated mount に向ける。
3. StorageClass を作る。
4. Longhorn StorageClass を default にする。
5. `local-path` は残すが default から外す。

Longhorn の default disk は label 付き node のみに作る。`natsume-02` に誤って `/var/lib/longhorn` disk を作らせないため、03/04/05 の準備が完了してから label を付ける。

```text
kubectl label node natsume-03 node.longhorn.io/create-default-disk=true
kubectl label node natsume-04 node.longhorn.io/create-default-disk=true
kubectl label node natsume-05 node.longhorn.io/create-default-disk=true
```

初期設定:

```text
default replica count: 2
data engine: V1
over-provisioning: conservative
minimal available storage: 10% or stricter
backup target: object storage
```

確認:

```text
kubectl get sc
kubectl -n longhorn-system get pods -o wide
```

## Phase 7: Migrate disposable storage

消えてよい PVC は再作成する。

対象:

- Mimir local PVC
- Loki local PVC
- Valkey PVC

手順:

1. 対象 HelmRelease/Kustomization を suspend または scale down。
2. PVC を削除。
3. Longhorn default StorageClass で再作成されることを確認。
4. app を再開。

## Phase 8: Migrate persistent app PVCs

対象:

- Grafana
- Uptime Kuma
- Navidrome

手順:

1. 対象 app を停止する。
2. 新 Longhorn PVC を作る。
3. 旧 local-path PVC と新 Longhorn PVC を一時 Pod に mount する。
4. `rsync` または `tar` でコピーする。
5. HelmRelease の persistence を新 PVC に向ける。
6. app を起動して動作確認する。
7. 旧 PVC/PV はすぐ消さず、一定期間退避する。

注意:

- Grafana / Uptime Kuma は SQLite を含むため停止コピーを必須にする。
- Navidrome も metadata DB の整合性を優先し、停止コピーする。

## Phase 9: Migrate CNPG clusters

DB ごとに実施する。まず小さい DB で手順を検証する。

基本手順:

1. Longhorn を default StorageClass にする。
2. CNPG Cluster を一時的に `instances: 2` にする。
3. 新しく作られる standby PVC が Longhorn になることを確認する。
4. replication catch-up を待つ。
5. Longhorn 側 instance に switchover する。
6. 旧 local-path 側 instance/PVC を削除する。
7. 通常 DB は `instances: 1` に戻す。
8. Misskey は最終的に `instances: 2` にする。

確認:

```text
kubectl cnpg status <cluster> -n <namespace>
kubectl get pvc -n <namespace>
kubectl get pods -n <namespace> -o wide
```

注意:

- CNPG の scale down 対象を雑に任せない。
- 挙動が読みづらい DB は `backup -> restore to new cluster -> app切替` の方が安全。
- Longhorn replica と CNPG instances を重ねすぎない。

## Phase 10: Cordon and drain natsume-02

1. `natsume-02` を cordon する。

```text
kubectl cordon natsume-02
```

2. local-path PVC が残っていないことを確認する。
3. drain する。

```text
kubectl drain natsume-02 --ignore-daemonsets --delete-emptydir-data
```

4. workload が 03/04/05 で正常に動くことを確認する。

## Phase 11: Remove natsume-02 from etcd / k3s

1. `natsume-02` の k3s datastore endpoint を手で `natsume-03/04/05` の etcd に向け変える。

`natsume-02` を etcd member から remove する前に、`natsume-02` 上の k3s が `192.168.9.2:2379` に依存しない状態にする。

```text
sudo vi /etc/rancher/k3s/config.yaml
```

```yaml
datastore-endpoint: "https://192.168.9.3:2379,https://192.168.9.4:2379,https://192.168.9.5:2379"
```

```text
sudo systemctl restart k3s
kubectl get nodes
kubectl get --raw=/readyz
```

必要なら `natsume-02` の etcd を一時停止して、k3s API が 03/04/05 の etcd だけで動くことを確認する。

```text
sudo systemctl stop etcd
kubectl get nodes
sudo systemctl start etcd
```

注意:

- 02 の向け変え後、退役完了まで `install-k3s` role を 02 に再適用しない。
- 再適用すると、inventory の `groups['etcd']` 由来の endpoint に戻る可能性がある。

2. etcd member から `natsume-02` を remove する。

```text
etcdctl member list -w table
etcdctl member remove <natsume-02-member-id>
```

3. `k3s_datastore_endpoint` から 02 の private IP を外す。
4. k3s server config を 03/04/05 で再レンダーする。
5. 03/04/05 の k3s を restart する場合は1台ずつ実施する。
6. inventory から `natsume-02` を外す。

確認:

```text
etcdctl endpoint status --cluster -w table
kubectl get nodes -o wide
kubectl get pods -A -o wide
```

## Rollback notes

- Phase 3 前の etcd snapshot を必ず保持する。
- `natsume-02` は最後まで削除せず、cordon/drain 後もしばらく停止せず保持する。
- PVC 移行では旧 PV/PVC をすぐ削除しない。
- CNPG は各 DB ごとに進め、複数 DB を同時に移行しない。
- Mimir/Loki/Valkey は消失許容として扱うため、rollback 対象にしない。

## Repo work items

1. `feat(ansible): add natsume-03/04/05 inventory`
2. `feat(ansible): support existing etcd member add/remove`
3. `feat(flux): add longhorn`
4. `chore(flux): make longhorn default storageclass`
5. `chore(flux): pin storageClassName for stateful apps`
6. `chore(flux): migrate cnpg clusters to longhorn`
7. `chore(ansible): retire natsume-02`
