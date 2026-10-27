# master は起動時に作られる。管理ログインも mTLS ホスト内で完結させる。
import {
  to = keycloak_realm.master
  id = "master"
}
resource "keycloak_realm" "master" {
  realm   = "master"
  enabled = true
  attributes = {
    frontendUrl           = var.admin_url
    adminEventsExpiration = "2592000"
  }
  password_policy = "length(16)"
  web_authn_passwordless_policy {
    relying_party_entity_name     = "Keycloak administration"
    relying_party_id              = split(":", split("//", var.admin_url)[1])[0]
    discoverable_credential       = "required"
    user_verification_requirement = "required"
    passwordless_passkeys_enabled = true
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "keycloak_realm" "pke" {
  realm                       = "pke"
  enabled                     = true
  display_name                = "pke"
  ssl_required                = "external"
  registration_allowed        = false
  reset_password_allowed      = false
  login_with_email_allowed    = true
  duplicate_emails_allowed    = false
  access_token_lifespan       = "5m"
  sso_session_idle_timeout    = "720h"
  sso_session_max_lifespan    = "2160h"
  revoke_refresh_token        = true
  refresh_token_max_reuse     = 0
  default_signature_algorithm = "RS256"
  password_policy             = "length(16)"
  attributes = {
    frontendUrl           = var.public_url
    adminEventsExpiration = "2592000"
  }
  web_authn_passwordless_policy {
    relying_party_entity_name     = "pke"
    relying_party_id              = split(":", split("//", var.public_url)[1])[0]
    discoverable_credential       = "required"
    user_verification_requirement = "required"
    passwordless_passkeys_enabled = true
  }
  security_defenses {
    brute_force_detection {
      permanent_lockout                = false
      max_login_failures               = 5
      wait_increment_seconds           = 60
      minimum_quick_login_wait_seconds = 60
      max_failure_wait_seconds         = 900
      failure_reset_time_seconds       = 43200
    }
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "keycloak_user" "soli" {
  realm_id         = keycloak_realm.pke.id
  username         = "soli"
  enabled          = true
  email            = var.soli_email
  email_verified   = true
  first_name       = "Soli"
  last_name        = "User"
  required_actions = ["CONFIGURE_TOTP", "webauthn-register-passwordless"]
  initial_password {
    value_wo         = var.soli_initial_password
    value_wo_version = "1"
    temporary        = true
  }
  depends_on = [
    keycloak_required_action.otp,
    keycloak_required_action.passkey,
  ]
  lifecycle {
    prevent_destroy = true
    # 利用者が初回ログインで完了した action を再要求しない。
    ignore_changes = [required_actions]
  }
}

# 新しいアプリや CIMD クライアントへ管理ロールを既定で渡さない。
# 既存の組み込み管理コンソールには元の client scopes が残る。
resource "keycloak_realm_default_client_scopes" "pke" {
  realm_id       = keycloak_realm.pke.id
  default_scopes = ["basic", "profile", "email"]
}

data "keycloak_openid_client" "realm_management" {
  realm_id  = keycloak_realm.pke.id
  client_id = "realm-management"
}
data "keycloak_role" "pke_admin" {
  realm_id  = keycloak_realm.pke.id
  client_id = data.keycloak_openid_client.realm_management.id
  name      = "realm-admin"
}
resource "keycloak_user_roles" "soli" {
  realm_id   = keycloak_realm.pke.id
  user_id    = keycloak_user.soli.id
  role_ids   = [data.keycloak_role.pke_admin.id]
  exhaustive = false
}

resource "keycloak_openid_client" "sui_web" {
  realm_id                     = keycloak_realm.pke.id
  client_id                    = "sui-web"
  name                         = "sui Web"
  access_type                  = "PUBLIC"
  standard_flow_enabled        = true
  implicit_flow_enabled        = false
  direct_access_grants_enabled = false
  pkce_code_challenge_method   = "S256"
  valid_redirect_uris          = ["https://sui.str08.net/api/auth/callback"]
  full_scope_allowed           = false
}
resource "keycloak_openid_client_default_scopes" "sui_web" {
  realm_id       = keycloak_realm.pke.id
  client_id      = keycloak_openid_client.sui_web.id
  default_scopes = ["basic", "profile", "email"]
}
resource "keycloak_openid_client_optional_scopes" "sui_web" {
  realm_id        = keycloak_realm.pke.id
  client_id       = keycloak_openid_client.sui_web.id
  optional_scopes = []
}
