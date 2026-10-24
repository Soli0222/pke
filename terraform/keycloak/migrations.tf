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
