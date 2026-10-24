locals {
  dcr_allowed_scopes = concat(
    ["openid", "basic", "profile", "email", "offline_access"],
    [for scope in keycloak_openid_client_scope.sui : scope.name],
  )
  # realm 作成時に Keycloak が自動生成する匿名登録ポリシーを取り込む。
  dcr_registration_policies = {
    consent = {
      name        = "Consent Required"
      provider_id = "consent-required"
      config      = {}
    }
    full_scope = {
      name        = "Full Scope Disabled"
      provider_id = "scope"
      config      = {}
    }
    max_clients = {
      name        = "Max Clients Limit"
      provider_id = "max-clients"
      # DCR だけでなく、組み込み・事前登録・CIMD を含む realm 全体の上限。
      config = { "max-clients" = "50" }
    }
    client_scopes = {
      name        = "Allowed Client Scopes"
      provider_id = "allowed-client-templates"
      config = {
        "allow-default-scopes"  = "false"
        "allowed-client-scopes" = join(",", local.dcr_allowed_scopes)
      }
    }
    protocol_mappers = {
      name        = "Allowed Protocol Mapper Types"
      provider_id = "allowed-protocol-mappers"
      # claims は管理済み client scopes から付与し、登録者による mapper 追加を許可しない。
      config = { "allowed-protocol-mapper-types" = "" }
    }
  }
}

import {
  for_each = local.dcr_registration_policies
  to       = keycloak_realm_client_registration_policy.dcr[each.key]
  id       = "${keycloak_realm.pke.realm}/${each.value.name}/${each.value.provider_id}/anonymous"
}

resource "keycloak_realm_client_registration_policy" "dcr" {
  for_each    = local.dcr_registration_policies
  realm_id    = keycloak_realm.pke.id
  name        = each.value.name
  provider_id = each.value.provider_id
  sub_type    = "anonymous"
  config      = each.value.config
  depends_on = [
    keycloak_realm_default_client_scopes.pke,
    keycloak_realm_optional_client_scopes.pke,
  ]
}

# ByAnonymous は CIMD の登録・更新にも一致するため、共通の登録要件だけを指定する。
# localhost / 固定ポートを含む候補を登録できるようにし、実際の URI の scheme は認可時に検査する。
resource "keycloak_realm_client_policy_profile" "mcp_registration" {
  realm_id    = keycloak_realm.pke.id
  name        = "sui-mcp-registration"
  description = "MCP client registration: PKCE S256 and loopback-compatible redirect metadata"
  # 認可時の検査を先に有効にしてから、登録時の OAuth 2.1 固有の制約を外す。
  depends_on = [keycloak_realm_client_policy_profile_policy.authorization_redirect]
  lifecycle {
    replace_triggered_by = [keycloak_realm.pke.realm]
  }
  executor {
    name          = "pkce-enforcer"
    configuration = { "auto-configure" = "true" }
  }
  executor {
    name = "secure-redirect-uris-enforcer"
    configuration = {
      # OAuth 2.0 の fragment 禁止を維持し、localhost と登録時のポート指定を受け付ける。
      # 外部 HTTP は登録候補には入るが、authorization_redirect で認可要求を拒否する。
      "allow-http-scheme"            = "true"
      "allow-ipv4-loopback-address"  = "true"
      "allow-ipv6-loopback-address"  = "true"
      "allow-private-use-uri-scheme" = "false"
      "allow-wildcard-context-path"  = "false"
      "allow-open-redirect"          = "false"
      "oauth-2-0-compliant"          = "true"
      "oauth-2-1-compliant"          = "false"
    }
  }
  executor {
    name          = "reject-implicit-grant"
    configuration = { "auto-configure" = "true" }
  }
  executor {
    name          = "reject-ropc-grant"
    configuration = { "auto-configure" = "true" }
  }
}

# 全クライアントの実際の認可要求を HTTPS / HTTP loopback のみに制限する。
# secure-client-uris の登録処理は CIMD 非対応なので、CRUD イベントには適用しない。
resource "keycloak_realm_client_policy_profile" "authorization_redirect" {
  realm_id    = keycloak_realm.pke.id
  name        = "pke-authorization-redirect"
  description = "Authorization requests: HTTPS or HTTP loopback redirects only"
  lifecycle {
    replace_triggered_by = [keycloak_realm.pke.realm]
  }
  executor {
    name = "secure-client-uris"
    configuration = {
      "allow-http-on-localhost" = "true"
    }
  }
}

resource "keycloak_realm_client_policy_profile_policy" "authorization_redirect" {
  realm_id = keycloak_realm.pke.id
  name     = "pke-authorization-redirect"
  profiles = [keycloak_realm_client_policy_profile.authorization_redirect.name]
  lifecycle {
    replace_triggered_by = [keycloak_realm.pke.realm]
  }
  condition {
    name          = "any-client"
    configuration = {}
  }
  condition {
    name = "client-updater-context"
    configuration = {
      # CRUD は NO、それ以外は ABSTAIN。認可時は any-client の YES で適用される。
      "is-negative-logic" = "true"
      "update-client-source" = jsonencode([
        "ByAnonymous", "ByInitialAccessToken", "ByRegistrationAccessToken", "ByAuthenticatedUser",
      ])
    }
  }
}

resource "keycloak_realm_client_policy_profile_policy" "mcp_registration" {
  realm_id = keycloak_realm.pke.id
  name     = "sui-mcp-registration"
  profiles = [keycloak_realm_client_policy_profile.mcp_registration.name]
  lifecycle {
    replace_triggered_by = [keycloak_realm.pke.realm]
  }
  condition {
    name = "client-updater-context"
    configuration = {
      "update-client-source" = jsonencode(["ByAnonymous", "ByRegistrationAccessToken"])
    }
  }
}
