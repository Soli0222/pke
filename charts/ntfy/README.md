# ntfy Helm chart

PKE から公式 `binwiederhier/ntfy` image をデプロイするためのラッパー chart です。
SQLite の message cache と auth DB、添付ファイルを同じ永続 volume に保存します。

`ntfy.config` は `tpl` で評価して `/etc/ntfy/server.yml` に配置します。
認証情報は `envFrom` などを使って Secret から渡し、平文を values に含めないでください。

natsume では 1Password item `ntfy-admin` の `auth-users` field を
`NTFY_AUTH_USERS` に割り当てます。値は
`<username>:<bcrypt password hash>:admin` 形式です。

`ntfy.templates` は拡張子を除くファイル名から template 本文への map です。
ConfigMap を `/etc/ntfy/templates` に read-only で mount し、変更時は checksum で Pod を再起動します。
本文は ntfy が評価する Go template なので Helm の `tpl` には渡しません。
`ntfy.config` の `template-dir` を同じパスに設定してください。
natsume は `alertmanager` template を使い、critical=5、warning=3、その他と resolved=2 の priority を指定します。
同名の組み込み template と URL が共通なので、適用順が前後しても従来形式で受信できます。
