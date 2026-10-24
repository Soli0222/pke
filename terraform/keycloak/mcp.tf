locals {
  mcp_resource = "https://sui.str08.net/mcp"
}
resource "keycloak_openid_client" "sui_mcp" {
  realm_id           = keycloak_realm.pstr.id
  client_id          = "sui-mcp"
  name               = "sui MCP resource"
  access_type        = "BEARER-ONLY"
  full_scope_allowed = false
  extra_config = {
    resource_url = local.mcp_resource
  }
}
resource "keycloak_openid_client_scope" "sui" {
  for_each = {
    "read:sui"  = "sui の資産データを読み取る"
    "write:sui" = "sui の資産データを更新する"
  }
  realm_id                            = keycloak_realm.pstr.id
  name                                = each.key
  description                         = each.value
  consent_screen_text                 = each.value
  include_in_token_scope              = true
  include_in_openid_provider_metadata = true
}
resource "keycloak_openid_audience_protocol_mapper" "sui" {
  for_each                 = keycloak_openid_client_scope.sui
  realm_id                 = keycloak_realm.pstr.id
  client_scope_id          = each.value.id
  name                     = "sui MCP audience"
  included_client_audience = keycloak_openid_client.sui_mcp.client_id
  add_to_id_token          = false
  add_to_access_token      = true
}
# CIMD で新規作成されるクライアントが、要求した権限だけを取得できるようにする。
resource "keycloak_realm_optional_client_scopes" "pstr" {
  realm_id        = keycloak_realm.pstr.id
  optional_scopes = concat(["address", "phone", "offline_access", "microprofile-jwt"], [for scope in keycloak_openid_client_scope.sui : scope.name])
}
resource "keycloak_realm_client_policy_profile" "mcp" {
  realm_id = keycloak_realm.pstr.id
  name     = "sui-mcp-cimd"
  executor {
    name = "client-id-metadata-document"
    configuration = {
      "cimd-allow-http-scheme" = "false"
      # URL-valued metadata に必要なホスト。client_id の受付ホストは policy でさらに限定。
      "cimd-allow-permitted-domains"                             = jsonencode(["claude.ai", "grok.com", "console.x.ai"])
      "cimd-restrict-same-domain"                                = "false"
      "only-allow-confidential-client"                           = "false"
      "accept-public-client-with-confidential-client-only-grant" = "true"
      "cimd-required-properties"                                 = jsonencode(["client_id", "client_name", "redirect_uris"])
      "cimd-resource-indicator-allow-list"                       = jsonencode([local.mcp_resource])
    }
  }
  executor {
    name = "pkce-enforcer"
    configuration = {
      "auto-configure" = "true"
    }
  }
}
resource "keycloak_realm_client_policy_profile_policy" "mcp" {
  realm_id = keycloak_realm.pstr.id
  name     = "sui-mcp-cimd"
  profiles = [keycloak_realm_client_policy_profile.mcp.name]
  condition {
    name = "client-id-uri"
    configuration = {
      "client-id-uri-scheme"                  = jsonencode(["https"])
      "client-id-uri-allow-permitted-domains" = jsonencode(["claude.ai", "grok.com"])
    }
  }
  depends_on = [
    keycloak_realm_optional_client_scopes.pstr,
    keycloak_realm_default_client_scopes.pstr,
  ]
}

# ChatGPT の拡張 metadata は、この Keycloak 版の CIMD parser が拒否する。
# 既存の接続 ID を事前登録し、公開 JWKS による private_key_jwt を使う。
variable "chatgpt_callback_id" {
  description = "ChatGPT の接続固有 callback ID。接続の再作成時は新しい CIMD URL の ID に更新する。"
  type        = string
  default     = "ll1JdSuQS8kD"
  validation {
    condition     = can(regex("^[A-Za-z0-9_-]+$", var.chatgpt_callback_id))
    error_message = "Use the callback ID from the ChatGPT client metadata URL."
  }
}
resource "keycloak_openid_client" "chatgpt" {
  realm_id                     = keycloak_realm.pstr.id
  client_id                    = "https://chatgpt.com/oauth/${var.chatgpt_callback_id}/client.json"
  name                         = "ChatGPT (sui)"
  access_type                  = "CONFIDENTIAL"
  client_authenticator_type    = "client-jwt"
  standard_flow_enabled        = true
  direct_access_grants_enabled = false
  implicit_flow_enabled        = false
  service_accounts_enabled     = false
  pkce_code_challenge_method   = "S256"
  full_scope_allowed           = false
  consent_required             = true
  valid_redirect_uris = [
    "https://chatgpt.com/connector/oauth/${var.chatgpt_callback_id}",
    "https://chatgpt.com/connector_platform_oauth_redirect",
  ]
  extra_config = {
    "use.jwks.url"                    = "true"
    "jwks.url"                        = "https://chatgpt.com/oauth/jwks.json"
    "token.endpoint.auth.signing.alg" = "RS256"
  }
}
resource "keycloak_openid_client_default_scopes" "chatgpt" {
  realm_id       = keycloak_realm.pstr.id
  client_id      = keycloak_openid_client.chatgpt.id
  default_scopes = ["basic", "profile", "email"]
}
resource "keycloak_openid_client_optional_scopes" "chatgpt" {
  realm_id        = keycloak_realm.pstr.id
  client_id       = keycloak_openid_client.chatgpt.id
  optional_scopes = concat(["offline_access"], [for scope in keycloak_openid_client_scope.sui : scope.name])
}
