locals {
  sui_callback_url = "https://sui.str08.net/api/auth/callback"
  sui_user_email   = "sui@str08.net"
}

# ---------------------------------------------------------------------------
# Application (sui / SPA)
# ---------------------------------------------------------------------------

resource "auth0_client" "sui" {
  name        = "sui"
  description = "sui (https://sui.str08.net)"
  app_type    = "spa"

  oidc_conformant = true
  grant_types     = ["authorization_code"]

  callbacks           = [local.sui_callback_url]
  web_origins         = []
  allowed_logout_urls = []
}

# Public client: no client authentication at the token endpoint.
resource "auth0_client_credentials" "sui" {
  client_id             = auth0_client.sui.id
  authentication_method = "none"
}

# ---------------------------------------------------------------------------
# Database connection (dedicated to sui; the default
# Username-Password-Authentication connection is left untouched)
# ---------------------------------------------------------------------------

resource "auth0_connection" "sui" {
  name     = "sui-users"
  strategy = "auth0"

  options {
    disable_signup         = true
    brute_force_protection = true
    password_policy        = "excellent"
    requires_username      = false

    # Passkeys. Auth0 requires password authentication to stay enabled
    # alongside passkeys so that browsers and devices without passkey support
    # can still sign in.
    authentication_methods {
      password {
        enabled = true
      }
      passkey {
        enabled = true
      }
    }

    passkey_options {
      # "both" = passkey button + autofill.
      challenge_ui                   = "both"
      progressive_enrollment_enabled = true
      local_enrollment_enabled       = true
    }
  }
}

variable "management_client_id" {
  description = <<-EOT
    Client ID of the Management API M2M application used by this configuration.
    Auth0 rejects single user creation unless the connection is enabled for the
    calling client, so it has to be an enabled client of this connection.
    Exported as TF_VAR_management_client_id by setup.sh.
  EOT
  type        = string
}

# Login-wise only the sui application may use this connection; the M2M
# application is present solely so that auth0_user.sui can be managed here.
resource "auth0_connection_clients" "sui" {
  connection_id = auth0_connection.sui.id
  enabled_clients = [
    auth0_client.sui.id,
    var.management_client_id,
  ]
}

# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

# Auth0 requires an initial password when creating a user in an "auth0"
# strategy connection. Retrieve it with:
#   terraform output -raw sui_user_initial_password
resource "random_password" "sui_user" {
  length           = 32
  min_lower        = 4
  min_upper        = 4
  min_numeric      = 4
  min_special      = 4
  override_special = "!@#%^*_-+=?"
}

resource "auth0_user" "sui" {
  connection_name = auth0_connection.sui.name

  email          = local.sui_user_email
  email_verified = true
  password       = random_password.sui_user.result

  # verify_email is intentionally not set: it would override email_verified.

  lifecycle {
    # The password is only used to bootstrap the account; do not fight with
    # password changes made through Auth0 itself.
    ignore_changes = [password]
  }

  depends_on = [auth0_connection_clients.sui]
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "sui_client_id" {
  description = "Auth0 client ID of the sui SPA."
  value       = auth0_client.sui.client_id
}

output "sui_connection_name" {
  description = "Name of the database connection dedicated to sui."
  value       = auth0_connection.sui.name
}

output "sui_user_initial_password" {
  description = "Initial password of the sui user."
  value       = random_password.sui_user.result
  sensitive   = true
}
