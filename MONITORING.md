# 監視基盤の運用

2026-09-17 のユーザー指定により、本番の合成通知テストは今後実施しない。
通知検証を省略した旨を記録し、各 Issue のほかの検証を満たした時点で完了扱いとする。
以下に残る通知テスト手順は参考用であり、自動実行やクローズの必須条件にはしない。

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


## クラスタごとにルールの入力と出力を分離する（#736）

両クラスタの Alloy は同じ Mimir の `anonymous` tenant にルールを同期する。
`extra_query_matchers` で全 vector selector に `cluster` の完全一致条件を加え、`external_labels` で alert / recording rule の出力にも同じ値を付ける。
集計で元のラベルが落ちる式や `absent` にも出力ラベルが必要となる。
既存の `cluster` matcher と出力ラベルは上書きされるため、中央で複数クラスタを集計するルールはこの同期対象に入れない。
CNPG の DB 選択、`by`、`on`、`ignoring` では DB 識別に `cnpg_cluster` を使い、Kubernetes クラスタの選択には `cluster` を使う。
仕様は [Alloy の component reference](https://grafana.com/docs/alloy/latest/reference/components/mimir/mimir.rules.kubernetes/) と [v1.19.2 の変換処理](https://github.com/grafana/alloy/blob/v1.19.2/internal/component/mimir/rules/kubernetes/events.go)で確認できる。

| 設定 | natsume | meruto |
| --- | --- | --- |
| Mimir URL | `http://mimir.mimir:8080` | `https://mimir.pstr.space` |
| namespace prefix | `natsume`（#762 で旧 `alloy` から移行） | `meruto` |
| query matcher / 出力ラベル | `cluster="natsume"` | `cluster="meruto"` |
| 認証 | クラスタ内 HTTP、tenant `anonymous` | remote_write と同じ `remote.kubernetes.secret.mtls`、tenant `anonymous` |

Mimir namespace は `<prefix>/<Kubernetes namespace>/<PrometheusRule名>/<UID>` となる。
同じ名前の PrometheusRule が両クラスタにあっても衝突しない。
Alloy chart 1.12.1 の既存 ClusterRole は namespaces と prometheusrules の get / list / watch を許可しているため、追加 RBAC は不要。
chart 自体、blackbox の閾値、Alertmanager の route は変更しない。

### #736 の merge 前の確認記録（2026-09-17 JST）

両クラスタの実 Alloy は v1.19.2。ServiceAccount `alloy/alloy` の上記権限は `kubectl auth can-i --as=system:serviceaccount:alloy:alloy` で全件確認した。
Mimir の discovered Alertmanager は1、同期済みルール24本はすべて `health=ok` だった。
既存 namespace は次の3つで、prefix はすべて `alloy`。meruto prefix は存在しなかった。

| Kubernetes namespace / PrometheusRule | UID | 内訳 |
| --- | --- | --- |
| emoji-service / emoji-renderer | `63d1b106-01f0-44eb-ba5b-542e4e90eff9` | alert 3本 |
| loki / loki-loki-rules | `7392d2dd-e610-46bf-ac47-e2701af5250d` | recording 18本 |
| spotify-nowplaying / spotify-nowplaying | `0d0fedf1-330e-424f-9ff8-19f8b97f0ea7` | alert 3本 |

meruto には `blackbox-exporter-probes/blackbox-exporter-probes-blackbox-exporter`（UID `ebefe359-4a96-4dbc-b3e6-9d4e21338817`）がある。
対象は `EndpointDown`、`SSLCertExpiringSoon`、`SSLCertExpiryCritical`、`SlowResponse`、`HTTPStatusCodeError` の5本。
同期 component が未配置のため、Mimir にはまだ存在しない。
この棚卸しに CNPG のルールはなく、リポジトリにも旧 `cluster=<DB名>` を前提とする CNPG rule query は見つからなかった。
代表系列 `cnpg_collector_up` は natsume の5 DB で `cluster` / `cnpg_cluster` の共存を再確認した。

ただし、既存アプリ6本の `job="emoji-renderer"` / `job="spotify-nowplaying"` は現在の系列に一致しない。
実際の job は `emoji-renderer-metrics` / `spotify-nowplaying-metrics` であり、変更前から入力が空だった。
評価の `health=ok` は入力や通知の有効性を保証しないため、この6本を有効な監視として数えない。
アプリ固有 chart の matcher 修正は #736 に含めず、別途対応する。Loki の入力 bucket は natsume で135系列あり、既存 recording 出力には cluster がなかった。

以下はローカル検証である。#736 の同期・評価・ラベルは本番確認済みで、通知テストはユーザー指定により省略してクローズした。

```sh
uv run --with pyyaml --with jinja2 python scripts/validate-cluster-labels.py
uv run --with pyyaml python scripts/validate-rule-scoping.py
```

前者は両クラスタの Kustomize / Alloy validate と既存のラベル変換を検証する。
後者は合成 PrometheusRule を返すローカルの偽 Kubernetes API と保存用の偽 Mimir API に、実 Alloy v1.19.2 を接続する。
取得した同期後のルールを promtool v3.5.0 で評価し、同名系列の値が natsume=2、meruto=7 に分かれることを確認する。
集計、range、既存 matcher の上書き、片方にだけ系列がある `absent`、CNPG の DB 別 join / 選択、alert と recording の出力ラベルを対象とする。
本番の kubeconfig / Secret は使わない。偽 API は一時ポートで合成データだけを返し、終了時にサーバー・コンテナ・一時ファイルを回収する。

### 履歴を確認してから通常の Flux 同期で反映する

#735 でラベルが変わった系列の履歴開始は、[反映記録](https://github.com/Soli0222/pke/issues/735#issuecomment-5698389709)の 2026-09-16 22:33 JST とする。
経過時間だけで充足を判定せず、対象 selector ごとに `count_over_time` と `timestamp` を確認する。
収集間隔と range から期待サンプル数を求め、対象ごとの欠落や古い最終サンプルがあれば原因を調べる。

| 既存ルール | 最長 range | for | 反映時の確認 |
| --- | --- | --- | --- |
| natsume アプリ6本 | 5m | 5m | 対象系列の直近5分の履歴。反映後の pending を含め最低5分観測 |
| Loki recording 18本 | 1m | なし | bucket / sum / count の直近1分の履歴と出力ラベル |
| meruto blackbox 5本 | range なし | 5m / 1h | probe の最新値と継続収集。条件が続く SSL 警報は1時間経過後に評価 |

今回、欠測・予測ルールは追加しない。
後続 Issue で追加する場合は必要な range と for を個別に確認し、追加収集系列はその収集開始から履歴を数える。
Loki recording のうち出力に cluster がなかった系列も反映時から新系列になるため、その記録系列を使う後続ルールの履歴は別に確認する。
2026-09-17 の読み取りでは meruto の `probe_success` は4ターゲットそれぞれ直近1時間に120サンプルあり、SSL expiry / duration / HTTP status もそれぞれ4系列を確認した。
本番反映時にも最新の値と履歴を再確認する。

merge 後は両クラスタの `alloy` Kustomization の revision、ConfigMap の内容、Alloy の reload 成功を確認する。
通常同期では適用順を保証しないが、別 prefix を使うため両クラスタの同時反映は可能。
Mimir の port-forward はこの文書の「適用と状態確認」を使う。
反映前後で次の API を取得し、namespace の集合、保存された expr / labels、評価結果を比較する。ルール定義だけを保存し、認証情報を含む `/config` は取得しない。

```sh
curl -fsS -H 'X-Scope-OrgID: anonymous' \
  http://127.0.0.1:18080/prometheus/config/v1/rules > /tmp/pke-rules-after.yaml
curl -fsS -H 'X-Scope-OrgID: anonymous' \
  http://127.0.0.1:18080/prometheus/api/v1/rules |
  jq '[.data.groups[] | {file, name, rules: [.rules[] | {name, query, labels, health, lastError, state}]}]'
```

棚卸し以降に PrometheusRule が増減していなければ、既存3 namespace と新しい meruto 1 namespace の計4つ、alert 11本 / recording 18本となる。
#736 の反映では既存3 namespace の名前と UID を維持した。#762 の移行後は natsume prefix となり、UID は同じ。
すべての selector が所有クラスタに限定され、全 rule の labels に同じ cluster があることを確認する。
`health=ok` / `lastError` 空に加え、評価失敗と Alloy の `mimir_rules_events_failed_total` が増加しないことを確認する。
Loki の記録系列も実際に query し、集計で cluster が落ちていないことを確認する。

### #736 は通知テストを省略して完了

[反映記録](https://github.com/Soli0222/pke/issues/736#issuecomment-5714682431)に、両クラスタの同期、全29ルールの正常評価、入力 matcher と出力ラベル、Loki 記録系列のラベルを記載した。
ユーザー指定により本番の合成通知テストは省略し、Issue をクローズした。一時 PrometheusRule は作成していない。
通知到着を確認済みとは扱わず、今後の Issue も同じ方針で進める。

### rollback では meruto の同期済みルールも確認する

この項は #736 の rollback を扱う。#762 の prefix 移行を戻す場合は次節の手順を使う。
matcher / external_labels を revert すると、使用中の namespace に保存されたルールが旧式に更新される。
meruto は component の撤去だけでは同期済みルールが残る可能性がある。
meruto の一時テスト CR を先に回収し、その削除同期を確認してから設定を revert する。
component の停止を確認後、API の namespace 一覧と PrometheusRule の UID を照合し、今回作成した `meruto/blackbox-exporter-probes/blackbox-exporter-probes-blackbox-exporter/<UID>` だけを個別削除する。
削除には namespace 全体を URL encode した `/prometheus/config/v1/rules/<encoded-namespace>` への DELETE を使う。
実行前に対象を確認し、prefix 一括削除や既存 alloy namespace の削除は行わない。
rollback 後に既存24本の正常評価・通知先 discovery と、meruto ルールの二重評価がないことを確認する。

## natsume の rule namespace prefix を統一する（#762）

#736 では既存 namespace を維持したが、ユーザーの追加指定で prefix を `alloy` から `natsume` に変更する。
`cluster` matcher と出力ラベル、rule group 名、式、UID は変えない。meruto の設定も維持する。
以下の3 namespace が移行対象であり、先頭の `alloy/` だけを `natsume/` に置き換える。

```text
alloy/emoji-service/emoji-renderer/63d1b106-01f0-44eb-ba5b-542e4e90eff9
alloy/loki/loki-loki-rules/7392d2dd-e610-46bf-ac47-e2701af5250d
alloy/spotify-nowplaying/spotify-nowplaying/0d0fedf1-330e-424f-9ff8-19f8b97f0ea7
```

実 Alloy v1.19.2 を使う `scripts/validate-rule-scoping.py` で、新しい natsume / meruto prefix とクラスタ分離を検証する。
この試験は旧 alloy namespace をあらかじめ偽 API に置き、prefix を変えても旧ルールが自動削除されないことも確認する。
本番でも新旧が一時的に並び、同じルールが二重評価されるため、merge 後は新ルールの確認と旧ルールの回収を続けて行う。
新しい rule group は評価状態を引き継がない可能性がある。pending / firing の状態や `for` の経過を確認し、無中断の移行とは扱わない。
記録系列の名前・ラベルは維持するため、prefix 変更自体で別のメトリクス系列にはならない。

### 新しい3 namespace の同期・正常評価を確認してから旧3つを回収する

1. merge 前に Mimir の `/prometheus/config/v1/rules` を保存し、上記3つと meruto 1つの計4 namespace であることを確認する。natsume の PrometheusRule 一覧と UID も取り直す。対象が増減していたら一覧を更新し、削除対象を確認し直す。
2. merge 後、natsume の Alloy Kustomization の revision と reload 成功を確認する。すべての稼働 Alloy Pod で component の `mimir_namespace_prefix` が `natsume` になったことを確認する。旧 prefix を書く Pod が残っている間は回収しない。
3. Mimir の保存 API を再取得し、旧3つと対応する新3つの全 rule group を比較する。group 名、interval、rule 順序、expr、for、labels、annotations を含む内容が一致することを確認する。移行先が欠ける場合や内容が違う場合は削除しない。
4. `/prometheus/api/v1/rules` で新3 namespace の全24本が評価され、`health=ok` / `lastError` 空となったことを確認する。Loki の記録系列に新しいサンプルが届くことも確認する。
5. 旧3 namespace を1件ずつ指定し、対応する移行先を再確認してから DELETE する。削除 API は namespace 全体を percent-encode する。tenant 全体や prefix 一括の削除 API は使わない。

Alloy component の実引数は、各 Pod の12345への port-forward 後に `/api/v0/web/components/mimir.rules.kubernetes.default` で確認できる。
Mimir の API はこの文書の port-forward を使い、認証情報を含む `/config` は取得しない。
[namespace 削除 API](https://grafana.com/docs/mimir/latest/references/http-api/#delete-namespace)の実行例は次のとおり。旧3つそれぞれについて、上記の照合後に実行する。

```sh
# 一覧から照合済みの旧 namespace を1つだけ指定する。
old_namespace='alloy/emoji-service/emoji-renderer/63d1b106-01f0-44eb-ba5b-542e4e90eff9'
encoded_namespace=$(python3 -c 'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' "$old_namespace")
curl -fsS -X DELETE -H 'X-Scope-OrgID: anonymous' \
  "http://127.0.0.1:18080/prometheus/config/v1/rules/$encoded_namespace"
```

削除受付の202だけで完了とはしない。
保存 API と評価 API の両方から旧3つが消え、natsume 3つ / meruto 1つの計4 namespace、alert 11本 / recording 18本となることを確認する。
次の Alloy 同期周期（既定5分）後も旧 prefix が再生成されず、評価失敗が増えていないことを確認して #762 に記録する。
移行前の meruto の保存内容が変わらないことも比較する。通知テストは実施しない。

### rollback も新旧を照合してから不要な側を回収する

旧 namespace を削除する前なら、prefix を `alloy` に revert し、全 Alloy Pod の反映を確認する。
旧3つが正常評価されていることを確認してから、今回作成した natsume 3 namespace だけを削除する。
旧 namespace の回収後に戻す場合は、先に prefix を revert して Alloy に旧3つを再同期させる。
旧側の保存内容と正常評価を確認後、同じ手順で新側を回収する。両側を先に削除しない。

## Longhorn manager への Alloy 通信を許可する（#737）

Longhorn 1.12.1 の manager metrics は `http://<manager Pod IP>:9500/metrics` で公開される。
既存 ServiceMonitor の `app=longhorn-manager`、Service `longhorn-backend`、port `manager` はこの endpoint を指しており、変更しない。
[公式の監視構成](https://longhorn.io/docs/1.12.0/monitoring/prometheus-and-grafana-setup/)も同じ Service と port を使う。

収集を妨げていたのは、chart の [manager NetworkPolicy](https://github.com/longhorn/charts/blob/longhorn-1.12.1/charts/longhorn/templates/network-policies/manager-network-policy.yaml)だった。
既定の `networkPolicies.restrictInternalTraffic: true` により作られ、Longhorn 内部の Pod だけを許可する。
`networkPolicies.enabled: false` でも、この内部通信用の policy は生成される。
両クラスタの `apps/longhorn/networkpolicy-alloy-metrics.yaml` で、既存 policy に次の許可を加える。

- 宛先: `longhorn-system` の `app=longhorn-manager` Pod、TCP/9500。
- 送信元: `alloy` namespace **かつ** `app.kubernetes.io/name=alloy` / `app.kubernetes.io/instance=alloy` の Pod。

namespaceSelector と podSelector は同じ `from` 要素に置き、両条件を満たす Pod だけを対象とする。
9500 は manager API と metrics の共用 port であり、NetworkPolicy は HTTP path 単位の制限をしない。
既存の内部通信用 policy は維持する。ServiceMonitor、Alloy、Longhorn chart / storage の設定は変更しない。

### 変更前の証拠（2026-09-17 JST）

| クラスタ / ノード | manager endpoint | Alloy の last scrape error | manager 自身からの取得 |
| --- | --- | --- | --- |
| natsume / natsume-03 | `10.1.1.169:9500/metrics` | `context deadline exceeded`、約10秒 | 成功、volume capacity 7系列 |
| natsume / natsume-08 | `10.1.0.86:9500/metrics` | 同上 | 成功、volume capacity 1系列 |
| meruto / meruto-01 | `10.1.0.16:9500/metrics` | 同上 | 成功、volume capacity 2系列 |

Pod IP は当時の値で、再作成後は EndpointSlice を取り直す。
ServiceMonitor は各クラスタ1件、Alloy target は natsume 2件 / meruto 1件で、すべて `up=0`。
Mimir の `{__name__=~"longhorn_.*"}` は空だった。
Alloy の生成設定は HTTP、`/metrics`、1分間隔、timeout 10秒。Service / EndpointSlice の selector、port 9500、ready endpoint は一致していた。
実 NetworkPolicy の ingress は同 namespace の Longhorn 関連 Pod に限られ、Alloy を許可していなかった。Alloy 側には egress 制限がなかった。

manager コンテナから自身の Pod IP に curl すると、全3台が成功した。
`longhorn_volume_capacity_bytes` / `longhorn_volume_robustness` に node / volume / pvc / pvc_namespace、`longhorn_node_storage_capacity_bytes` に node を確認した。
ここで取得した生メトリクスには cluster がなく、Alloy の共通 relabel が Mimir への送信前に付ける。
manager は localhost:9500 で待ち受けていないため、Pod port-forward は connection refused となった。endpoint の異常とは扱わず、Pod IP で切り分けた。

### merge 後に収集の継続とラベルを確認する

両クラスタの root / Longhorn Kustomize build、送信元を AND 条件にした selector の検査、実 API の `kubectl apply --dry-run=server` は成功した。
本番への policy 反映と Mimir への到達確認は merge 後に行う。通常の Flux 同期で適用でき、Alloy / Longhorn の再起動は不要。

```sh
kubectl --context natsume@soli -n longhorn-system get networkpolicy longhorn-manager-alloy-metrics
kubectl --context meruto@soli -n longhorn-system get networkpolicy longhorn-manager-alloy-metrics
```

Flux `longhorn` Kustomization の反映 revision と Ready を確認し、Alloy の `prometheus.operator.servicemonitors.default` で該当 target の last_error が消えることを確認する。
API から調べる場合は Alloy の12345へ port-forward し、`/api/v0/web/components/prometheus.operator.servicemonitors.default` の debugInfo / targets を読む。
Mimir の query API はこの文書の「適用と状態確認」の port-forward を使う。

```promql
up{namespace="longhorn-system",job="longhorn-backend"}
min_over_time(up{namespace="longhorn-system",job="longhorn-backend"}[5m])
count_over_time(up{namespace="longhorn-system",job="longhorn-backend"}[5m])
count by (cluster, node) (longhorn_node_storage_capacity_bytes)
count by (cluster, node, volume, pvc_namespace, pvc) (longhorn_volume_capacity_bytes)
count by (cluster, node, volume, state) (longhorn_volume_robustness)
count by (cluster, instance) (up{namespace="longhorn-system",job="longhorn-backend"})
time() - timestamp(longhorn_volume_capacity_bytes)
```

反映後5分以上観測し、期待する3 target が継続して up=1、5分の最小値が1、1分間隔に見合うサンプル数であることを確認する。
node / volume の系列に正しい cluster が付き、最終サンプルが更新され続けることを確認する。
最後の count は各 instance で1となることを確認し、ServiceMonitor / Alloy target の一覧も照合して二重 scrape がないことを確かめる。
新しく届く系列の履歴開始を記録し、#747 のルールは必要な履歴がたまってから有効化する。
通知テストは行わず、収集とラベルの確認記録を #737 に残して完了扱いとする。

### rollback は今回の通信許可だけを取り除く

両クラスタの追加 NetworkPolicy と Kustomize の登録を revert し、Flux の prune で今回の policy だけが消えることを確認する。
chart が管理する既存 `longhorn-manager` policy は削除しない。
Longhorn の volume / replica / disk / StorageClass と ServiceMonitor を変更する必要はない。
rollback 後は Alloy からの収集が再び遮断されるため、欠測を確認して復旧方針を記録する。
Longhorn 指標の収集成功だけで、すべての iSCSI 障害を検出できるとは扱わない。

## Flux controller とリソース状態の収集（#738）

両クラスタの `apps/flux-monitoring/` に2件の PodMonitor を置く。
Flux Kustomization は `prometheus-operator-crd` に依存し、既存の `flux-system` namespace を使う。
Alloy の既存 PodMonitor discovery と共通 relabel を通し、Mimir へ `cluster=natsume` / `cluster=meruto` を付けて送る。

### 実機で確認した endpoint と収集対象

2026-09-17 JST の実 Deployment と各 Pod の `/metrics` で、次の構成を確認した。
両クラスタは同じ controller 構成で、image automation controller は稼働していない。

| 対象 | 実 image version | metrics port | 主なメトリクス |
| --- | --- | --- | --- |
| source-controller | v1.9.5 | `http-prom` / 8080 | `gotk_reconcile_duration_seconds_*`、`gotk_cache_events_total` |
| kustomize-controller | v1.9.5 | `http-prom` / 8080 | `gotk_reconcile_duration_seconds_*` |
| helm-controller | v1.6.4 | `http-prom` / 8080 | `gotk_reconcile_duration_seconds_*` |
| notification-controller | v1.9.4 | `http-prom` / 8080 | `gotk_event_http_request_duration_seconds_*` |
| flux-operator | v0.57.0 | `http-metrics` / 8080 | `flux_resource_info`、`flux_instance_info`、`flux_operator_info` |

すべて HTTP の `/metrics` を30秒間隔で scrape する。
controller は `app.kubernetes.io/part-of=flux`、Operator は `app.kubernetes.io/name=flux-operator` と `app.kubernetes.io/instance=flux-operator` の一致で選択する。
port 名を分けるため、Operator を controller 側で二重収集しない。
Pod label から `controller` ラベルも付ける。
既存の Flux NetworkPolicy `allow-scraping` が全 namespace から TCP/8080 を許可しているため、通信許可の追加は不要。

controller の duration には `kind` / `name` / `namespace` があり、`namespace` は監視対象リソースの namespace を表す。
controller 側は `honorLabels: true` とし、scrape 対象の `flux-system` でこの値を上書きしない。
Operator の `flux_resource_info` は対象 namespace を `exported_namespace` に持つ。
こちらの scrape ラベル `namespace=flux-system` と混同しない。

### Ready と suspend は Operator の状態ラベルで判定する

現行 controller の実 endpoint に `gotk_reconcile_condition` / `gotk_suspend_status` は存在しない。
[Flux の監視仕様](https://fluxcd.io/flux/monitoring/metrics/)に従い、処理時間は controller、リソース状態は [Flux Operator のメトリクス](https://fluxoperator.dev/docs/instance/monitoring/)から取得する。
`flux_resource_info` の値は常に1で、`ready="True|False|Unknown"` と `suspended="True|False"` が現在の状態を表す。
Ready=False のときは `ready="False"` の系列が1となる。Unknown も同様で、ほかの状態を値0で並べる形式ではない。
`type` / `status` ラベルはなく、Ready condition の status が `ready` に、reason が `reason` に入る。
系列の不在や値0を、正常・失敗の判定に使わない。

[v0.57.0 の実装](https://github.com/controlplaneio-fluxcd/flux-operator/blob/v0.57.0/internal/reporter/metrics.go)では、Ready condition がなければ `ready="Unknown"`、`spec.suspend=true` なら `suspended="True"` となる。
OCI 型 HelmRepository と Alert / Provider は例外的に `ready="True"` として扱う。
後続 #748 では対象 kind を絞り、意図した suspend と収集欠損を別に扱う。
今回の生メトリクスは natsume 117件 / meruto 64件がすべて `ready="True",suspended="False"`、値1だった。
False / Unknown / suspended=True の表現は上記実装で確認したもので、本番への障害注入や suspend による観測はしていない。

Operator は FluxReport の更新時に状態メトリクスを更新する。
現在は両クラスタとも reporting interval の上書きがなく、既定の5分間隔である。
30秒 scrape にしても状態の判明には通常最大約5分と収集・転送時間がかかる。
この PR では reporting / reconciliation interval とリリース戦略を変更しない。

### 静的検証と merge 後の確認

両クラスタの root / app の Kustomize build、実 Deployment に対する selector と named port の一致、実 API の server dry-run を確認する。
PodMonitor は各クラスタで4 controller と1 Operator を選択する想定である。
PodMonitor / ServiceMonitor と Alloy の既存 target 一覧も比較し、同じ endpoint を収集する既存経路がないことを確認する。
Pod endpoint の生メトリクスを読めたことだけでは、Alloy から Mimir までの到達確認としない。

```sh
kubectl kustomize flux/clusters/natsume/apps/flux-monitoring
kubectl kustomize flux/clusters/meruto/apps/flux-monitoring
kubectl --context natsume@soli apply --dry-run=server -k flux/clusters/natsume/apps/flux-monitoring
kubectl --context meruto@soli apply --dry-run=server -k flux/clusters/meruto/apps/flux-monitoring
```

merge 後は通常の Flux 同期で反映し、`flux-monitoring` Kustomization の revision / Ready と2件の PodMonitor を確認する。
Alloy の `prometheus.operator.podmonitors.default` で各クラスタ5 target が healthy、last_error が空であることを確認する。
controller / Operator の再起動は不要。
Mimir では次を照合する。

```promql
up{controller=~"source-controller|kustomize-controller|helm-controller|notification-controller|flux-operator"}
count by (cluster, controller, instance) (up{controller=~".+"})
count by (cluster, controller, kind, namespace) (gotk_reconcile_duration_seconds_count)
count by (cluster, controller) (gotk_event_http_request_duration_seconds_count)
count by (cluster, kind, ready, suspended) (flux_resource_info)
flux_resource_info{kind=~"Kustomization|HelmRelease|GitRepository",ready=~"False|Unknown",suspended="False"} == 1
time() - timestamp(flux_resource_info)
```

期待する10 target が up=1、各 instance の収集経路が1つで、サンプルが継続して更新されることを確認する。
duration の対象リソース名・namespace と、Operator の `name` / `exported_namespace` を実 CR に照合する。
sample timestamp は scrape 時刻であり、FluxReport の更新時刻ではない点に注意する。
通知テストは行わず、収集とラベルの確認後に結果を #738 へ記録する。

### rollback は PodMonitor を取り除く

両クラスタの `flux-monitoring` app / Kustomization と root の登録を revert する。
Flux の prune で今回の2件の PodMonitor が消え、Alloy の対応 target がなくなることを確認する。
既存の FluxInstance、controller、Operator、NetworkPolicy は維持する。
過去のメトリクスは Mimir に残るが、新しいサンプルの収集は止まる。
