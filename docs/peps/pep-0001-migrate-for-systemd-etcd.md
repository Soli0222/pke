# PEP-0001: Kubernetes の etcd を static Pod から systemd 管理へ移行する

## ステータス

- Accepted

## 背景

現行の Kubernetes コントロールプレーンは `kubeadm` デフォルト構成で、`etcd` は static Pod（stacked etcd）として稼働している。
運用要件として、`etcd` の起動順序・障害切り分け・再起動ポリシーを host レベルで明示制御したいため、`systemd` 管理へ移行する。

## 目的

- 既存クラスターを停止時間とリスクを最小化して `systemd etcd` へ移行する。
- 新規構築時に `kubeadm init` 前提の証明書/etcd ブートストラップ手順を確立する。
- `kubeadm`・`join`・`upgrade` の整合を維持する。

## 非目的

- etcd 専用ノードへの分離
- CNI / LB / 監視スタックの設計変更
- Worker 管理フローの変更

## 現状（リポジトリ）

- `kubeadm init`: `ansible/roles/init-cp-kubernetes/tasks/main.yaml`
- `kubeadm` テンプレート: `ansible/roles/init-cp-kubernetes/templates/kubeadm-config.yaml.j2`（現状 minimal）
- `join control plane`: `ansible/roles/join-cp-kubernetes/tasks/main.yaml`
- `upgrade`: `ansible/roles/upgrade-kubernetes/tasks/main.yaml`

## 採用方針（決定）

1. etcd は control-plane ノード上で `systemd` 管理する（外部 etcd 構成として kubeadm から参照）。
2. 既存移行では kubeadm 既存証明書を流用する。
   - 利用パス: `/etc/kubernetes/pki/etcd/`
   - `bootstrap-etcd-certs` は既存移行では不要。
3. 新規構築では `bootstrap-etcd-certs` を実行してから `install-etcd-systemd` を実行する。
4. `kubeadm-config` は「ファイル」だけでなく `kube-system/kubeadm-config` ConfigMap まで更新する。
   - 更新方式は `kubeadm init phase upload-config` に統一する。
   - Ansible から `kubectl` は実行しない。
5. `kube-apiserver` static Pod manifest の etcd フラグ更新を移行手順に含める。
6. etcd アップグレードは Kubernetes アップグレードから分離し、専用 role/playbook で運用する。
7. etcd の配布は公式 release tarball を使用する。
   - 取得元形式: `https://github.com/etcd-io/etcd/releases/download/v${etcd_version}/etcd-v${etcd_version}-linux-amd64.tar.gz`
   - バージョンは inventory 変数で管理する（k8s/containerd と同様）。
8. `kubeadm` config API は `kubeadm.k8s.io/v1beta4` を採用する。
9. 既存移行の `install-etcd-systemd` では `systemctl enable` まで実施し、`start` はノード切替時のみ実施する。
10. `kube-apiserver` manifest 更新は直接編集ではなく `kubeadm init phase control-plane apiserver --config <file>` で再生成する。
    - このコマンドは実行ノードのローカル manifest のみ再生成するため、全 `k8s-cp` ノードで実行する。
11. `kubeadm init phase upload-config` は `k8s-cp-leader` でのみ実行する。
12. external etcd endpoints は `groups['k8s-cp']` から動的生成する（静的変数は通常運用で使わない）。
13. バージョン変数は現行リポジトリの実態に合わせて `group_vars/internal.yaml` に集約する。

## 追加/変更する Ansible 構成

### 新規 playbook

- `ansible/site-etcd.yaml`
  - 対象: `k8s-cp`
  - 目的: etcd precheck / install / migrate / postcheck 実行

- `ansible/upgrade-etcd.yaml`
  - 対象: `k8s-cp`
  - `serial: 1`
  - 目的: etcd バイナリのローリングアップグレード

### 新規 role

- `ansible/roles/etcd-precheck/`
  - snapshot 取得
  - endpoint/member health 検証
  - manifest/証明書/データディレクトリ存在確認

- `ansible/roles/install-etcd-systemd/`
  - etcd バイナリ配置
  - 公式 tarball を展開して `etcd`/`etcdctl` を配置
  - `etcd` user/group 作成
  - `/etc/etcd/etcd.env` と `etcd.service` 配置
  - 既存移行: `ETCD_INITIAL_CLUSTER_STATE=existing` を出力
  - 新規構築: `ETCD_INITIAL_CLUSTER_STATE=new` と `ETCD_INITIAL_CLUSTER` を出力
  - 既存移行では `enable` のみ実施（`start` はしない）
  - `Restart=always`, `RestartSec`, `LimitNOFILE` など unit 設定

- `ansible/roles/migrate-etcd-to-systemd/`
  - static Pod 停止 → データディレクトリ所有権変更 → systemd 起動
  - ノードごと health gate を実施（`serial: 1`）

- `ansible/roles/bootstrap-etcd-certs/`（新規構築トラック専用）
  - `etcd-ca`, `server`, `peer`, `healthcheck-client`, `apiserver-etcd-client` 生成/配布

- `ansible/roles/upgrade-etcd/`
  - etcd バージョン互換性チェック
  - ノード 1 台ずつバイナリ置換・再起動・health 検証

- `ansible/roles/etcd-maintenance/`
  - defrag/compaction の timer 配備
  - metrics endpoint 前提設定

### 既存 role の修正

- `ansible/roles/init-cp-kubernetes/templates/kubeadm-config.yaml.j2`
  - `etcd_mode: stacked|external` 分岐追加
  - `external` 時に `endpoints`, `caFile`, `certFile`, `keyFile` を出力
  - `apiVersion: kubeadm.k8s.io/v1beta4` を使用

- `ansible/roles/join-cp-kubernetes/tasks/main.yaml`
  - 前提: join 対象ノードで `systemd etcd` が起動済み・member 参加済み
  - `kubeadm join --control-plane` の前に etcd readiness gate を追加

- `ansible/roles/upgrade-kubernetes/tasks/main.yaml`
  - external etcd 前提の precheck を追加
  - 必要時 `kubeadm upgrade apply --config ...` の分岐を用意

- `ansible/site-k8s.yaml`
  - 実行順を調整し、etcd 準備 role を `init/join` より前に配置

## 実行フロー

### A. 既存クラスター移行

1. `etcd-precheck` 実行（snapshot 必須）
2. `install-etcd-systemd` を全 cp へ配備（`systemctl enable` のみ、`start` はしない）
3. ノードごと（`serial: 1`）に以下を実施
   - static Pod manifest 退避（`/etc/kubernetes/manifests/etcd.yaml`）
   - static Pod 停止確認
   - `/var/lib/etcd` の所有者を `etcd:etcd` へ変更
   - systemd etcd 起動
   - `etcdctl endpoint health` / `member list` 確認
4. `kubeadm-config` ファイルを leader ノードに生成（external etcd 設定を反映）
5. `kubeadm-config` ConfigMap 更新（leader ノードで実行）
   - `kube-system/kubeadm-config` の ClusterConfiguration を external etcd へ更新
   - 方式は `kubeadm init phase upload-config --config <file>` に固定
6. `kubeadm init phase control-plane apiserver --config <file>` で apiserver manifest を再生成（全 `k8s-cp` ノード）
7. apiserver 再起動完了を待ち、`/readyz` および etcd health で確認
8. postcheck（upgrade dry-run 相当、join 互換チェック）

### B. 新規構築

1. `bootstrap-etcd-certs`
2. `install-etcd-systemd`
3. etcd クラスタ形成確認
4. `init-cp-kubernetes`（`etcd_mode=external`）
5. `join-cp-kubernetes`（etcd readiness gate 付き）
6. `join-wk-kubernetes`

## 変数設計（案）

`ansible/inventories/group_vars/internal.yaml` 追加候補:

- `etcd_mode: external`
- `etcd_version: "<managed-version>"`
- `etcd_download_url: "https://github.com/etcd-io/etcd/releases/download/v{{ etcd_version }}/etcd-v{{ etcd_version }}-linux-amd64.tar.gz"`
- `etcd_data_dir: /var/lib/etcd`
- `etcd_conf_dir: /etc/etcd`
- `etcd_pki_dir: /etc/kubernetes/pki/etcd`
- `etcd_client_cert: /etc/kubernetes/pki/apiserver-etcd-client.crt`
- `etcd_client_key: /etc/kubernetes/pki/apiserver-etcd-client.key`
- `etcd_cluster_state_mode: existing|new`
- `etcd_upgrade_strategy: serial`
- `etcd_maintenance_enabled: true`

`etcd_endpoints` は変数で静的定義せず、`kubeadm-config.yaml.j2` 内で `groups['k8s-cp']` / `hostvars` から動的生成する。

## バージョン管理方針

- etcd は kubeadm 管理外になるため、Kubernetes と別 release train で管理する。
- `upgrade-etcd.yaml` で以下を必須化する。
  - 目標 Kubernetes バージョンに対する etcd 互換チェック
  - `serial: 1` のローリング
  - 各ノード更新後 health gate

推奨順序:

1. etcd を互換範囲の最新版へ更新
2. Kubernetes（kubeadm/kubelet/kubectl）を更新

## ロールバック

- ノード単位失敗時
  - systemd etcd 停止
  - 退避 manifest を戻して static Pod 復帰
  - health 確認後に次ノードへ進まない
- 全体障害時
  - snapshot から復旧

## 運用タスク（systemd 移行後）

- defrag/compaction の定期実行（systemd timer）
- etcd metrics 監視連携
- unit の再起動ポリシー見直し

## Done Criteria

- 全 cp ノードの etcd が `systemd` 管理で稼働
- static Pod etcd manifest は運用無効化（退避済み）
- `kubeadm-config` ConfigMap が external etcd を参照
- `kube-apiserver` manifest が external etcd を参照
- `kubeadm join --control-plane` と `kubeadm upgrade` が成功
- etcd maintenance timer が有効

## 未決事項

- なし（本PEP記載範囲）

## 実装レビュー指摘事項（第 3 回: コードレビュー）

### 取り込み結果

- IR-1: 対応済み。`reconfigure-kubeadm-external-etcd` のテンプレートへ `kubernetesVersion` / `imageRepository` / `certificatesDir` / `serviceSubnet` / `dnsDomain` / `proxy.disabled` / 証明書有効期間等を追加。
- IR-2: 対応済み。`site-k8s.yaml` の etcd 関連 role に `inventory_hostname in groups['k8s-cp']` 条件を追加。
- IR-3: 対応済み。`etcd-maintenance.sh` はローカル endpoint (`127.0.0.1:2379`) のみ defrag する方式へ変更。
- IR-4: 対応済み。`upgrade-etcd.yaml` で `etcd-precheck` を先行実行し snapshot を取得。
- IR-5: 対応済み。`install-etcd-systemd` は既存 `etcd_data_dir` の所有者を変更しない実装へ変更。
- IR-6: 対応済み。`etcd-precheck` に `member list` と `inventory_hostname` 一致確認の assert を追加。
- IR-7: 対応済み。`etcd-precheck` は `/usr/local/bin/etcdctl` が存在すればダウンロードをスキップ。
- IR-8: 対応済み。`bootstrap-etcd-certs` cleanup に OpenSSL 設定ファイル削除を追加。

### IR-1. [高] `reconfigure-kubeadm-external-etcd` のテンプレートが ClusterConfiguration を上書きするリスク

`reconfigure-kubeadm-external-etcd/templates/kubeadm-config.yaml.j2` は `controlPlaneEndpoint`、`podSubnet`、`etcd.external` のみを含む最小構成。
`kubeadm init phase upload-config kubeadm --config <file>` はこのファイルの内容で `kube-system/kubeadm-config` ConfigMap の **ClusterConfiguration 全体を置換** する。
現行 ConfigMap に含まれる `kubernetesVersion`、`imageRepository`、`dns`、`apiServer` 設定等が欠落し、デフォルト値に戻るリスクがある。

**対策案**: 現行 ConfigMap から `kubernetesVersion` 等の必要フィールドを取得してテンプレートに含めるか、テンプレートに `kubernetesVersion: "{{ kubernetes_version }}"` を追加する。同様に `kubeadm init phase control-plane apiserver` も同じ最小テンプレートを使うため、apiserver manifest から既存のカスタムフラグが消える可能性がある。

### IR-2. [高] `site-k8s.yaml` で etcd role が worker ノードにも適用される

`site-k8s.yaml` は `hosts: k8s` を対象としている。現在 `k8s-wk` グループは空だが、将来 worker ノードを追加した場合、`bootstrap-etcd-certs` と `install-etcd-systemd` が worker にも実行される。
`when: (etcd_mode | default('stacked')) == 'external'` だけでは不十分。

**対策案**: `when` 条件に `inventory_hostname in groups['k8s-cp']` を追加するか、`groups['k8s-cp-leader'] + groups['k8s-cp-follower']` でフィルタする。

### IR-3. [高] `etcd-maintenance` が全ノードから全 endpoint に defrag を実行する

`etcd-maintenance.sh.j2` は全 `k8s-cp` の endpoint に対して defrag を実行する。このスクリプトが全 cp ノードの timer から 6 時間ごとに実行されるため、各 endpoint が N 回（= cp ノード数）defrag される。
defrag は etcd をブロックする操作であり、同時実行は可用性に影響する。

**対策案**: ローカル endpoint（`https://127.0.0.1:2379`）のみを defrag する方式に変更する。各ノードが自分自身だけを defrag すれば、全ノードがカバーされる。

### IR-4. [中] `upgrade-etcd` が事前 snapshot を取らない

`upgrade-etcd.yaml` は `upgrade-etcd` role のみを実行し、`etcd-precheck`（snapshot 取得）を含まない。
アップグレード失敗時にデータ破損が発生した場合、snapshot がなければ復旧できない。

**対策案**: `upgrade-etcd.yaml` に `etcd-precheck` role を先行実行として追加する。

### IR-5. [中] `install-etcd-systemd` が移行フローで `/var/lib/etcd` の所有者を変更する

`install-etcd-systemd` のディレクトリ作成タスクが `/var/lib/etcd` の所有者を `etcd:etcd` に設定する。
移行フロー（`site-etcd.yaml`）では static Pod がまだ稼働中のタイミングでこの role が実行される。static Pod 内の etcd は root で動作するため実害は少ないが、意図しない所有者変更が移行前に発生する。

**対策案**: `install-etcd-systemd` で `/var/lib/etcd` の所有者変更は行わず、`migrate-etcd-to-systemd` の `chown -R` に統一する。`install-etcd-systemd` では `etcd_data_dir` が既に存在する場合は所有者変更をスキップするか、ディレクトリ作成のみ（`state: directory` + 所有者変更なし）にする。

### IR-6. [中] `ETCD_NAME` と既存メンバー名の一致確認が不足

`etcd.env.j2` は `ETCD_NAME={{ inventory_hostname }}`（例: `kkg-cp1`）を設定する。
既存の stacked etcd のメンバー名は kubeadm がノードのホスト名から設定している。
`inventory_hostname` と実際のホスト名が一致しない場合、`ETCD_INITIAL_CLUSTER_STATE=existing` で起動しても既存データディレクトリとメンバー名が不一致となり起動に失敗する。

**対策案**: `etcd-precheck` に `etcdctl member list` の出力からメンバー名を採取し、`inventory_hostname` との一致を検証する assert を追加する。

### IR-7. [低] `etcd-precheck` が `/tmp` に tarball をダウンロードする重複

`etcd-precheck` が `/tmp/etcd-v{{ etcd_version }}-linux-amd64.tar.gz` にダウンロード・展開して etcdctl を取得するが、`install-etcd-systemd` も `/var/lib/etcd/downloads/` に同じ tarball をダウンロードする。
移行フローでは両方が順番に実行されるため、同じファイルを 2 回ダウンロードする。

**対策案**: `etcd-precheck` を `/usr/local/bin/etcdctl` の存在確認付きにし、なければダウンロード、あればスキップする。または `install-etcd-systemd` を先に実行してから precheck を実行する順序に変更する。

### IR-8. [低] `bootstrap-etcd-certs` の一時ファイル未削除

CSR ファイル（`/tmp/etcd-*.csr`）は cleanup タスクで削除されているが、OpenSSL 設定ファイル（`/tmp/etcd-server-openssl.cnf`、`/tmp/etcd-peer-openssl.cnf`）が削除されていない。

## レビュー指摘事項（第 2 回）取り込み結果

- R2-1: 取り込み済み。既存移行 Step 2 は `enable` のみ、`start` は Step 3 に確定。
- R2-2: 取り込み済み。`ETCD_INITIAL_CLUSTER_STATE` を移行種別で分岐（既存=`existing`、新規=`new`）。
- R2-3: 取り込み済み。apiserver は `kubeadm init phase control-plane apiserver --config` で再生成。
- R2-4: 取り込み済み。`upload-config` は leader ノードのみで実行し、事前に更新済み config ファイルを生成。
- R2-5: 取り込み済み。external etcd endpoints は inventory から動的生成。
- R2-6: 取り込み済み。本リポジトリ実態に合わせ、バージョン変数は `group_vars/internal.yaml` に集約。
- 補足: `kubeadm init phase control-plane apiserver --config <file>` はローカル manifest 再生成のため、全 `k8s-cp` ノードで実行する。

## 実装タスク分解（チェックリスト）

### 実装進捗（2026-02-22）

- 実装フェーズ（ロール/プレイブック追加と既存ロール改修）は完了。
- 未完了は「検証環境での実行確認」「本番適用ゲート」。
- `ansible-playbook --syntax-check` はローカル実行で完了（`site-k8s.yaml` / `site-etcd.yaml` / `upgrade-etcd.yaml` / `site-monitoring.yaml`）。
- 第3回レビュー指摘取り込み後も再度 `--syntax-check` を実行し、同4プレイブックでエラーなしを確認。
- `--list-tasks` で etcd 関連タスクの展開を確認済み（`bootstrap-etcd-certs` / `install-etcd-systemd` / `reconfigure-kubeadm-external-etcd` / `install-alloy: Configure Prometheus for etcd`）。
- 監視連携は `install-alloy` に `etcd.alloy` を追加し、`k8s-cp` のみに配布する方式で実装済み。

### 現行状態メモ（2026-02-22 採取）

- `kubeadm version`: `v1.35.1`（BuildDate: `2026-02-10T12:55:17Z`）。
- `kube-system/kubeadm-config` は `etcd.local.dataDir=/var/lib/etcd`（stacked etcd）を保持。
- `kube-apiserver` static Pod manifest は `--etcd-servers=https://127.0.0.1:2379` を使用。
- 同 manifest で `--etcd-cafile=/etc/kubernetes/pki/etcd/ca.crt`、`--etcd-certfile=/etc/kubernetes/pki/apiserver-etcd-client.crt`、`--etcd-keyfile=/etc/kubernetes/pki/apiserver-etcd-client.key` を使用。
- control-plane ノードは `kkg-cp1/2/3` の 3 台が `Ready`、Kubernetes `v1.35.1` で稼働中。

### 0. 事前調査

- [x] 現行クラスタで `kubeadm version` を確認
- [x] 現行 `kube-system/kubeadm-config` の ClusterConfiguration を採取
- [x] 現行 `kube-apiserver` manifest の etcd 関連フラグを採取
- [x] `etcd_version` の初期値を決定（例: `3.6.8`）

### 1. 変数・テンプレート基盤

- [x] `ansible/inventories/group_vars/internal.yaml` に etcd 変数を追加
- [x] `ansible/roles/init-cp-kubernetes/templates/kubeadm-config.yaml.j2` に `etcd_mode` 分岐を追加
- [x] external etcd 用の `endpoints` を `groups['k8s-cp']` から動的生成
- [x] external etcd 用の `caFile` / `certFile` / `keyFile` 出力を実装
- [x] `kubeadm` API version を `v1beta4` へ更新してテンプレートへ反映

### 2. etcd ロール実装

- [x] `ansible/roles/etcd-precheck/` を作成
- [x] `ansible/roles/install-etcd-systemd/` を作成
- [x] `install-etcd-systemd` に `ETCD_INITIAL_CLUSTER_STATE` 分岐（existing/new）を実装
- [x] `ansible/roles/migrate-etcd-to-systemd/` を作成
- [x] `ansible/roles/upgrade-etcd/` を作成
- [x] `ansible/roles/etcd-maintenance/` を作成
- [x] `ansible/roles/bootstrap-etcd-certs/` を作成（新規構築専用）

### 3. プレイブック実装

- [x] `ansible/site-etcd.yaml` を作成（precheck/install/migrate/postcheck）
- [x] `ansible/upgrade-etcd.yaml` を作成（`serial: 1`）
- [x] `ansible/site-k8s.yaml` の role 順序を見直し、etcd 準備を `init/join` より前へ移動

### 4. join/upgrade 互換対応

- [x] `ansible/roles/join-cp-kubernetes/tasks/main.yaml` に etcd readiness gate を追加
- [x] `ansible/roles/upgrade-kubernetes/tasks/main.yaml` に external etcd precheck を追加

### 5. 既存移行フロー実装（本命）

- [x] `install-etcd-systemd` は既存移行時 `enable` のみ実行（`start` しない）
- [x] static Pod manifest 退避処理を実装（ノード単位）
- [x] static Pod 停止確認処理を実装
- [x] `/var/lib/etcd` の `chown etcd:etcd` を実装
- [x] systemd etcd 起動処理を実装
- [x] ノードごとの health gate（`endpoint health` / `member list`）を実装
- [x] `serial: 1` を強制してローリング移行にする

### 6. kubeadm-config / apiserver 再構成

- [x] 更新済み kubeadm config ファイルを leader ノードへ事前配置
- [x] `kube-system/kubeadm-config` を external etcd へ更新するタスクを実装
- [x] 更新方式を `kubeadm init phase upload-config` に固定
- [x] `kubeadm init phase control-plane apiserver --config <file>` で manifest 再生成（全 `k8s-cp` ノード）
- [x] apiserver 再起動待機と疎通確認タスクを実装

### 7. 運用タスク実装

- [x] defrag/compaction の systemd timer を実装
- [x] etcd metrics 監視用設定（`install-alloy` + `etcd.alloy` を `k8s-cp` のみに配布）を実装
- [x] `etcd.service` の `Restart=` / `RestartSec=` / `WatchdogSec=` を最終化

### 8. テスト（検証環境）

- [ ] 新規構築トラックを通し実行して control-plane 構築を確認
- [ ] 既存移行トラックを通し実行してローリング移行を確認
- [ ] `kubeadm join --control-plane` の成功を確認
- [ ] `kubeadm upgrade` の成功（または dry-run）を確認
- [ ] ロールバック手順（manifest 戻し / snapshot 復旧）のリハーサルを実施

### 9. 本番適用ゲート

- [ ] メンテナンスウィンドウを確定
- [ ] snapshot 保管先と復旧責任者を確定
- [ ] 実行コマンドと実行順（runbook）を確定
- [ ] Done Criteria の全項目を満たしたことを記録
