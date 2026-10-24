locals {
  interactive_realms = {
    master = keycloak_realm.master.id
    pke    = keycloak_realm.pke.id
  }
}

# パスキーと OTP の両方を新規ユーザーの初回登録で必須にする。
# soli は required_actions でも明示し、bootstrap の既存ユーザーは取り込まない。
resource "keycloak_required_action" "otp" {
  for_each       = local.interactive_realms
  realm_id       = each.value
  alias          = "CONFIGURE_TOTP"
  name           = "Configure OTP"
  enabled        = true
  default_action = true
  priority       = 20
}
resource "keycloak_required_action" "passkey" {
  for_each       = local.interactive_realms
  realm_id       = each.value
  alias          = "webauthn-register-passwordless"
  name           = "Register Passkey"
  enabled        = true
  default_action = true
  priority       = 30
}

# Cookie による SSO、またはパスキー / パスワードの入力。
# 組み込みフォームのパスキー対応を使い、パスワード認証時には OTP を必須にする。
resource "keycloak_authentication_flow" "browser" {
  for_each    = local.interactive_realms
  realm_id    = each.value
  alias       = "passkey-or-password-otp"
  description = "Passkey with user verification, or password and mandatory OTP"
}
resource "keycloak_authentication_execution" "cookie" {
  for_each          = local.interactive_realms
  realm_id          = each.value
  parent_flow_alias = keycloak_authentication_flow.browser[each.key].alias
  authenticator     = "auth-cookie"
  requirement       = "ALTERNATIVE"
  priority          = 10
}
resource "keycloak_authentication_subflow" "forms" {
  for_each          = local.interactive_realms
  realm_id          = each.value
  parent_flow_alias = keycloak_authentication_flow.browser[each.key].alias
  alias             = "passkey-or-password-otp-forms"
  requirement       = "ALTERNATIVE"
  priority          = 20
}
resource "keycloak_authentication_execution" "credentials" {
  for_each          = local.interactive_realms
  realm_id          = each.value
  parent_flow_alias = keycloak_authentication_subflow.forms[each.key].alias
  authenticator     = "auth-username-password-form"
  requirement       = "REQUIRED"
  priority          = 10
}
resource "keycloak_authentication_subflow" "password_otp" {
  for_each          = local.interactive_realms
  realm_id          = each.value
  parent_flow_alias = keycloak_authentication_subflow.forms[each.key].alias
  alias             = "password-requires-otp"
  requirement       = "CONDITIONAL"
  priority          = 20
}
resource "keycloak_authentication_execution" "not_passkey" {
  for_each          = local.interactive_realms
  realm_id          = each.value
  parent_flow_alias = keycloak_authentication_subflow.password_otp[each.key].alias
  authenticator     = "conditional-credential"
  requirement       = "REQUIRED"
  priority          = 10
}
resource "keycloak_authentication_execution_config" "not_passkey" {
  for_each     = local.interactive_realms
  realm_id     = each.value
  execution_id = keycloak_authentication_execution.not_passkey[each.key].id
  alias        = "skip-otp-only-after-passkey"
  config = {
    credentials = "webauthn-passwordless"
    included    = "false"
  }
}
resource "keycloak_authentication_execution" "otp" {
  for_each          = local.interactive_realms
  realm_id          = each.value
  parent_flow_alias = keycloak_authentication_subflow.password_otp[each.key].alias
  authenticator     = "auth-otp-form"
  requirement       = "REQUIRED"
  priority          = 20
}

# pke はブラウザ経由の認証に統一し、組み込み admin-cli を含め password grant を拒否。
# master の direct grant は初回 bootstrap apply に必要なので、この段階では変更しない。
resource "keycloak_authentication_flow" "deny_direct_grant" {
  realm_id    = keycloak_realm.pke.id
  alias       = "deny-direct-grants"
  description = "Use authorization code and the browser MFA flow"
}
resource "keycloak_authentication_execution" "deny_direct_grant" {
  realm_id          = keycloak_realm.pke.id
  parent_flow_alias = keycloak_authentication_flow.deny_direct_grant.alias
  authenticator     = "deny-access-authenticator"
  requirement       = "REQUIRED"
  priority          = 10
}
resource "keycloak_authentication_bindings" "browser" {
  for_each          = local.interactive_realms
  realm_id          = each.value
  browser_flow      = keycloak_authentication_flow.browser[each.key].alias
  direct_grant_flow = each.key == "pke" ? keycloak_authentication_flow.deny_direct_grant.alias : null
  # フロー構築前に切り替えず、初回の管理用サービスアカウントも先に完成させる。
  depends_on = [
    keycloak_authentication_execution.cookie,
    keycloak_authentication_execution.credentials,
    keycloak_authentication_execution.otp,
    keycloak_authentication_execution_config.not_passkey,
    keycloak_authentication_execution.deny_direct_grant,
    keycloak_required_action.otp,
    keycloak_required_action.passkey,
    keycloak_openid_client_default_scopes.terraform,
    keycloak_openid_client_service_account_realm_role.terraform_admin,
  ]
}
