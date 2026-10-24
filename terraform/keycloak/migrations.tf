# Terraform のアドレス変更を明示する。realm 属性の変更は provider が置換扱いにするため、
# データを保持する rename ではなく pstr の削除と pke の新規作成になる。
moved {
  from = keycloak_realm.pstr
  to   = keycloak_realm.pke
}
moved {
  from = keycloak_realm_default_client_scopes.pstr
  to   = keycloak_realm_default_client_scopes.pke
}
moved {
  from = keycloak_realm_optional_client_scopes.pstr
  to   = keycloak_realm_optional_client_scopes.pke
}

# 接続固有の ChatGPT クライアントを維持したまま、共通 client ID の登録を追加する。
moved {
  from = keycloak_openid_client.chatgpt
  to   = keycloak_openid_client.chatgpt["connector"]
}
moved {
  from = keycloak_openid_client_default_scopes.chatgpt
  to   = keycloak_openid_client_default_scopes.chatgpt["connector"]
}
moved {
  from = keycloak_openid_client_optional_scopes.chatgpt
  to   = keycloak_openid_client_optional_scopes.chatgpt["connector"]
}
