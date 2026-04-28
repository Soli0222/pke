# natsume multi-node migration operations

## Scope

この手順は、既存の `natsume-02` single-node k3s cluster に `natsume-03`, `natsume-04`, `natsume-05` を追加し、Longhorn を直接適用してから、後続の storage migration に進むための運用手順。

この branch は作業完了後に PR merge する想定のため、Longhorn は Flux root 経由ではなく、作業端末から次で直接適用する。

```text
kubectl label node natsume-03 node.longhorn.io/create-default-disk=true --overwrite
kubectl label node natsume-04 node.longhorn.io/create-default-disk=true --overwrite
kubectl label node natsume-05 node.longhorn.io/create-default-disk=true --overwrite
kubectl apply -k flux/clusters/natsume/apps/longhorn
```

## Preconditions

- `natsume-03/04/05` は OS install 済み。
- 各 node は global/private の dual-homed。
- global 側 inbound は `80/tcp`, `443/tcp` のみ。
- k3s / etcd / Cilium / Longhorn などの cluster internal traffic は private 側で完結させる。
- `ansible/inventories/host_vars/natsume-03.yaml` から `natsume-05.yaml` の global `TBD` を実値に置き換える。
- 各 node の `/dev/vda` で、`/dev/vda3` までが OS installer により利用済みで、残り領域が未割り当てになっていることを確認する。`/dev/vda4` が既に存在する場合は、未使用の Longhorn 用領域であることを確認する (data がある partition は role が上書きしないが、人間側でも確認する)。
- etcd snapshot を取得済み。
- Ansible 実行前に repository root で venv を有効化する。

```text
source venv/bin/activate
ansible --version
```

`source venv/bin/activate` 済みなら、通常は `PYENV_VERSION=...` の指定は不要。もし Ansible が `~/.ansible/tmp` に書けず失敗する場合だけ、作業 shell で次を設定する。

```text
export ANSIBLE_LOCAL_TEMP=/tmp/ansible-local
```

## 0. Preflight

既存 cluster の状態を確認する。

```text
kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl get sc
kubectl get pv,pvc -A
```

etcd の状態を確認する。

```text
etcdctl endpoint status --cluster -w table
etcdctl endpoint health --cluster -w table
etcdctl member list -w table
```

etcd snapshot を取得する。

```text
sudo mkdir -p /var/lib/etcd/snapshots
sudo ETCDCTL_API=3 etcdctl snapshot save "/var/lib/etcd/snapshots/pre-multinode-$(date +%Y%m%d%H%M%S).db"
```

## 1. Complete inventory values

`natsume-03/04/05` の host_vars で、global 側の `TBD` を埋める。

確認する値:

- `network_netplan.global.ipv4`
- `network_netplan.global.ipv6`
- `network_netplan.global.default_route`
- `ansible_host` の名前解決または SSH 接続性

private 側は次の想定。

```text
natsume-03: 192.168.9.3/24, fd00:192:168:9::3/64
natsume-04: 192.168.9.4/24, fd00:192:168:9::4/64
natsume-05: 192.168.9.5/24, fd00:192:168:9::5/64
```

## 2. Prepare OS / network / firewall

`site-k3s.yaml` はここでは使わない。既存 etcd cluster に対して `setup-etcd` を一括適用しないため、prepare 専用 playbook を新規 node に限定して流す。

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/prepare-k3s-nodes.yaml \
  --limit natsume-03,natsume-04,natsume-05
```

確認:

```text
ping 192.168.9.3
ping 192.168.9.4
ping 192.168.9.5
```

global 側は `80/tcp`, `443/tcp` 以外を開けない。

## 3. Prepare Longhorn host storage

各 node の `/dev/vda` の状態を確認する。`/dev/vda4` が存在しない、もしくは既存でも未使用領域であることを確認する。

```text
lsblk
sudo parted /dev/vda print free
```

Longhorn 用 partition / VG / LV / filesystem / mount と必要 package を Ansible で準備する。`prepare-longhorn-storage.yaml` は次を順に行う。

- 必要 package (`lvm2`, `open-iscsi`, `nfs-common`, `cryptsetup`, `parted` 等) の install
- `longhorn_storage_parent_disk` 上で `longhorn_storage_partition_number` の partition を未割り当て領域から `100%` まで作成 (既存ならスキップ)
- `iscsid` の enable/start
- VG (`longhorn`) / LV (`data`, `100%FREE`) / ext4 filesystem の作成
- `/var/lib/longhorn` への mount

partition / device の値は `ansible/inventories/group_vars/longhorn_storage.yaml` で `/dev/vda` + partition `4` を default にしている。disk geometry が異なる node を追加する場合は host_vars 側で override する。

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/prepare-longhorn-storage.yaml \
  --limit natsume-03,natsume-04,natsume-05
```

確認:

```text
lsblk
findmnt /var/lib/longhorn
systemctl status iscsid
```

## 4. Add etcd members

`natsume-03/04/05` を既存 etcd cluster に1台ずつ追加する。

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/add-etcd-member.yaml \
  -e etcd_member_host=natsume-03
```

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/add-etcd-member.yaml \
  -e etcd_member_host=natsume-04
```

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/add-etcd-member.yaml \
  -e etcd_member_host=natsume-05
```

確認:

```text
etcdctl endpoint status --cluster -w table
etcdctl endpoint health --cluster -w table
etcdctl member list -w table
```

この時点では一時的に `natsume-02/03/04/05` の 4 member になる。

## 5. Move natsume-02 k3s away from local etcd

`natsume-03/04/05` の etcd が healthy になったら、k3s server join の前に `natsume-02` を etcd member から外す。4 member etcd は quorum が 3 になるため、移行中の時間を短くする。

まず `natsume-02` 上の k3s が `192.168.9.2:2379` に依存しないようにする。

`natsume-02` で `/etc/rancher/k3s/config.yaml` を編集する。

```yaml
datastore-endpoint: "https://192.168.9.3:2379,https://192.168.9.4:2379,https://192.168.9.5:2379"
```

`natsume-02` の k3s を restart する。

```text
sudo systemctl restart k3s
kubectl get nodes -o wide
kubectl get --raw=/readyz
```

必要なら `natsume-02` の etcd を一時停止して、k3s API が `natsume-03/04/05` の etcd だけで動くことを確認する。

```text
sudo systemctl stop etcd
kubectl get nodes
sudo systemctl start etcd
```

注意:

- 02 の向け変え後、02 退役完了まで `install-k3s` role を 02 に再適用しない。
- 再適用すると inventory の `groups['etcd']` 由来の endpoint に戻る可能性がある。

## 6. Remove natsume-02 from etcd

`natsume-02` の k3s が 03/04/05 の etcd を見ていることを確認してから、`natsume-02` を etcd member から削除する。

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/remove-etcd-member.yaml \
  -e etcd_member_host=natsume-02 \
  -e etcd_member_leader_host=natsume-03
```

確認:

```text
etcdctl endpoint status --cluster -w table
etcdctl endpoint health --cluster -w table
etcdctl member list -w table
```

期待:

- etcd members は `natsume-03/04/05` の3台。
- quorum は2に戻る。
- `natsume-02` の k3s API は正常。

この後、`ansible/inventories/hosts.yaml` の `etcd` group から `natsume-02` を外す。`k3s_server` にはまだ残してよい。

```yaml
etcd:
  hosts:
    natsume-03:
    natsume-04:
    natsume-05:
```

この変更後に以降の k3s server join を行う。これにより、03/04/05 の k3s config に入る `datastore-endpoint` は 03/04/05 のみになる。

## 7. Join k3s servers

`natsume-03/04/05` を k3s server として join する。`install-k3s-servers.yaml` を新規 node に限定して流す。

```text
ansible-playbook -i ansible/inventories/hosts.yaml ansible/install-k3s-servers.yaml \
  --limit natsume-03,natsume-04,natsume-05
```

確認:

```text
kubectl get nodes -o wide
kubectl get --raw=/readyz
kubectl -n kube-system get pods -o wide
```

期待:

- `InternalIP` は private IP。
- `ExternalIP` は global IP。
- Cilium が各 node に配置される。
- k3s の `node-ip` / `advertise-address` は private 側。
- k3s の `datastore-endpoint` は `192.168.9.3/4/5:2379` のみ。

## 8. Verify ingress exposure

Traefik が各 node に DaemonSet として配置され、Service の external IP に global IP が並ぶことを確認する。

```text
kubectl -n traefik get pods -o wide
kubectl -n traefik get svc traefik -o wide
```

public DNS / external-dns の挙動を確認し、private IP が公開 DNS に混ざっていないことを確認する。

## 9. Apply Longhorn from this branch

Longhorn の default disk は label 付き node にのみ作成する設定なので、03/04/05 に label を付ける。

```text
kubectl label node natsume-03 node.longhorn.io/create-default-disk=true --overwrite
kubectl label node natsume-04 node.longhorn.io/create-default-disk=true --overwrite
kubectl label node natsume-05 node.longhorn.io/create-default-disk=true --overwrite
```

この branch の PR は後で merge する想定のため、この段階では Flux root ではなく app manifest を直接適用する。

```text
kubectl apply -k flux/clusters/natsume/apps/longhorn
```

確認:

```text
kubectl -n longhorn-system get pods -o wide
kubectl get sc
```

この時点では Longhorn は default StorageClass ではない。

## 10. Make Longhorn the default StorageClass

Longhorn が healthy で、03/04/05 に default disk が作成されていることを確認する。

```text
kubectl -n longhorn-system get pods -o wide
kubectl get nodes -L node.longhorn.io/create-default-disk
kubectl get sc
```

`longhorn` を default StorageClass にし、`local-path` の default annotation を外す。

```text
kubectl annotate storageclass local-path storageclass.kubernetes.io/is-default-class-
kubectl annotate storageclass longhorn storageclass.kubernetes.io/is-default-class=true --overwrite
kubectl get sc
```

期待:

- `longhorn` が default。
- `local-path` は残るが default ではない。

この段階では既存 PVC の StorageClass は変わらない。以降、PVC を再作成したものだけ Longhorn になる。

## 11. Recreate disposable PVCs on Longhorn

消失してよい PVC は、対象 workload を停止して PVC を再作成する。

対象:

- Mimir local PVC
- Loki local PVC
- Valkey PVC

Valkey は次を対象にする。

```text
rss-fetcher/rss-fetcher-valkey
emoji-service/emoji-bot-gateway-valkey
registry/registry-valkey
misskey/misskey-valkey
```

Mimir:

```text
flux suspend helmrelease -n mimir mimir
kubectl -n mimir scale statefulset mimir --replicas=0 || true
kubectl -n mimir scale deployment mimir --replicas=0 || true
kubectl -n mimir get pvc -o wide
kubectl -n mimir delete pvc --all
flux resume helmrelease -n mimir mimir
kubectl -n mimir get pvc -o wide
kubectl -n mimir get pods -o wide
```

Loki:

```text
flux suspend helmrelease -n loki loki
kubectl -n loki scale statefulset loki --replicas=0 || true
kubectl -n loki scale deployment loki --replicas=0 || true
kubectl -n loki get pvc -o wide
kubectl -n loki delete pvc --all
flux resume helmrelease -n loki loki
kubectl -n loki get pvc -o wide
kubectl -n loki get pods -o wide
```

rss-fetcher Valkey:

```text
flux suspend helmrelease -n rss-fetcher rss-fetcher-valkey
kubectl -n rss-fetcher scale statefulset rss-fetcher-valkey --replicas=0 || true
kubectl -n rss-fetcher get pvc -o wide
kubectl -n rss-fetcher delete pvc -l app.kubernetes.io/instance=rss-fetcher-valkey
flux resume helmrelease -n rss-fetcher rss-fetcher-valkey
kubectl -n rss-fetcher get pvc -o wide
kubectl -n rss-fetcher get pods -o wide
```

emoji-service Valkey:

```text
flux suspend helmrelease -n emoji-service emoji-bot-gateway-valkey
kubectl -n emoji-service scale statefulset emoji-bot-gateway-valkey --replicas=0 || true
kubectl -n emoji-service get pvc -o wide
kubectl -n emoji-service delete pvc -l app.kubernetes.io/instance=emoji-bot-gateway-valkey
flux resume helmrelease -n emoji-service emoji-bot-gateway-valkey
kubectl -n emoji-service get pvc -o wide
kubectl -n emoji-service get pods -o wide
```

registry Valkey:

```text
flux suspend helmrelease -n registry registry-valkey
kubectl -n registry scale statefulset registry-valkey --replicas=0 || true
kubectl -n registry get pvc -o wide
kubectl -n registry delete pvc -l app.kubernetes.io/instance=registry-valkey
flux resume helmrelease -n registry registry-valkey
kubectl -n registry get pvc -o wide
kubectl -n registry get pods -o wide
```

Misskey Valkey は PR #416 適用後に実施する。

```text
flux suspend helmrelease -n misskey misskey-valkey
kubectl -n misskey scale statefulset misskey-valkey --replicas=0 || true
kubectl -n misskey get pvc -o wide
kubectl -n misskey delete pvc -l app.kubernetes.io/instance=misskey-valkey
flux resume helmrelease -n misskey misskey-valkey
kubectl -n misskey get pvc -o wide
kubectl -n misskey get pods -o wide
```

期待:

- 再作成された PVC の `STORAGECLASS` が `longhorn`。
- app が起動する。

Mimir/Loki は object storage 側のデータを正とし、local PVC は消失許容として扱う。

## 12. Migrate Grafana / Uptime Kuma / Navidrome PVCs

Grafana、Uptime Kuma、Navidrome は停止コピーで Longhorn PVC に移す。SQLite や metadata の整合性を優先し、起動中コピーはしない。

対象 PVC を確認する。

```text
kubectl -n grafana get pvc
kubectl -n uptime-kuma get pvc
kubectl -n navidrome get pvc
```

Grafana:

```text
flux suspend helmrelease -n grafana grafana
kubectl -n grafana scale deployment grafana --replicas=0
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: grafana-longhorn
  namespace: grafana
spec:
  accessModes:
  - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 10Gi
EOF
cat <<EOF | kubectl -n grafana apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: grafana-pvc-copy
spec:
  restartPolicy: Never
  containers:
  - name: copy
    image: alpine:3.20
    command: ["sh", "-c", "sleep 3600"]
    volumeMounts:
    - name: old
      mountPath: /old
      readOnly: true
    - name: new
      mountPath: /new
  volumes:
  - name: old
    persistentVolumeClaim:
      claimName: grafana
      readOnly: true
  - name: new
    persistentVolumeClaim:
      claimName: grafana-longhorn
EOF
kubectl -n grafana wait --for=condition=Ready pod/grafana-pvc-copy --timeout=120s
kubectl -n grafana exec grafana-pvc-copy -- sh -c 'cd /old && tar cf - . | tar xf - -C /new && sync'
kubectl -n grafana delete pod grafana-pvc-copy
kubectl -n grafana patch helmrelease grafana --type=json -p='[{"op":"add","path":"/spec/values/persistence/existingClaim","value":"grafana-longhorn"}]'
flux resume helmrelease -n grafana grafana
kubectl -n grafana get pvc -o wide
kubectl -n grafana get pods -o wide
```

Uptime Kuma:

```text
flux suspend helmrelease -n uptime-kuma uptime-kuma
kubectl -n uptime-kuma scale deployment uptime-kuma --replicas=0
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: uptime-kuma-longhorn
  namespace: uptime-kuma
spec:
  accessModes:
  - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 4Gi
EOF
cat <<EOF | kubectl -n uptime-kuma apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: uptime-kuma-pvc-copy
spec:
  restartPolicy: Never
  containers:
  - name: copy
    image: alpine:3.20
    command: ["sh", "-c", "sleep 3600"]
    volumeMounts:
    - name: old
      mountPath: /old
      readOnly: true
    - name: new
      mountPath: /new
  volumes:
  - name: old
    persistentVolumeClaim:
      claimName: uptime-kuma-pvc
      readOnly: true
  - name: new
    persistentVolumeClaim:
      claimName: uptime-kuma-longhorn
EOF
kubectl -n uptime-kuma wait --for=condition=Ready pod/uptime-kuma-pvc-copy --timeout=120s
kubectl -n uptime-kuma exec uptime-kuma-pvc-copy -- sh -c 'cd /old && tar cf - . | tar xf - -C /new && sync'
kubectl -n uptime-kuma delete pod uptime-kuma-pvc-copy
kubectl -n uptime-kuma patch helmrelease uptime-kuma --type=json -p='[{"op":"add","path":"/spec/values/volume/existingClaim","value":"uptime-kuma-longhorn"}]'
flux resume helmrelease -n uptime-kuma uptime-kuma
kubectl -n uptime-kuma get pvc -o wide
kubectl -n uptime-kuma get pods -o wide
```

Navidrome:

```text
flux suspend helmrelease -n navidrome navidrome
kubectl -n navidrome scale deployment navidrome --replicas=0
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: navidrome-data-longhorn
  namespace: navidrome
spec:
  accessModes:
  - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 10Gi
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: navidrome-music-longhorn
  namespace: navidrome
spec:
  accessModes:
  - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 100Gi
EOF
cat <<EOF | kubectl -n navidrome apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: navidrome-data-pvc-copy
spec:
  restartPolicy: Never
  containers:
  - name: copy
    image: alpine:3.20
    command: ["sh", "-c", "sleep 3600"]
    volumeMounts:
    - name: old
      mountPath: /old
      readOnly: true
    - name: new
      mountPath: /new
  volumes:
  - name: old
    persistentVolumeClaim:
      claimName: navidrome-data-pvc
      readOnly: true
  - name: new
    persistentVolumeClaim:
      claimName: navidrome-data-longhorn
EOF
kubectl -n navidrome wait --for=condition=Ready pod/navidrome-data-pvc-copy --timeout=120s
kubectl -n navidrome exec navidrome-data-pvc-copy -- sh -c 'cd /old && tar cf - . | tar xf - -C /new && sync'
kubectl -n navidrome delete pod navidrome-data-pvc-copy
cat <<EOF | kubectl -n navidrome apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: navidrome-music-pvc-copy
spec:
  restartPolicy: Never
  containers:
  - name: copy
    image: alpine:3.20
    command: ["sh", "-c", "sleep 3600"]
    volumeMounts:
    - name: old
      mountPath: /old
      readOnly: true
    - name: new
      mountPath: /new
  volumes:
  - name: old
    persistentVolumeClaim:
      claimName: navidrome-music-pvc
      readOnly: true
  - name: new
    persistentVolumeClaim:
      claimName: navidrome-music-longhorn
EOF
kubectl -n navidrome wait --for=condition=Ready pod/navidrome-music-pvc-copy --timeout=120s
kubectl -n navidrome exec navidrome-music-pvc-copy -- sh -c 'cd /old && tar cf - . | tar xf - -C /new && sync'
kubectl -n navidrome delete pod navidrome-music-pvc-copy
kubectl -n navidrome patch helmrelease navidrome --type=json -p='[{"op":"add","path":"/spec/values/persistence/data","value":{"enabled":true,"existingClaim":"navidrome-data-longhorn","size":"10Gi"}},{"op":"add","path":"/spec/values/persistence/music/existingClaim","value":"navidrome-music-longhorn"}]'
flux resume helmrelease -n navidrome navidrome
kubectl -n navidrome get pvc -o wide
kubectl -n navidrome get pods -o wide
```

このコピー後に HelmRelease values を新 PVC に向ける変更を Git にも反映する。上の `kubectl patch helmrelease ...` は live state の向け変えなので、PR merge 後に Flux が戻さないよう manifest 側も同じ値にする。

旧 PVC/PV はすぐ削除しない。一定期間、rollback 用に保持する。

## 13. Migrate CNPG clusters to Longhorn

DB ごとに1つずつ実施する。複数 DB を同時に移行しない。

現行対象:

```text
spotify-reblend/reblend-cluster
spotify-nowplaying/spn-cluster
sui/sui-cluster
misskey/misskey-cluster
```

Misskey 以外は最終 `instances: 1`。Misskey は最終 `instances: 2`。

事前に `jq` が使えることを確認する。この関数は Longhorn PVC を持つ standby instance を検出し、その instance を primary に昇格する。

```text
jq --version
```

```text
migrate_cnpg_to_longhorn() {
  NS="$1"
  CLUSTER="$2"
  FINAL_INSTANCES="$3"

  kubectl cnpg backup -n "$NS" "$CLUSTER"
  kubectl cnpg status -n "$NS" "$CLUSTER"

  kubectl -n "$NS" patch cluster "$CLUSTER" --type=merge -p '{"spec":{"instances":2}}'

  kubectl -n "$NS" wait pod \
    -l "cnpg.io/cluster=${CLUSTER}" \
    --for=condition=Ready \
    --timeout=600s

  kubectl cnpg status -n "$NS" "$CLUSTER"
  kubectl -n "$NS" get pods -o wide
  kubectl -n "$NS" get pvc -o wide

  LONGHORN_INSTANCE="$(
    kubectl -n "$NS" get pvc -o json \
      | jq -r --arg CLUSTER "$CLUSTER" '
          .items[]
          | select(.metadata.labels["cnpg.io/cluster"] == $CLUSTER)
          | select(.spec.storageClassName == "longhorn")
          | .metadata.labels["cnpg.io/instanceName"]
        ' \
      | grep -v '^null$' \
      | head -n 1
  )"

  test -n "$LONGHORN_INSTANCE"

  kubectl cnpg promote -n "$NS" "$CLUSTER" "$LONGHORN_INSTANCE"
  kubectl cnpg status -n "$NS" "$CLUSTER"

  if [ "$FINAL_INSTANCES" = "1" ]; then
    kubectl -n "$NS" patch cluster "$CLUSTER" --type=merge -p '{"spec":{"instances":1}}'
  fi

  kubectl cnpg status -n "$NS" "$CLUSTER"
  kubectl -n "$NS" get pods -o wide
  kubectl -n "$NS" get pvc -o wide
}
```

通常 DB:

```text
migrate_cnpg_to_longhorn spotify-reblend reblend-cluster 1
migrate_cnpg_to_longhorn spotify-nowplaying spn-cluster 1
migrate_cnpg_to_longhorn sui sui-cluster 1
```

Misskey は PR #416 適用後に実施し、最終 `instances: 2` を維持する。

```text
migrate_cnpg_to_longhorn misskey misskey-cluster 2
```

期待:

- 残す CNPG PVC は `longhorn`。
- 通常 DB は `instances: 1`。
- Misskey は `instances: 2`。
- app 側は CNPG の `*-rw` Service を使うため、通常は接続先変更不要。

旧 local-path 側 instance/PVC が残る場合だけ、状態を確認してから削除する。

```text
kubectl -n spotify-reblend get pods,pvc -o wide
kubectl -n spotify-nowplaying get pods,pvc -o wide
kubectl -n sui get pods,pvc -o wide
kubectl -n misskey get pods,pvc -o wide
```

CNPG plugin で明示的に削除する場合は、削除対象 instance 名を `kubectl cnpg status` で確認してから実行する。

### 13.4 Commit manifest changes

移行結果に合わせて Git 側の Cluster manifest を更新する。

- 通常 DB: `instances: 1`
- Misskey: `instances: 2`
- 必要なら `storage.storageClass: longhorn` を明示する

Flux 管理へ戻したときに live state が巻き戻らないことを確認する。

## 14. Verify no workload depends on local-path

`natsume-02` を drain する前に、local-path PV/PVC が残っていないことを確認する。

```text
kubectl get pv -o wide
kubectl get pvc -A -o wide
```

`local-path` の PV が残っている場合、その workload はまだ移行できていない。

node 配置も確認する。

```text
kubectl get pods -A -o wide | grep natsume-02
```

DaemonSet 以外の workload が `natsume-02` に残らない状態を目指す。

## 15. Cordon and drain natsume-02

`natsume-02` を scheduling 対象から外す。

```text
kubectl cordon natsume-02
kubectl drain natsume-02 --ignore-daemonsets --delete-emptydir-data
```

確認:

```text
kubectl get nodes -o wide
kubectl get pods -A -o wide | grep natsume-02
```

DaemonSet 以外の pod が残っていないことを確認する。

## 16. Remove natsume-02 from k3s

`natsume-02` 上で k3s を停止する。

```text
sudo systemctl stop k3s
sudo systemctl disable k3s
```

cluster から node object を削除する。

```text
kubectl delete node natsume-02
kubectl get nodes -o wide
```

期待:

- Kubernetes nodes は `natsume-03/04/05` のみ。
- Traefik Service の external IP から `natsume-02` の global IP が消える。

```text
kubectl -n traefik get svc traefik -o wide
```

## 17. Remove natsume-02 from Ansible inventory

Git 側で `natsume-02` を active cluster から外す。

`ansible/inventories/hosts.yaml`:

- `etcd` から `natsume-02` が外れていることを確認する。
- `k3s_server` から `natsume-02` を外す。
- 必要なら `natsume-02` の host_vars を削除するか、退役済み host として別管理に移す。

最終形:

```yaml
etcd:
  hosts:
    natsume-03:
    natsume-04:
    natsume-05:
k3s_cluster:
  children:
    k3s_server:
      hosts:
        natsume-03:
        natsume-04:
        natsume-05:
    k3s_agent:
      hosts: {}
longhorn_storage:
  hosts:
    natsume-03:
    natsume-04:
    natsume-05:
```

## 18. Final verification

Control plane / etcd:

```text
kubectl get nodes -o wide
kubectl get --raw=/readyz
etcdctl endpoint status --cluster -w table
etcdctl endpoint health --cluster -w table
etcdctl member list -w table
```

Storage:

```text
kubectl get sc
kubectl get pv,pvc -A
kubectl -n longhorn-system get pods -o wide
```

Apps:

```text
flux get kustomizations -A
flux get helmreleases -A
kubectl get pods -A -o wide
```

Ingress:

```text
kubectl -n traefik get svc traefik -o wide
kubectl -n traefik get pods -o wide
```

Expected final state:

- etcd members are `natsume-03/04/05`.
- Kubernetes nodes are `natsume-03/04/05`.
- Longhorn has disks on `natsume-03/04/05`.
- New/default PVCs use `longhorn`.
- No required workload depends on `local-path`.
- `natsume-02` is not serving Kubernetes or etcd.

## Notes

- `site-k3s.yaml` は、この移行中の新 node join には使わない。
- `setup-etcd` は初回構築向け。既存 cluster 拡張は `add-etcd-member.yaml` を使う。
- `natsume-02` の k3s datastore endpoint 向け変えと etcd remove は、k3s server join 前に実施する。
- `natsume-02` 退役前に、local-path PVC が残っていないことを確認する。
