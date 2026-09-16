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
`group_by` は `alertname`、`severity`、`pke_cluster` とし、クラスタや重要度が違う通知を分離する。

| `pke_cluster` | ntfy topic | Slack |
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
購読専用ユーザーは未作成だったため、`alerts-reader` を追加する。
実際の変更前にも `kubectl --context natsume@soli -n ntfy exec deployment/ntfy -- ntfy user list` でユーザー名と ACL を再確認する。

Kubernetes vault の次のフィールドを準備する。
値は 1Password の編集画面で扱い、Issue、PR、シェル履歴には記録しない。

| Item | Field | 内容 |
| --- | --- | --- |
| `ntfy-admin`（既存） | `auth-users`（既存） | 現在の値を全文保持し、カンマ区切りで `alertmanager:<bcrypt hash>:user` と `alerts-reader:<bcrypt hash>:user` を追加 |
| `ntfy-admin`（既存） | `auth-tokens`（追加） | `alertmanager:<publish token>`。既存の宣言的 token があれば全文保持して追加。label は省略可 |
| `ntfy-alertmanager`（新規） | `token` | 上記 publish token 単体。ユーザー名や label、`Bearer ` は含めない |

`ntfy user hash` でそれぞれ異なる強いパスワードをハッシュ化し、`ntfy token generate` で publish token を生成する。
どちらも ntfy v2.28.0 のローカル CLI で実行し、生成値は直接 1Password に保存する。
環境変数の値はカンマ区切りの1行で、bcrypt の `$` を二重化しない。
`auth-users` に既存 `soli:<hash>:admin` を残し、既存の他ユーザーが増えていればそれらも保持する。
[宣言的 provisioning](https://github.com/binwiederhier/ntfy/blob/v2.28.0/docs/config.md#users-via-the-config) では、以前宣言したユーザーを一覧から除くと次回起動時に削除される。

publish token の権限は `alertmanager` ユーザーの ACL によって制限する。
role は `user` とし、3 topic への write-only だけを許可する。
`alerts-reader` は同じ3 topic の read-only、既存 admin は全 topic の管理権限を維持する。
Alloy namespace には token 単体の item だけを同期し、admin の hash や購読ユーザーの資格情報を渡さない。

### 反映順序と Secret 更新

1Password を準備してから PR を merge する。
既存 `ntfy-admin` の編集は、現行設定でも auto-restart を起こし得るため、本番作業として行う。
Alloy の設定反映が先行すると ntfy の準備前に webhook が失敗するため、承認後の適用では次の順序を使う。

1. `flux suspend kustomization alloy --context natsume@soli -n flux-system` で Alloy の同期を一時停止する。
2. 上記 item と field を準備する。既存 Secret は値を表示せずキーだけを確認する。例：`kubectl --context natsume@soli -n ntfy get secret ntfy-admin -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'`。
3. PR を merge し、ntfy の Flux 同期と `kubectl --context natsume@soli -n ntfy rollout status deployment/ntfy --timeout=5m` を確認する。HelmRelease の observedGeneration と generation の一致、Ready、新 Pod の起動時刻を確認する。
4. `ntfy user list` で既存 admin、新規ユーザー、3 topic の ACL を確認する。購読アプリに `alerts-reader` を設定する。
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
ntfy v2.28.0 に対しては3 topic の発火と解消の整形、publish token の購読拒否、対象外 topic の送信拒否、購読ユーザーの書き込み拒否、無効 token、token 更新後の旧 token 拒否、admin と購読ユーザーの継続利用を確認する。
render に実 token は渡さず、資格情報が `secretKeyRef` のままであることも確認する。
この検証は実機の Slack 配信や 1Password Operator の動作を証明するものではない。

### 本番の配信確認

本番テストの承認後、「Slack までの発火と解消の検証」の短命な PrometheusRule を使う。
各ケースで異なる test ID を使い、`labels` に次の値を追加して順番に実行する。
`severity: info` は維持する。

| ケース | 追加ラベル | 期待する topic |
| --- | --- | --- |
| natsume | `pke_cluster: natsume` | `natsume-alerts` |
| meruto | `pke_cluster: meruto` | `meruto-alerts` |
| 共通 | `pke_cluster: pke` | `pke-alerts` |
| 未知 | `pke_cluster: unknown` | `pke-alerts` |
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
| alerts-reader で3 topic を GET / POST | 200 / 403 |
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
最後に旧 token だけを `auth-tokens` から外して ntfy を再起動し、旧 token が401になることと admin/reader の継続利用を確認する。
`auth-users`、既存 token、他ユーザーの ACL は維持する。
旧 token が漏洩している場合は併存させず失効を優先し、その間の ntfy 未達を記録する。

Slack-only に戻す場合は `alloy-config.yaml` の root receiver `slack_webhook` と既存 `slack_configs` を保持したまま、追加した `route.routes` 全体、3つの `ntfy_*` receiver、`remote.kubernetes.secret "ntfy_publish"` を削除する PR を作る。
`group_by` はそのままでよい。
merge 後に Alloy から Mimir への同期成功と Slack 通知を確認し、それから不要な token を失効させる。
ntfy のユーザー一覧を過去の値で丸ごと上書きせず、admin と購読者を保持する。
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
