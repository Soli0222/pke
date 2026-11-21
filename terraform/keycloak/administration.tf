# provider 認証には恒久サービスアカウントの client credentials を使う。
# master の全体管理権限はこのサービスアカウントに付与し、pke の soli と分離する。
resource "keycloak_openid_client" "terraform" {
  realm_id                     = keycloak_realm.master.id
  client_id                    = "terraform"
  name                         = "Terraform administration"
  access_type                  = "CONFIDENTIAL"
  client_authenticator_type    = "client-secret"
  client_secret_wo             = var.terraform_client_secret
  client_secret_wo_version     = "1"
  service_accounts_enabled     = true
  standard_flow_enabled        = false
  implicit_flow_enabled        = false
  direct_access_grants_enabled = false
  full_scope_allowed           = true
  lifecycle {
    prevent_destroy = true
  }
}
# 非推奨の keycloak_openid_client_default_scopes / optional_scopes を
# keycloak_client_scope_assignments へ移行する。apply 前に旧リソースを state から除去する:
#   terraform state rm keycloak_openid_client_default_scopes.terraform keycloak_openid_client_default_scopes.sui_web keycloak_openid_client_default_scopes.hokan keycloak_openid_client_default_scopes.chatgpt keycloak_openid_client_optional_scopes.sui_web keycloak_openid_client_optional_scopes.hokan keycloak_openid_client_optional_scopes.chatgpt
# 各 import ブロックは apply 後に削除してよい。
import {
  to = keycloak_client_scope_assignments.terraform
  id = "client/${keycloak_realm.master.id}/${keycloak_openid_client.terraform.id}"
}
# Admin API は kc-admin-api scope の audience を持つトークンだけを受け付ける。
resource "keycloak_client_scope_assignments" "terraform" {
  realm_id       = keycloak_realm.master.id
  client_id      = keycloak_openid_client.terraform.id
  default_scopes = ["basic", "roles", "kc-admin-api"]
  # クライアント作成時に Keycloak が自動割当てした組み込み optional scope を維持する。
  optional_scopes = ["address", "microprofile-jwt", "offline_access", "organization", "phone"]
}
resource "keycloak_openid_client_service_account_realm_role" "terraform_admin" {
  realm_id                = keycloak_realm.master.id
  service_account_user_id = keycloak_openid_client.terraform.service_account_user_id
  role                    = "admin"
}
