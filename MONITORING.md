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
