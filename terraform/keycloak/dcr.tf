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
# redirect URI のホストは限定せず、HTTPS・ワイルドカード禁止などの共通要件を適用する。
resource "keycloak_realm_client_policy_profile" "mcp_registration" {
  realm_id    = keycloak_realm.pke.id
  name        = "sui-mcp-registration"
  description = "MCP client registration: PKCE S256 and HTTPS redirects"
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
      "allow-http-scheme"            = "false"
      "allow-ipv4-loopback-address"  = "false"
      "allow-ipv6-loopback-address"  = "false"
      "allow-private-use-uri-scheme" = "false"
      "allow-wildcard-context-path"  = "false"
      "allow-open-redirect"          = "false"
      "oauth-2-1-compliant"          = "true"
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
