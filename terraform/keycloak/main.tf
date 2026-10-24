# master は起動時に作られる。管理ログインも mTLS ホスト内で完結させる。
import {
  to = keycloak_realm.master
  id = "master"
}
resource "keycloak_realm" "master" {
  realm   = "master"
  enabled = true
  attributes = {
    frontendUrl = var.admin_url
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "keycloak_realm" "sui" {
  realm                       = "sui"
  enabled                     = true
  display_name                = "sui"
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
    frontendUrl = var.public_url
  }
  web_authn_passwordless_policy {
    relying_party_entity_name     = "sui"
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

resource "keycloak_user" "sui" {
  realm_id         = keycloak_realm.sui.id
  username         = "sui"
  enabled          = true
  email            = "sui@str08.net"
  email_verified   = true
  first_name       = "Sui"
  last_name        = "User"
  required_actions = ["webauthn-register-passwordless"]
  initial_password {
    value_wo         = var.sui_initial_password
    value_wo_version = "1"
    temporary        = true
  }
  lifecycle {
    prevent_destroy = true
    # 利用者が初回ログインで完了した action を再要求しない。
    ignore_changes = [required_actions]
  }
}

resource "keycloak_openid_client" "sui_web" {
  realm_id                     = keycloak_realm.sui.id
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
  realm_id       = keycloak_realm.sui.id
  client_id      = keycloak_openid_client.sui_web.id
  default_scopes = ["basic", "profile", "email"]
}
resource "keycloak_openid_client_optional_scopes" "sui_web" {
  realm_id        = keycloak_realm.sui.id
  client_id       = keycloak_openid_client.sui_web.id
  optional_scopes = []
}
