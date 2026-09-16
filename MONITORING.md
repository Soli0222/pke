# 監視基盤の運用

## natsume の Ruler 通知先

Mimir 3.2.1 の Ruler は、次の設定で同じ Mimir 内の Alertmanager に通知する。
`multitenancy_enabled: false` のため tenant は `anonymous` となる。

```yaml
limits:
  ruler_alertmanager_client_config:
    alertmanager_url: http://mimir.mimir:8080/alertmanager
```

設定は `flux/clusters/natsume/apps/mimir/helmrelease-mimir.yaml` で管理する。
フィールドは Mimir 3.2.1 の [Limits 定義](https://github.com/grafana/mimir/blob/mimir-3.2.1/pkg/util/validation/limits.go)と [Alertmanager client 定義](https://github.com/grafana/mimir/blob/mimir-3.2.1/pkg/ruler/notifier/notifier_config.go)に対応する。
Alloy の `mimir.rules.kubernetes` が PrometheusRule を同期し、Ruler が評価と通知を行う。
`mimir.alerts.kubernetes` は Alertmanager の通知設定を同期する。
既存の通知先は Slack の `#alert` で、`send_resolved: true` である。
meruto の設定と共通 chart は変更しない。

## 静的検証

リポジトリのルートで実行する。
Python 3 と PyYAML、Helm、Docker が必要となる。
一時ファイルにはリポジトリの設定だけを使い、S3 認証情報は検証用のダミー値とする。
Mimir 3.2.1 に `-verify-config` はないため、`-modules` を使う。
[起動処理](https://github.com/grafana/mimir/blob/mimir-3.2.1/cmd/mimir/main.go)は設定をロードして `Validate` を実行した後、サービスを起動せずモジュール一覧を出力して終了する。

```sh
validation_dir=$(mktemp -d)
export validation_dir
python3 - <<'PY'
import os
from pathlib import Path
import subprocess
import yaml

out = Path(os.environ['validation_dir'])
hr = yaml.safe_load(Path('flux/clusters/natsume/apps/mimir/helmrelease-mimir.yaml').read_text())
values = out / 'values.yaml'
values.write_text(yaml.safe_dump(hr['spec']['values']))
subprocess.run(['helm', 'lint', 'charts/mimir', '-f', str(values)], check=True)
rendered = subprocess.check_output(
    ['helm', 'template', 'mimir', 'charts/mimir', '-n', 'mimir', '-f', str(values)], text=True)
(out / 'rendered.yaml').write_text(rendered)
config = next(d for d in yaml.safe_load_all(rendered) if d and d['kind'] == 'ConfigMap')
(out / 'mimir.yaml').write_text(config['data']['mimir.yaml'])
PY
docker run --rm --network none \
  -v "$validation_dir/mimir.yaml:/etc/mimir/mimir.yaml:ro" \
  -e S3_ENDPOINT=s3.example.com \
  -e S3_ACCESS_KEY_ID=validation -e S3_SECRET_ACCESS_KEY=validation \
  grafana/mimir:3.2.1 \
  -config.file=/etc/mimir/mimir.yaml -config.expand-env=true -modules
rm -r "$validation_dir"
```

## 適用と状態確認

PR の merge と本番への反映が承認された後、Flux の通常同期で反映する。
HelmRelease の values 変更は ConfigMap の checksum を変えるため、Mimir の単一 Pod が再起動する。
この間は評価と通知が一時的に止まる。

```sh
kubectl --context natsume@soli -n mimir wait helmrelease/mimir \
  --for=condition=Ready --timeout=5m
kubectl --context natsume@soli -n mimir rollout status statefulset/mimir --timeout=5m
kubectl --context natsume@soli -n mimir get pod mimir-0 \
  -o jsonpath='{.spec.containers[0].image}'
```

Ready だけで変更反映を判定せず、HelmRelease の `status.observedGeneration` と `metadata.generation` の一致も確認する。
別ターミナルで port-forward を維持する。

```sh
kubectl --context natsume@soli -n mimir port-forward svc/mimir 18080:8080
```

以降はこの接続先を使う。
`/config` や Alertmanager の設定全体には認証情報が含まれうるため、Issue や PR に貼らない。

```sh
curl -fsS -H 'X-Scope-OrgID: anonymous' \
  http://127.0.0.1:18080/prometheus/api/v1/rules |
  jq '[.data.groups[] | {name, rules: [.rules[] | {name, health, lastError, state}]}]'
curl -fsS http://127.0.0.1:18080/metrics |
  rg '^cortex_(prometheus_(notifications_(alertmanagers_discovered|sent_total|errors_total|dropped_total)|rule_evaluations_total|rule_evaluation_failures_total)|alertmanager_notifications(_failed)?_total)\{'
```

`cortex_prometheus_notifications_alertmanagers_discovered{user="anonymous"}` が 1 以上であることを確認する。
評価 API の `health` は `ok`、`lastError` は空であることを確認する。
テスト前後のメトリクスを比較し、Ruler の評価回数と通知送信数、Alertmanager の Slack 通知数が増えることを確認する。
評価失敗、通知エラー、通知 drop、Slack 通知失敗の増加があればログで原因を調べる。
通知前は関連するカウンターが存在しない場合があるため、系列がないことを成功と判定しない。
Pod 再起動時はカウンターがリセットされるので比較区間を取り直す。

## Slack までの発火と解消の検証

本番テストの実施が承認された後に実行する。
このテストは既存の `#alert` に通知する。
既存ワークロードの停止は不要で、Alertmanager API への直接 POST は使わない。

実行ごとに異なる alertname を使い、通知の grouping と過去のテストを区別する。
以下を同じシェルで実行する。

```sh
test_run=$(date -u +%Y%m%dT%H%M%SZ)
test_rule="pke-notification-e2e-$(printf '%s' "$test_run" | tr '[:upper:]' '[:lower:]')"
test_alert="PKEFullPathE2E${test_run}"
test_end=$(($(date +%s) + 1200))
test_manifest=$(mktemp)
cat > "$test_manifest" <<EOF_RULE
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: ${test_rule}
  namespace: alloy
spec:
  groups:
    - name: ${test_rule}
      interval: 15s
      rules:
        - alert: ${test_alert}
          expr: vector(time()) < ${test_end}
          for: 30s
          labels:
            severity: info
          annotations:
            summary: 'PKE 通知経路テスト ${test_run}（意図したテスト通知）'
EOF_RULE
kubectl --context natsume@soli create -f "$test_manifest"
```

式は作成から20分後に空ベクトルとなり、自動的に解消する。
同期が遅れて期限内に発火しなかった場合は、回収後に新しい ID でやり直す。
同期待ちの間も、次の API で対象ルールが現れ、`pending` から `firing` に変わることを確認する。

```sh
curl -fsS -H 'X-Scope-OrgID: anonymous' \
  http://127.0.0.1:18080/prometheus/api/v1/rules |
  jq --arg name "$test_alert" \
    '[.data.groups[].rules[] | select(.name == $name) | {name, state, health, lastError, alerts}]'
curl -fsSG -H 'X-Scope-OrgID: anonymous' \
  http://127.0.0.1:18080/alertmanager/api/v2/alerts \
  --data-urlencode "filter=alertname=\"$test_alert\"" |
  jq '[.[] | {labels, status, startsAt, endsAt}]'
```

Alertmanager で対象アラートが active となり、Slack に同じ ID の発火通知が届いた時刻とメッセージリンクを記録する。
既存設定の `group_wait` は30秒、`group_interval` は5分であり、ルール同期や評価にも時間がかかる。
発火通知を確認したら、期限を待つか、同じルールの式だけを空ベクトルに変えて解消を促す。
この段階ではルールを削除しない。

```sh
kubectl --context natsume@soli -n alloy patch prometheusrule "$test_rule" \
  --type=json \
  -p='[{"op":"replace","path":"/spec/groups/0/rules/0/expr","value":"vector(0) > 1"}]'
```

Ruler が `inactive` になり、Alertmanager の active 一覧から消え、Slack に同じ ID の解消通知が届くことを確認する。
既存 Slack テンプレートはタイトルに状態を含めないため、本文の ID と通知時刻に加え、通知の色および Ruler / Alertmanager の状態で発火と解消を照合する。
解消通知は次の group interval まで待つ。
両方の通知のリンクを記録してから回収する。

```sh
kubectl --context natsume@soli -n alloy delete prometheusrule "$test_rule" --ignore-not-found
kubectl --context natsume@soli -n alloy get prometheusrule "$test_rule" --ignore-not-found
rm -f "$test_manifest"
```

Alloy の次の同期後に、Ruler API の対象ルールの検索結果も `[]` となることを確認する。
port-forward は Ctrl-C で終了する。
途中で失敗した場合も同じ回収を実施し、通知未達または解消未確認として記録する。
シェルを失った場合は `alloy` namespace の `pke-notification-e2e-` で始まる PrometheusRule を確認し、この実行のリソースだけを削除する。

## Rollback

テストルールを回収してから、この変更の commit を revert する PR を作成する。
`limits.ruler_alertmanager_client_config` の追加した2行だけを取り除き、他の limits は維持する。
承認後に merge し、Flux の同期と Mimir の rollout を確認する。
ConfigMap の直接編集は Flux に上書きされるため使用しない。
旧設定では通知先が未設定に戻るため、通知の復旧を意味しない。

## Issue #733 の検証記録

2026-09-16 に修正前の実機を読み取り確認した。
イメージは `grafana/mimir:3.2.1`、anonymous tenant の discovered Alertmanager 数は `0` だった。
取得した既存ルールの評価失敗カウンターはすべて `0` だった。

本番反映とテスト通知は未実施。
Issue は次の記録が揃ってから閉じる。

| 確認項目 | 結果 |
| --- | --- |
| Helm lint / render | 成功 |
| Mimir 3.2.1 の設定検証 | `-modules` による設定ロードと Validate が成功 |
| 反映 commit / 実施日時 | 未実施 |
| 反映後の discovered 数 | 未確認 |
| テスト ID / Ruler 評価 / 送信エラー増分 | 未確認 |
| Slack 発火 / 解消の時刻とリンク | 未確認 |
| PrometheusRule / Ruler 同期先の回収 | テスト未実施 |

## ntfy と Slack への並行通知

natsume の Alloy が Mimir Alertmanager に同期する設定で、すべてのアラートを Slack `#alert` と ntfy に送る。
最初の子 route は条件なしで Slack に送り、`continue: true` によって次の ntfy route も評価する。
ntfy route は次の topic を選択する。
`group_by` は `alertname`、`severity`、`cluster` とし、クラスタや重要度が違う通知を分離する。

| `cluster` | ntfy topic | Slack |
| --- | --- | --- |
| `natsume` | `natsume-alerts` | `#alert` |
| `meruto` | `meruto-alerts` | `#alert` |
| その他（`pke` を含む）、空文字、欠落 | `pke-alerts` | `#alert` |

URL は `https://ntfy.pstr.space/<topic>?template=alertmanager` とする。
[ntfy v2.28.0 の組み込み template](https://github.com/binwiederhier/ntfy/blob/v2.28.0/docs/publish.md#pre-defined-templates) が発火と解消の webhook を整形する。
全 receiver の `send_resolved` は `true` を維持する。
meruto の manifest と共通 chart は変更しない。
クラスタラベルを付ける作業は別 Issue の範囲であり、それまでの通知は共通 topic で受ける。

### 1Password の準備

2026-09-16 の読み取り確認では、実機 ntfy は v2.28.0、ユーザーは既存 admin の `soli` のみで、anonymous は deny-all だった。
購読には既存の `soli` を使い、送信専用の `alertmanager` だけを追加する。
実際の変更前にも `kubectl --context natsume@soli -n ntfy exec deployment/ntfy -- ntfy user list` でユーザー名と ACL を再確認する。

Kubernetes vault の次のフィールドを準備する。
値は 1Password の編集画面で扱い、Issue、PR、シェル履歴には記録しない。

| Item | Field | 内容 |
| --- | --- | --- |
| `ntfy-admin`（既存） | `auth-users`（既存） | 現在の値を全文保持し、カンマ区切りで `alertmanager:<bcrypt hash>:user` を追加 |
| `ntfy-admin`（既存） | `auth-tokens`（追加） | `alertmanager:<publish token>`。既存の宣言的 token があれば全文保持して追加。label は省略可 |
| `ntfy-alertmanager`（新規） | `token` | 上記 publish token 単体。ユーザー名や label、`Bearer ` は含めない |

`ntfy user hash` で送信専用ユーザーの強いパスワードをハッシュ化し、`ntfy token generate` で publish token を生成する。
どちらも ntfy v2.28.0 のローカル CLI で実行し、生成値は直接 1Password に保存する。
環境変数の値はカンマ区切りの1行で、bcrypt の `$` を二重化しない。
`auth-users` に既存 `soli:<hash>:admin` を残し、既存の他ユーザーが増えていればそれらも保持する。
[宣言的 provisioning](https://github.com/binwiederhier/ntfy/blob/v2.28.0/docs/config.md#users-via-the-config) では、以前宣言したユーザーを一覧から除くと次回起動時に削除される。

publish token の権限は `alertmanager` ユーザーの ACL によって制限する。
role は `user` とし、3 topic への write-only だけを許可する。
既存 admin は全 topic の管理権限を維持し、購読にも使う。
Alloy namespace には token 単体の item だけを同期し、admin の hash や購読ユーザーの資格情報を渡さない。

### 反映順序と Secret 更新

1Password を準備してから PR を merge する。
既存 `ntfy-admin` の編集は、現行設定でも auto-restart を起こし得るため、本番作業として行う。
Alloy の設定反映が先行すると ntfy の準備前に webhook が失敗するため、承認後の適用では次の順序を使う。

1. `flux suspend kustomization alloy --context natsume@soli -n flux-system` で Alloy の同期を一時停止する。
2. 上記 item と field を準備する。既存 Secret は値を表示せずキーだけを確認する。例：`kubectl --context natsume@soli -n ntfy get secret ntfy-admin -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'`。
3. PR を merge し、ntfy の Flux 同期と `kubectl --context natsume@soli -n ntfy rollout status deployment/ntfy --timeout=5m` を確認する。HelmRelease の observedGeneration と generation の一致、Ready、新 Pod の起動時刻を確認する。
4. `ntfy user list` で既存 admin、新規ユーザー、3 topic の ACL を確認する。購読アプリには既存 `soli` を使う。
5. `kubectl --context natsume@soli apply -f flux/clusters/natsume/apps/alloy/onepassworditem.yaml` で Git と同じ OnePasswordItem を先行作成し、`alloy/ntfy-alertmanager` Secret の `token` キーが存在することを値を出さずに確認する。
6. `flux resume kustomization alloy --context natsume@soli -n flux-system` で同期を再開する。Alloy の `remote.kubernetes.secret.ntfy_publish` と `mimir.alerts.kubernetes.default` の health、Mimir への設定同期成功を確認する。失敗時は後述の Slack-only rollback を行い、同期停止を放置しない。

[1Password Operator の auto-restart](https://github.com/1Password/onepassword-operator/blob/main/USAGEGUIDE.md#configuring-automatic-rolling-restarts-of-deployments) は Secret を参照する Deployment に適用される。
`ntfy-admin` OnePasswordItem の既存 annotation は保持し、ntfy Deployment の `secretKeyRef` による環境変数を再読み込みさせる。
更新後に Pod が置き換わらなければ、`kubectl --context natsume@soli -n ntfy rollout restart deployment/ntfy` と rollout status で反映する。
Secret 値を Helm lookup や checksum に展開しない。

Alloy は Secret を Pod の環境変数や volume で参照せず、[remote.kubernetes.secret](https://grafana.com/docs/alloy/latest/reference/components/remote/remote.kubernetes.secret/) が API から1分ごとに取得する。
そのため Secret 更新の反映に auto-restart は使わず、Alloy が設定を再評価して Mimir に同期するのを待つ。
既存 Slack Secret も同じ方式である。
Secret 不在、キー欠落、RBAC エラーは component health と同期エラーで確認する。
Mimir に以前の設定が残る場合があるため、Pod Ready だけでは反映成功と判定しない。
Alloy の UI やログ、Mimir の設定取得結果には資格情報が含まれ得るので、そのまま共有しない。

### 実値を使わない検証

Python 3、PyYAML、Helm、kubectl、Docker を使い、リポジトリのルートで実行する。
スクリプトは Kubernetes API と 1Password を呼ばず、公式イメージとローカルのダミー資格情報を使う。
ntfy は localhost の一時ポートだけで公開し、テスト後にコンテナと DB を回収する。

```sh
python3 scripts/validate-ntfy-alerts.py
```

Helm lint/render、両クラスタの kustomize build、Alloy v1.19.2 の validate、amtool v0.31.1 の設定チェックと6通りの route test を行う。
ntfy v2.28.0 に対しては3 topic の発火と解消の整形、publish token の購読拒否、対象外 topic の送信拒否、無効 token、token 更新後の旧 token 拒否、admin の継続利用を確認する。
render に実 token は渡さず、資格情報が `secretKeyRef` のままであることも確認する。
この検証は実機の Slack 配信や 1Password Operator の動作を証明するものではない。

### 本番の配信確認

本番テストの承認後、「Slack までの発火と解消の検証」の短命な PrometheusRule を使う。
各ケースで異なる test ID を使い、`labels` に次の値を追加して順番に実行する。
`severity: info` は維持する。

| ケース | 追加ラベル | 期待する topic |
| --- | --- | --- |
| natsume | `cluster: natsume` | `natsume-alerts` |
| meruto | `cluster: meruto` | `meruto-alerts` |
| 共通 | `cluster: pke` | `pke-alerts` |
| 未知 | `cluster: unknown` | `pke-alerts` |
| 欠落 | 追加しない | `pke-alerts` |

同じ ID の発火と解消が Slack と期待した ntfy topic に届き、他の topic には届かないことを記録する。
ntfy では JSON 原文ではなく、タイトル、本文の状態と summary が表示されることを確認する。
解消確認前にルールを消さず、配信確認後に PrometheusRule と Ruler 同期先からの削除まで確認する。
`cortex_alertmanager_notifications_total` と `cortex_alertmanager_notifications_failed_total` を Slack と webhook それぞれで比較する。
ルートごとの時刻、Slack リンク、ntfy message ID、テストリソースの回収結果を Issue #734 に残す。

実機 ACL は認証済み HTTP クライアントで次の表を確認する。
資格情報は 1Password からクライアントへ渡し、URL、コマンド引数、verbose 出力に token を含めない。
送信テストは購読者へ届くため、意図したテスト本文と ID を付ける。

| 資格情報と操作 | 期待値 |
| --- | --- |
| publish token で各3 topic に POST | 200 |
| publish token で各3 topic の `/json?poll=1` を GET | 403 |
| publish token で対象外 topic に POST | 403 |
| 既存 admin で既存 topic を読み書き | 継続利用可能 |
| 無効 token で POST | 401 |

認証失敗時は ntfy が要求を拒否し、Alertmanager の webhook 通知失敗として観測される。
Slack は別 receiver なので配信を続けるが、401/403 の通知は恒久的な失敗として扱われ得るため、資格情報を直しただけで失敗分が再送されるとは限らない。
新しい test ID の発火と解消で復旧を確認する。
本番 token を意図的に壊す試験は通常手順に含めず、ローカルの無効 token テストを使う。

### Token rotation と Slack-only rollback

通常の rotation は旧 token と新 token の併存期間を設ける。
`ntfy-admin.auth-tokens` に同じユーザーの新 token を追加し、ntfy の再起動と新 token の受付を確認する。
次に `ntfy-alertmanager.token` を新 token に置き換え、Secret 更新、Alloy の再取得、Mimir 同期、新しい通知の到達を確認する。
最後に旧 token だけを `auth-tokens` から外して ntfy を再起動し、旧 token が401になることと admin の継続利用を確認する。
`auth-users`、既存 token、他ユーザーの ACL は維持する。
旧 token が漏洩している場合は併存させず失効を優先し、その間の ntfy 未達を記録する。

Slack-only に戻す場合は `alloy-config.yaml` の root receiver `slack_webhook` と既存 `slack_configs` を保持したまま、追加した `route.routes` 全体、3つの `ntfy_*` receiver、`remote.kubernetes.secret "ntfy_publish"` を削除する PR を作る。
`group_by` はそのままでよい。
merge 後に Alloy から Mimir への同期成功と Slack 通知を確認し、それから不要な token を失効させる。
ntfy のユーザー一覧を過去の値で丸ごと上書きせず、既存 admin を保持する。
#733 の Ruler 通知先設定は削除しない。

### Issue #734 の検証記録

| 確認項目 | 結果 |
| --- | --- |
| #733 の反映 | PR #754 merge 済み。Issue の記録では discovered=1、Slack 発火到達。解消到達と Ruler からの回収は未確認のままユーザー判断で終了 |
| 実機の読み取り確認 | ntfy v2.28.0、Alloy v1.19.2、ntfy は admin soli のみ |
| ローカル静的検証と ntfy テスト | `scripts/validate-ntfy-alerts.py` で検証。結果は PR に記録 |
| 1Password の実値準備と本番反映 | 未実施 |
| 本番の3 topic、unknown、missing の発火と解消、Slack 並行配信 | 未実施 |
| 本番 ACL、Secret 更新時の再起動と再取得、rotation | 未実施 |

本番未確認項目を完了扱いにせず、実施後にこの表と Issue を更新する。

### 1Password item の整理

運用で参照する item は `ntfy-admin` と `ntfy-alertmanager` の2つとする。
`ntfy-admin.auth-users` に既存 admin と送信ユーザーの hash、`auth-tokens` に送信 token の宣言を保持する。
`ntfy-alertmanager.token` には同じ送信 token を保存し、Alloy に同期する。
この2か所の token は送信側と受信側で必要なため削除しない。

購読専用の `alerts-reader` は使わないため、ACL を GitOps で削除した後、`auth-users` からそのユーザーだけを除いて再起動する。
起動後に既存 admin と送信ユーザーの権限を確認し、保管用の `ntfy-alerts-reader` と `ntfy-alertmanager-credentials` は削除する。
送信ユーザーの bcrypt hash は `ntfy-admin` に残るので、保管用 item の削除で送信ユーザーは消えない。

既存 item の更新は Operator の600秒ポーリングを待つ場合がある。
Secret のフィールド更新を値非表示で確認してから rollout する。
必要なら OnePasswordItem の metadata annotation を更新して再処理を促す。

## クラスタラベルの契約（#735）

Kubernetes クラスタ名は `cluster`、CNPG の DB クラスタ名は `cnpg_cluster` に分ける。
#735 の当初案だった `pke_cluster` の追加は採用しない。
後続 #736 以降の matcher、recording rule、通知ラベルも `cluster` を使う。

両クラスタの Alloy は、すべてのメトリクスを `prometheus.relabel.cluster_labels` に通す。
`cnpg_*` に元の `cluster` があれば `cnpg_cluster` にコピーし、その後 `cluster` を収集元の `natsume` / `meruto` で上書きする。
入力の同名ラベルより収集設定を優先する。
この変換は remote_write の external_labels による補完より前に行うため、元から DB 名を持たないメトリクスに Kubernetes クラスタ名を DB 名としてコピーしない。
[Alloy の relabel](https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.relabel/) の rule は記述順に適用される。

natsume の5つの DB PodMonitor は、Pod の `cnpg.io/cluster` を target relabeling で `cnpg_cluster` にコピーする。
これにより、元から `cluster` を持たない PostgreSQL メトリクスや `up` にも DB 名が付く。
meruto の DB PodMonitor は現在存在せず、追加は #740 で同じ relabeling を指定する。
ホスト Alloy は既存の共通 relabel で inventory の `cluster` を上書き設定しているため、変更しない。

| 収集元 | 共通経路 | 代表メトリクス |
| --- | --- | --- |
| 両クラスタの Kubernetes API / kubelet / cAdvisor | cluster_labels → remote_write.default | `apiserver_request_total`, `kubelet_running_pods`, `container_cpu_usage_seconds_total` |
| ServiceMonitor / PodMonitor / Probe | 同上 | `kube_node_info`, `cnpg_collector_up`, `probe_success` |
| Kubernetes Alloy 自己監視 | 同上 | `alloy_build_info` |
| OTLP → Prometheus exporter | 同上 | アプリが送信するメトリクス |
| ホスト node / system cAdvisor / Falco | add_common_labels → remote_write.mimir | `node_uname_info`, `container_cpu_usage_seconds_total`, `falcosecurity_*` |
| etcd（natsume-03 / meruto-01） | 同上 | `etcd_server_has_leader` |
| ホスト Alloy 自己監視 | 現在収集なし。#739 で add_common_labels に接続 | 追加後に確認 |

### 検証と段階的な反映

Python の PyYAML / Jinja2、kubectl、Docker がある環境で次を実行する。
両クラスタの kustomize build、Alloy v1.19.2 による設定検証、3ホストの template render（Falco 有効/無効、対象ホストの etcd）を行う。
使い捨ての Alloy と Prometheus への実送信で、DB 名の保持と欠落/空/誤った cluster の補正を検証し、コンテナを回収する。
通知側の検証は既存スクリプトで行う。

```sh
python3 scripts/validate-cluster-labels.py
python3 scripts/validate-ntfy-alerts.py
```

本番への反映は承認後に行う。
先に natsume の DB PodMonitor を反映し、次に meruto の Alloy、最後に natsume の Alloy と Alertmanager の cluster matcher を反映する。
段階を厳密に分ける場合は merge 前に対象 Flux Kustomization の同期を一時停止し、対象を一つずつ再開する。
通常の自動同期では両クラスタや別アプリの適用順は保証されない。
PodMonitor より先に Alloy が反映されても元の cluster を持つ CNPG 系列の DB 名は保持されるが、その他の DB 系列には PodMonitor 反映まで cnpg_cluster が付かない。
各段階で設定 reload 成功と remote_write の滞留/失敗を確認し、送信元ごとの反映時刻を記録する。
ホストへの Ansible 適用は不要である。

Mimir の port-forward を使い、次の式を `/prometheus/api/v1/query` で確認する。
上の表にある各代表メトリクスについて `count by (cluster, job, instance) (<metric>)` を調べる。
CNPG は次の式を確認し、`cluster="natsume"` と DB ごとの `cnpg_cluster` が共存することを確認する。

```promql
count by (cluster, cnpg_cluster, namespace) (cnpg_collector_up)
count by (cluster, cnpg_cluster, namespace) (cnpg_pg_replication_streaming_replicas)
count by (cluster, cnpg_cluster, namespace) (up{cnpg_cluster!=""})
```

DB 名で絞る既存クエリは `cluster="misskey-cluster"` から `cnpg_cluster="misskey-cluster"` に変更し、必要なら `cluster="natsume"` を併記する。
DB 単位の `by (cluster)` や vector matching も `cnpg_cluster` に変更する。
外部から取り込む CNPG ダッシュボードでは変数の label_values とパネル式の両方を確認する。
予約した `cluster` に別の意味を持たせる OTLP アプリも同様に固有のラベル名へ移行する。

### 履歴と rollback

ラベルが変わる CNPG 系列は新しい時系列になる。
過去のデータは変更されず、旧系列と新系列が retention 期間中は併存し、切替直後の lookback では二重に集計される場合がある。
従来から正しい cluster を持ち、他のラベルも変わらない系列は新系列にならない。
旧 DB 名を指定したクエリは切替後の新データを返さなくなるため、ダッシュボード側も同時に移行する。

後続ルールは各送信元の反映完了時刻から、その式の最長 range と for 期間を満たす連続履歴が蓄積してから有効化する。
たとえば `[6h]` を使う予測では6時間以上、`[24h]` なら24時間以上を確保し、欠測があれば待ち直す。
履歴不足を正常と判定せず、#736 以降で実際の式ごとに必要時間を決める。

rollback はこの PR の Alloy、PodMonitor、通知 matcher の変更を合わせて revert し、移行した外部クエリも戻す。
反映後に旧ラベルでの収集と通知先を確認する。
新ラベルの履歴は残り、rollback 前後をまたぐ集計には同じ注意が必要である。

### #735 の確認記録

2026-09-16 の読み取り確認では、両クラスタの Alloy は v1.19.2、natsume の `cnpg_collector_up` は5つの DB 名を `cluster` に持っていた。
`cnpg_pg_replication_streaming_replicas` は `cluster="natsume"` の5系列であり、同じ CNPG 内でも意味が異なっていた。
Grafana DB の保存済み dashboard は0件で、リポジトリ内にも移行対象の CNPG クエリは見つからなかった。
meruto の PodMonitor は CNPG operator 用のみだった。
本番反映、反映後の各送信元のラベル確認、履歴蓄積は未実施であり、結果を #735 に記録してから閉じる。
