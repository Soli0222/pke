# MCP resource discovery is served by sui. Auth0 owns authorization, tokens,
# and ChatGPT client metadata; no client secret is copied into ChatGPT.
locals {
  sui_mcp_identifier   = "https://sui.str08.net/mcp"
  sui_chatgpt_cimd_url = "https://chatgpt.com/oauth/ll1JdSuQS8kD/client.json"
  sui_mcp_scopes = {
    "read:sui"  = "Read sui financial data"
    "write:sui" = "Create, update, and delete sui financial data"
  }
}

# This resource updates the existing provider tenant, not a new Auth0 tenant.
# These settings affect all applications in the tenant.
resource "auth0_tenant" "mcp" {
  client_id_metadata_document_supported = true
  resource_parameter_profile            = "compatibility"
}

resource "auth0_resource_server" "sui_mcp" {
  name                                            = "sui MCP"
  identifier                                      = local.sui_mcp_identifier
  signing_alg                                     = "RS256"
  token_dialect                                   = "rfc9068_profile"
  token_lifetime                                  = 3600
  allow_offline_access                            = true
  skip_consent_for_verifiable_first_party_clients = false

  subject_type_authorization {
    user {
      policy = "require_client_grant"
    }
    client {
      policy = "deny_all"
    }
  }
}

resource "auth0_resource_server_scope" "sui_mcp" {
  for_each = local.sui_mcp_scopes

  resource_server_identifier = auth0_resource_server.sui_mcp.identifier
  scope                      = each.key
  description                = each.value
}

resource "auth0_client_cimd" "chatgpt" {
  external_client_id         = local.sui_chatgpt_cimd_url
  external_client_id_version = 1
  description                = "ChatGPT access to sui MCP"
  oidc_conformant            = true
  grant_types                = ["authorization_code", "refresh_token"]

  # Callbacks and token endpoint authentication come from ChatGPT's CIMD.
  refresh_token {
    rotation_type                = "rotating"
    expiration_type              = "expiring"
    token_lifetime               = 7776000 # 90 days
    idle_token_lifetime          = 2592000 # 30 days
    infinite_token_lifetime      = false
    infinite_idle_token_lifetime = false
    leeway                       = 5
  }

  depends_on = [auth0_tenant.mcp, auth0_connection.sui]
}

# Only this client may request the listed scopes on behalf of a user.
# sui must also enforce its user allowlist and the granted token scopes.
resource "auth0_client_grant" "chatgpt_sui_mcp" {
  client_id    = auth0_client_cimd.chatgpt.id
  audience     = auth0_resource_server.sui_mcp.identifier
  subject_type = "user"
  scopes       = keys(local.sui_mcp_scopes)

  depends_on = [auth0_resource_server_scope.sui_mcp]
}

output "sui_mcp_resource" {
  description = "Canonical MCP URL and expected access token audience for sui."
  value       = auth0_resource_server.sui_mcp.identifier
}

output "sui_mcp_scopes" {
  description = "Scopes that sui must enforce for OAuth MCP requests."
  value       = keys(local.sui_mcp_scopes)
}
