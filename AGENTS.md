# AGENTS.md

PKE は Ansible、Helmfile、Flux CD、Terraform で K3s 基盤を管理する。
構成の入口は [README.md](README.md)、DB 運用は [CNPG.md](CNPG.md)、監視運用は [MONITORING.md](MONITORING.md) を参照する。
バージョン・アプリ一覧・IP は設定ファイルを正とし、この文書に複製しない。

## 変更の基本

- YAML は `.yaml`、Helmfile の Go template は `.gotmpl` を使う。
- Ansible built-in module は FQCN を優先し、既存の role boundary を保つ。
- Secret は 1Password Operator の `OnePasswordItem` を基本とする。平文の認証情報をコード、ログ、PR に残さない。
- Git commit は Conventional Commits を使う。ドキュメントとコメントは日本語でよい。
- 文書には現在の構成・操作・制約を書く。変更経緯、Issue ごとの作業記録、検証日時は本文に蓄積しない。
- 複数クラスタへの変更は、natsume / meruto それぞれの適用範囲と差分を確認する。

## Ansible とクラスタの識別

inventory は `ansible/inventories/hosts.yaml` の `all.children` 配下に置く。
K3s ノードの host_vars に `cluster: <name>` を必ず指定し、etcd は `<cluster>_etcd` グループに所属させる。
`setup-etcd` と `k3s_datastore_endpoint` は `groups[cluster + '_etcd']` を参照する。
クラスタ固有値は host_vars、group_vars、クラスタ別グループで管理する。

natsume は `natsume-03` が server / etcd、`natsume-08` が agent。
meruto は `meruto-01` が server / etcd。
agent の join 先は `ansible/inventories/group_vars/k3s_agent.yaml` に定義する。
`openclaw` グループは K3s クラスタとは別のホスト管理対象である。

ストレージの対象を実ディスクの存在だけで拡張しない。
`natsume-03` は Longhorn 600GB / TopoLVM 200GB を持つが、inventory の `longhorn_storage` / `topolvm_storage` に加えない。
`natsume-08` は TopoLVM が `/dev/vda4`、Longhorn が `/dev/vda5`。
`meruto-01` の Longhorn は `longhorn_storage_use_existing_vg: true` と `ubuntu-vg` を使う。

ネットワークは host_vars の `network_netplan` に定義する。
public interface がない meruto に global interface や public UFW rule を要求しない。
K3s の external IP と TLS SAN は host_vars の設定を尊重する。

## Flux と Helm chart

アプリは `flux/clusters/<cluster>/apps/<app>/` に置く。
追加時は `kustomizations/<app>.yaml` と root `kustomization.yaml` への登録をそろえ、CRD や Secret などの依存を `dependsOn` で表す。
Helmfile は Cilium、1Password Connect / Operator、Flux Operator の bootstrap を担当する。
その後のアプリは Flux で管理する。

| chart の種類 | 参照元 |
|---|---|
| upstream が配布する chart | upstream の `HelmRepository` |
| 別リポジトリで開発する自作アプリ | `oci://ghcr.io/soli0222/charts` の OCI `HelmRepository` |
| PKE 内で管理する chart | `flux-system` の `GitRepository` と `chart: ./charts/<name>` |

HelmRelease の chart version は Renovate が追跡できる形で明示する。
`charts/` の chart を変更したら `Chart.yaml` の `version` を上げる。
Flux は `ChartVersion` で artifact を更新するため、version 据え置きでは反映されない。
README だけの変更は version 更新を要しない。
`appVersion` はアプリのバージョンであり、chart version と区別する。

## ノード・クラスタを追加する

ノードの増減では `hosts.yaml` と `host_vars/<node>.yaml` を更新する。
natsume の node DNS は `flux/clusters/natsume/apps/external-dns-config/node-dnsendpoints.yaml` も確認する。
常駐サービスを変えたら `alloy_systemd_units` と監視ルールの hosts / units も更新する。

新規 K3s クラスタには以下をそろえる。

- inventory の `<cluster>_etcd` と host_vars の `cluster`
- `helmfile/environments/<cluster>.yaml` と `helmfile/manifests/flux/<cluster>/fluxinstance.yaml`
- `flux/clusters/<cluster>/` のアプリと root 登録
- Alloy のクラスタラベル、Mimir の保存先 prefix、通知経路

## DB と監視の変更

CNPG の DB は natsume のみで、すべて1 instance。
DB を追加・変更するときはバックアップ、PodMonitor、`cnpg_cluster` ラベル、監視ルールの databases をそろえ、[CNPG.md](CNPG.md) を更新する。
operator の存在だけで DB の存在を判断しない。

メトリクスの `cluster` は Kubernetes クラスタ、`cnpg_cluster` は DB 名に使う。
Alloy の共通 relabel 経路と、ルールの入力 matcher・出力 label・保存先 prefix の分離を維持する。
共通ルールの定義は `charts/monitoring-rules/`、クラスタ別設定は各 `apps/monitoring-rules/` に置く。
本番への障害注入・合成通知テストは実施しない。

## 検証と反映

変更に対応する確認を行い、結果と未確認の範囲を PR に記載する。

| 変更 | 確認 |
|---|---|
| Ansible | 対象 playbook の `--syntax-check`、対象ホストを限定した `--check --diff` |
| Helmfile | environment ごとの template / diff。1Password を使うため認証情報を出力しない |
| Flux / chart | 対象クラスタの Kustomize build、chart の lint / render、CI の `flux-diff` と `chart-version-guard` |
| 監視ルール・通知 | `scripts/validate-monitoring-rules.py`、`scripts/validate-ntfy-alerts.py` |
| Alloy のラベル・ホスト収集 | `scripts/validate-cluster-labels.py` |
| Terraform | 対象ディレクトリで fmt / validate / plan |
| 文書 | 設定との整合、リンクとコマンドの確認、`git diff --check` |

適用先は `natsume@soli` / `meruto@soli` を明示する。
Flux の反映は Ready に加えて適用 revision と対象リソースの generation を確認し、実際の収集・評価・アプリ状態まで照合する。
ホストの Alloy 更新は `ansible/update-alloy-monitoring.yaml` で1台ずつ行う。
