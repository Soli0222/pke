# GitHub の管理

GitHub のリポジトリ設定を Terraform、Actions secret の配布を `manage.py secrets` で管理する。
宣言元は `repositories.yaml` に集約する。
Terraform の通常の plan では secret の値を取得しない。
1Password は backend 認証情報の準備と、明示した secret 同期に使う。

## 構成

| ファイル | 役割 |
|---|---|
| `repositories.yaml` | 固定 ID、設定、secret 配布先、削除・管理除外、対象外リポジトリ |
| `config.py` | YAML の検証と Terraform 定義の生成 |
| `repositories.generated.tf.json` | コミットする生成物。直接編集しない |
| `versions.tf` | provider と R2 backend。version はこのファイルを正とする |
| `migrations.tf.json` | 旧アドレスの移動と旧 secret リソースの管理解除 |
| `manage.py` | 定義・import block の生成、inventory 照合、任意の plan 検査、secret 同期 |
| `setup.sh` | GitHub と R2 の認証情報の準備 |
| `tests/` | 入力検証、secret の扱い、mock provider による state 移行・ライフサイクル検証 |

YAML のキーはリポジトリの固定 ID とし、GitHub 上の名前は `name` で上書きする。
Terraform アドレスは `github_repository.repo_<ID>` で固定する。
archive・復帰・rename でも同じアドレスを使う。

Terraform の `lifecycle` は `for_each` の値によって切り替えられないため、リポジトリごとの resource block を生成する。
active では設定全体を管理し、archived では `archived` と `name` 以外の設定変更を無視する。
archive 時の default branch は `destroy = false` で管理を外す。
secret は保持する。

## 準備と通常の操作

Python 3.10 以降、Terraform、GitHub CLI、1Password CLI、jq を使う。
GitHub owner は個人アカウントを対象とする。
`inventory` と既存リポジトリの一括 import は、private repository も列挙できるよう owner 本人の GitHub CLI 認証を要求する。

```bash
cd terraform/github
python3 -m pip install -r requirements.txt
source ./setup.sh
terraform init
```

`setup.sh` は既存の環境変数がそろっていれば再取得しない。
不足時は `gh auth token` と1Password item `terraform kkg-pve` を使う。
認証に失敗した場合はエラー終了し、途中まで取得した値を export しない。

通常の操作には `terraform plan` と `terraform apply` を使う。
YAML を編集した場合だけ、先に Terraform 定義を再生成する。

```bash
python3 manage.py generate
terraform plan
terraform apply
```

`terraform apply` が表示する plan を確認して承認する。
YAML と生成物の Git diff もあわせて確認する。
YAML を変更したまま再生成していない場合は、Terraform の precondition が plan を止める。

確認した plan を保存して適用する場合も、標準コマンドを使う。

```bash
terraform plan -out=github.tfplan
terraform apply github.tfplan
```

保存した plan の適用では追加の確認入力を求めない。
構成を編集した場合は、古い保存 plan を適用せず作り直す。

backend は R2 の `github/terraform.tfstate` を継続利用し、S3 lockfile を有効にする。
認証情報には state の読み書きに加え、`github/terraform.tfstate.tflock` の取得・作成・削除権限が必要である。
lock の取得に失敗した場合は権限と競合実行を確認し、`-lock=false` で回避しない。
Python の補助コマンド同士は checkout 内のローカルロックでも直列化する。

## リポジトリの追加・変更

新規リポジトリは固定 ID と `state: active` を追加する。
共通設定は `defaults`、個別設定は各 ID の下に置く。
`auto_init` の既定値で初期 commit を作り、default branch を管理する。
初期 commit を作らない場合は `auto_init: false` と `default_branch: null` を指定し、push 後に branch 管理を有効にする。

```yaml
repositories:
  example:
    state: active
    visibility: private
    has_wiki: false
    secret_sets: [renovate]
```

既存リポジトリを追加する場合は、通常の `terraform import` または `import` block を使う。
一括で取り込む場合は、補助コマンドで GitHub に存在する宣言対象の `import` block を生成できる。
生成先は `imports.generated.tf.json` で、内容を確認してから標準の plan/apply を実行する。
既に state にあるものは Terraform が skip する。

```bash
python3 manage.py generate
python3 manage.py imports
terraform plan
terraform apply
# import が成功したら補助ファイルを削除する。
rm imports.generated.tf.json
```

rename はキーを変えず、`name: new-name` を指定する。
archive は `state: archived`、復帰は `state: active` に変更する。
archived リポジトリの rename は、先に復帰を適用してから別の plan で行う。
archive と同時に指定した他の設定変更は無視されるため、必要な変更は archive 前に適用する。
復帰時には現在の共通設定と個別設定が再び適用対象になる。

default branch の変更は既存 branch の選択が既定である。
branch 自体も rename する場合は `rename_default_branch: true` を明示する。

## 削除と管理対象からの除外

固定 ID を `repositories` から `retired` に移す。
GitHub 上の現在の名前と、`delete` または `forget` を明示する。

```yaml
retired:
  example:
    name: example
    action: delete
```

`delete` は GitHub 上のリポジトリを実際に削除する。
`forget` は実体を残して Terraform の管理だけを外す。
plan に表示される対象アドレスとリポジトリ名を確認する。
削除を archive に置き換える `archive_on_destroy` は使わない。

`retired` の記録は削除・変更せず、ID を再利用しない。
CI は PR の base と比較し、処遇を宣言せずに ID を削除した変更を拒否する。
`prevent_destroy` は管理中のリソースの削除・置換を防ぐが、resource block 自体を消す操作は防げない。
そのため、削除時も YAML の `retired` を使い、生成物だけを手で消さない。
`forget` したリポジトリの管理を戻す場合は、新しい固定 ID を使って既存リポジトリとして import する。
`delete` の記録と同じ名前のリポジトリは管理対象へ追加できない。

所有リポジトリの照合は次のコマンドで行う。

```bash
python3 manage.py inventory
```

宣言のないリポジトリは `excluded` に理由とともに記載する。
`exclude_forks: true` は未宣言の fork を一括で対象外とする。
照合結果に未分類・不足・archive のずれ・未完了の削除があれば終了コード1を返す。

## Actions secrets

secret は `secret_sets` で定義し、各リポジトリの `secret_sets` リストで配布先を選ぶ。
リストを省略するか空にすると、共通 secret を配布しない。
個別の `actions_secrets` は同名 secret の参照元を上書きする。
値の宣言は1Password参照のみ受け付ける。

```yaml
secret_sets:
  example:
    API_TOKEN:
      onepassword:
        reference: op://Personal/example/API_TOKEN
```

参照は `reference`、`item + field`（任意の `vault`）、`vault + item + file` のいずれかを使う。
混在した指定と未知キーは検証エラーになる。

```bash
# 存在だけを確認。1Password から値を読まない。
python3 manage.py secrets --repo pke
# 指定したリポジトリへ配布・再配布する。
python3 manage.py secrets --repo pke --apply
# 全 active リポジトリを対象にする場合は明示する。
python3 manage.py secrets --all --apply
```

GitHub から secret の値は読み戻せないため、確認コマンドは値の一致を保証しない。
`--apply` は選択した secret を再送する。rotation 時も同じ操作を使う。
一意な1Password source をすべて読み終えてから書き込みを始める。
途中で GitHub への送信が失敗した場合は同じコマンドを再実行できる。

値はプロセスのメモリと標準入力だけで渡し、引数・ログ・ファイル・Terraform state に保存しない。
secret 定義の削除や secret set からの除外では、既存の GitHub secret を消さない。
削除する場合は active リポジトリの `actions_secrets` に `SECRET_NAME: null` を明示して同期する。
archived リポジトリへの同期・削除は行わない。

## 既存 state の移行

初回も標準の Terraform コマンドを使う。
既存 checkout で backend 設定の変更が検出された場合は `terraform init -reconfigure` を実行する。
state の保存先は同じなので `-migrate-state` は不要である。
`migrations.tf.json` が旧 active/archived のアドレスを固定アドレスへ移し、旧 Actions secret を `destroy = false` で管理から外す。
GitHub の secret 自体は削除しない。
旧 external data source は定義から除外され、現在の state から消える。
移行定義は未適用の state に必要なため保持する。
external provider の依存も旧 state の schema と移行テスト用に固定するが、通常の構成に external data source はない。

移行 plan では repository の移動、secret の管理解除、明示された `retired` の処理、既存の設定差分を確認する。
旧 state、バックアップ、初回の plan ファイルには平文の secret が残り得る。
現在の state から取り除いても過去のコピーまでは消えない。
保存 plan は使い終わったら削除し、過去の state コピーは保管先の保持方針に従って扱う。

## 保存 plan の追加検査（任意）

削除対象と `retired` の照合などを追加で確認したい場合は、標準コマンドで保存した plan を検査できる。

```bash
terraform plan -out=github.tfplan
python3 manage.py check-plan github.tfplan
```

このコマンドは plan を読み取るだけで、適用や state の更新は行わない。
独自の承認ファイルは作らず、`terraform apply` の前提条件にもならない。
生の plan JSON は表示せず、secret を含まない操作情報と設定差分を出力する。

## 検証

```bash
python3 manage.py generate --check
terraform fmt -check
terraform validate
python3 -m unittest discover -s tests -v
python3 tests/terraform_lifecycle.py
```

CI は backend と GitHub に接続せず、生成物の一致、YAML の検証、ID の削除方針、認証失敗時の停止、secret の取り扱いを確認する。
Terraform の mock provider では旧 state の移行、archive・復帰・rename、削除・管理解除を検証する。
本番の権限、R2 lock の取得、GitHub API の設定制約は実際の plan/apply で確認する。

GitHub provider は新しい REST client とローカル cache を使う。
速度の比較は同じ構成・認証条件で plan の所要時間を測る。
初回の移行・import と、移行後の通常 plan は分けて評価する。
