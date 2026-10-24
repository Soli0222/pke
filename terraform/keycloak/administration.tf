# 初回だけ bootstrap の password grant で作成し、以後の provider 認証に使う。
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
resource "keycloak_openid_client_default_scopes" "terraform" {
  realm_id       = keycloak_realm.master.id
  client_id      = keycloak_openid_client.terraform.id
  default_scopes = ["basic", "roles"]
}
resource "keycloak_openid_client_service_account_realm_role" "terraform_admin" {
  realm_id                = keycloak_realm.master.id
  service_account_user_id = keycloak_openid_client.terraform.service_account_user_id
  role                    = "admin"
}
