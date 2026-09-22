# Grafanaダッシュボードの管理

ダッシュボードはこのディレクトリでレビューし、Grafana native Git Syncで反映する。
同期先は専用フォルダに限定する。
JSONをConfigMap、Secret、GrafanaDashboard CRDに格納せず、Helm / Kustomize / Fluxから適用しない。
FluxはGrafanaの実行基盤と既存のPrometheusRuleを引き続き管理する。

## ファイルと保存形式

| 場所 | 用途 |
|---|---|
| `dashboards/<用途>/<名前>.json` | Git Sync対象のダッシュボード |
| `dashboards/<用途>/_folder.json` | フォルダUIDと表示名の固定 |
| `catalog.json` | UIDとファイルの対応、datasourceのUID、上流出典 |
| `schemas/` | PKEの保存形式を検証するJSON Schema |
| `requirements.txt` | 静的検証の依存パッケージ |

用途別のサブディレクトリをGrafanaのフォルダに対応させる。
各サブディレクトリには`_folder.json`を置き、`folder.grafana.app/v1beta1` / `Folder`のmetadata.nameとspec.titleを保存する。
同じUID・path・titleをcatalogの`folders`へ登録する。
既にGit Syncが作成したフォルダは実機のUIDを採用し、作り直さない。
`platform`、`observability`、`applications`、`developer-tools`を基本とし、Overviewは同期ルートに置く。
フォルダ名・パスの変更はGrafana側のフォルダ識別やリンクにも影響するため、単なる整理として移動しない。
同期対象は`grafana/dashboards/`だけにし、catalog、schema、バックアップ、認証情報を混ぜない。

保存するJSONは[Grafana公式のresource形式](https://grafana.com/docs/grafana/latest/as-code/observability-as-code/git-sync/export-resources/)を使う。
classicモデルは`dashboard.grafana.app/v1`、新モデルは`dashboard.grafana.app/v2`とする。
`kind`は`Dashboard`、`metadata.name`が永続的なdashboard UID、`spec`がダッシュボード本体である。
これはGrafana App Platform向けのファイルであり、Kubernetes APIには送らない。

```json
{
  "apiVersion": "dashboard.grafana.app/v1",
  "kind": "Dashboard",
  "metadata": { "name": "pke-example" },
  "spec": {
    "title": "PKE Example",
    "schemaVersion": 42,
    "time": { "from": "now-6h", "to": "now" },
    "panels": []
  }
}
```

上のJSONは形式の説明であり、運用する画面にはクエリ・変数・パネルを定義する。
実機のexportは`gcx dashboards get <uid> --api-version dashboard.grafana.app/v1 -o json`、または`v2`で取得できる。
APIの返却値をそのまま保存せず、metadataはnameのみとし、status・managedFields・resourceVersion・旧manager情報を除去する。
`spec`のid / uid / version / folderUIDも除去する。
UIDはmetadata、配置はGitのディレクトリ、履歴はGitで管理する。
UIからPRを作成した場合も、保存時に追加される情報をこの形式へ整理してからmergeする。

## データソースと上流出典

datasourceのUIDとtypeの対応は`catalog.json`に置く。
JSONには実UIDを明示するか、宣言済みのdatasource変数を参照させる。
PKE内では環境別の置換を行わず、cluster変数でnatsume / merutoを選ぶ。
別Grafanaへ移す場合はcatalogと定義内の参照を同じPRで変更し、実機の`gcx datasources list -o json`と照合する。
上流の`__inputs`や`${DS_PROMETHEUS}`等を解決してから保存する。

catalogの`dashboards`はUIDをキーにし、次の内容を記録する。

```json
{
  "path": "dashboards/platform/example.json",
  "title": "PKE Example",
  "origin": {
    "kind": "upstream",
    "url": "https://github.com/example/project/blob/<commit>/dashboard.json",
    "revision": "<commit SHA、またはGrafana LabsのIDとrevision>",
    "license": "<確認したlicenseと必要な表示先>",
    "changes": ["PKEのdatasourceとclusterラベルへ対応"]
  }
}
```

上流はcommitまたはrevisionを固定する。`main`や`latest`を追従する自動取得は行わない。
licenseの条件に必要な原文・著作権表示は同期対象外の`licenses/`に保存し、originから参照する。
PKE独自作成は`kind: custom`、既存画面の取り込みは`kind: existing`とする。
由来を確認できない既存画面はrevision / licenseを`unknown`とし、未確認の内容をchangesへ記す。推測した上流licenseを付けない。

## 検証と通常の更新

```sh
python3 -m pip install -r grafana/requirements.txt
python3 scripts/test-grafana-dashboards.py
python3 scripts/validate-grafana-dashboards.py
git diff --check
```

CIはGrafanaへの接続・認証なしで、JSONの構造、重複UID、panel ID、datasource参照、import placeholder、catalogとの一致を検証する。
PKEのschemaは保存形式の契約であり、Grafanaや各pluginの全仕様を複製したものではない。
plugin設定の妥当性、PromQL / LogQLの意味、データの有無、表示の品質は静的検証だけでは保証しない。
library panelはGit Syncで管理されないため、この管理対象では通常のpanelへ展開する。

1. 対象のmetric family・ラベルをgcxで確認し、JSONとcatalogを同じPRで編集する。
2. 静的検証を通し、主要クエリを両クラスタの実データで評価する。正常0件と欠測・対象外・エラーを区別する。
3. PRをスカッシュマージし、Git Syncの同期commitと保存済みUID・folder・変数・queryを読み戻す。
4. UIで変数、凡例、単位、色、No data、リンクを確認する。UI確認の担当者と結果をPRに記載する。

```sh
gcx config current-context
gcx config check
gcx resources get repositories -o json
gcx dashboards get <uid> -o json
```

接続の認証情報を含むexportやHTTP payloadの詳細ログは共有しない。
Repositoryの確認は必要なspec・statusだけを抽出する。
UIで変更する場合はGit Syncのブランチ経由でPRを作り、同じ検証を通す。
継続的な書き込み元はGit Syncのみとし、CIで`gcx push`やdashboard APIによる更新をしない。
接続・認証設定は[Git Syncの接続手順](git-sync/README.md)を参照する。

## ホストとsystemdの期待対象

`PKE / Host & Systemd`の期待対象はAnsible inventoryの`k3s_cluster`に所属するホストと、各host_varsの`cluster` / `alloy_systemd_units`から生成する。
クラスタ別monitoring-rulesの`hosts` / `units`との不一致は検証エラーにする。
ホストやunitを変更したら、同じPRで次を実行する。

```sh
python3 scripts/sync-grafana-host-inventory.py
python3 scripts/sync-grafana-host-inventory.py --check
python3 scripts/test-grafana-host-inventory.py
```

同期スクリプトはHost選択と期待状態のクエリだけを更新する。
CIはinventory・監視ルールの変更時にも実行し、生成忘れと設定の不一致を検知する。
PromQLの意味を確認するテストはDocker内のpromtoolを使い、fixtureだけで欠測・scrape失敗・inactive・failed・状態不整合を検証する。本番への障害注入はしない。

Host選択と期待unitの一覧は実系列の有無に依存しない。
有効な状態がないunitは「不明 / 欠測」と表示し、inactiveやfailedと区別する。
状態の推移には現在のinventoryを表示期間全体へ適用するため、ホスト・unit追加前の期間も不明になる。
Node Exporter FullとAlloyへのリンクは選択ホストと表示期間を引き継ぐ。

## Monitoring Pipelineの期待対象

`PKE / Monitoring Pipeline`は収集元・評価対象のclusterを選び、natsumeで共用するMimir / Loki / Alertmanager / ntfyと区別して表示する。
主要収集経路はKubernetes Alloy、API、kube-state-metrics、収集設定があるCoreDNSと、inventoryにある各ホストのAlloy / node exporter / kubelet / cAdvisor / etcdである。
各アプリexporterの完全な期待一覧ではなく、全jobの実測scrape失敗は別表で確認する。

期待対象は`up`や評価metricから列挙せず、次の設定から生成する。

- ホストとetcdはAnsible inventoryおよび監視ルールのhosts / unitsを照合する。
- 共通ルールとBlackboxのgroupは、各クラスタのHelmRelease valuesを使ったローカルchartの描画結果から取得する。
- 外部chartのgroup名は[pipeline-rule-groups.yaml](pipeline-rule-groups.yaml)へ登録し、参照先の有効化設定を確認する。外部chart更新時はgroup名を描画結果またはRuler APIと照合する。

```sh
python3 scripts/sync-grafana-pipeline-inventory.py
python3 scripts/sync-grafana-pipeline-inventory.py --check
python3 scripts/test-grafana-pipeline.py
```

同期にはHelmとPyYAML、時系列テストにはDockerを使う。
CIはinventory、対象chart・HelmReleaseの変更時にも生成差分と意味を検証する。
ルールの追加・削除・group名変更は期待一覧と同じPRで反映する。

主要収集経路の状態は120秒以内の`up`を使い、`up=0`と欠測を区別する。
Alloyの最終送信・Rulerの最終評価も、自己監視系列自体が120秒以上古ければ未確認とし、経過時間が5分を超えた対象を上段で数える。
固定の正常件数では判定せず、対象が消えても期待一覧へ欠測として残す。
この120秒・5分は画面上の確認基準であり、通知ルールの閾値やforを変更しない。

Rulerのサーバーmetricは`cluster=natsume`で取得し、保存先の`rule_group`に含まれるcluster prefixで評価対象を選ぶ。
共通基盤と通知は全クラスタ合算である。
webhook通知の試行数から過去のslack系列を除外し、未収集の失敗counterを0で補完して成功数を作らない。
ntfyのpublish counterは全用途合算の受付実績で、topic別・アラート専用の値でも、受信端末への配送保証でもない。
発火・抑制の状態はGrafana AlertingのMimir / Alertmanagerで確認し、空のalertlistを正常の証明には使わない。

## Overviewと調査リンク

[PKE / Overview](https://grafana.str08.net/d/pke-overview) は同期ルートの運用入口で、初期clusterはnatsume、期間は6時間、更新は1分とする。
12分野の現在値を「要確認」と「未確認」に分け、詳細画面へclusterと表示期間を引き継ぐ。
異常状態の件数であり、アラートの閾値・for・除外条件を再現した発火件数ではない。

期待するNode、etcd、DB、probeはinventoryとクラスタ設定から生成する。
Workload、Pod、PVC、Flux、証明書は現在観測した対象が基準で、一度も観測されていない個別resourceの存在までは保証しない。
KSM/証明書collectorの異常・欠測は各分野の確認項目にも含む。
120秒より古い系列は現在の状態判定に使わない。
どちらも0の場合も、詳細画面の全機能や復元可能性を保証する表示ではない。

DBのないclusterと、HTTP probeの設定がない観測元は対象外とする。
日次pg_dumpとbase backupの鮮度には監視ルールのpg_dump期限を使う。
Misskeyのbase backup成功時刻が未取得・0の場合は未確認であり、WAL成功をその代わりにしない。
Longhornのunknown/detachedと、期待PVに状態指標がない場合も未確認へ残す。

```sh
python3 scripts/sync-grafana-overview.py
python3 scripts/sync-grafana-overview.py --check
python3 scripts/test-grafana-overview.py
python3 scripts/validate-grafana-navigation.py
```

Node/DB/probe/監視ルール設定を変えた場合は同じPRで再生成する。
通知のリンクはmonitoring-rulesの`dashboard-links.yaml`でgroup既定と個別ruleのpanelを管理する。
UID・panel ID・変数名はGitの保存定義と静的照合し、RulerのURL展開をローカルのpromtoolで検証する。
通知は発生時点の表示期間を持たないため直近6時間を開き、Overviewからは利用者が選択した期間を引き継ぐ。

## 既存画面をUIDを保って移行する

既存画面の移行では、配置・パネル種別・色・凡例・折り畳み・既定の時間範囲を維持する。
初期cluster設定と移行に必要な参照整理を基本とし、見た目やクエリの再設計は移行と分けて扱う。

[公式の移行仕様](https://grafana.com/docs/grafana/latest/as-code/observability-as-code/git-sync/export-resources/)では、同一UIDの未管理画面があるとGit Syncが取り込めない。
元の画面を削除する必要があり、version historyは引き継がれない。
ダッシュボード定義の復元と履歴の保全は別に扱う。

1. 対象UID、元フォルダ、個別のpermission、manager、外部の原本・更新元を確認する。別管理元があれば二重更新を止める。
2. 同期対象外のアクセスを制限した保存先へ、未加工のresource JSON、classic dashboard JSON、permission、version一覧と必要な履歴を退避する。Grafana DBのバックアップも確認する。
3. 復旧用のJSONとpermissionの戻し方を準備する。UI確認用の新UIDコピーを作る場合は、旧UIDへの移行とは別の画面として扱う。
4. 対象JSONをPRでレビュー・mergeする。同期のUID競合を確認し、対応する元ダッシュボードだけを削除して再同期する。
5. 同期commit・UID・folder・permission・主要クエリを照合する。UI確認を終えてから次の画面へ進む。

フォルダ全体を削除しない。
同じフォルダのalert ruleやlibrary panelはGit Syncでは復旧できない。
managerによる保護を無条件に上書きするオプションも使わない。
元のpermissionは新しいprovisioned folderへ自動的に引き継がれると想定せず、移行前後で確認する。

退避に使う読み取りコマンドの例:

```sh
umask 077
gcx dashboards get <uid> -o json > <退避先>/resource.json
gcx dashboards get <uid> --api-version dashboard.grafana.app/v1 -o json > <退避先>/resource-v1.json
gcx dashboards list-versions <uid> > <退避先>/versions.txt
# permissionと旧versionの内容は対応する専用コマンドがなければgcx apiで取得する。
gcx api /api/dashboards/uid/<uid>/permissions > <退避先>/permissions.json
```

## 復旧

通常の更新不具合は、問題のcommitをrevertするPRをスカッシュマージして戻す。
mainをforce-pushせず、Git Syncの反映と実画面まで確認する。
Git Sync管理下の画面をUIのversion restoreや`gcx dashboards update`で戻さない。

初回移行で取り込みに失敗した場合は、次の順序で競合を解消する。

1. 対象Repositoryの同期を一時停止し、停止を確認する。Repository自体やフォルダを削除しない。
2. 対象JSONの追加をGitでrevertし、既に作成された移行先がある場合はそのUIDと管理状態を確認する。
3. Git Syncが対象を再作成しない状態で、退避した定義を元UID・元folderに復元する。別の既存UIDを上書きしない。
4. 元のpermissionと主要クエリを照合し、UIを確認する。version historyは退避ファイルで参照する。
5. Gitのrevertが反映対象になっていることを確認して同期を再開する。

この一時的な復元操作は通常の継続反映とは分け、対象UID・手順・確認結果を実行するPRへ残す。
