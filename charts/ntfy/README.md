# ntfy Helm chart

PKE から公式 `binwiederhier/ntfy` image をデプロイするためのラッパー chart です。
SQLite の message cache と auth DB、添付ファイルを同じ永続 volume に保存します。

`ntfy.config` は `tpl` で評価して `/etc/ntfy/server.yml` に配置します。
認証情報は `envFrom` などを使って Secret から渡し、平文を values に含めないでください。

natsume では 1Password item `ntfy-admin` の `auth-users` field を
`NTFY_AUTH_USERS` に割り当てます。値は
`<username>:<bcrypt password hash>:admin` 形式です。
