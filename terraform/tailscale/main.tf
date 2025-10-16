terraform {
  required_providers {
    tailscale = {
      source  = "tailscale/tailscale"
      version = "0.23.0"
    }
  }

  backend "s3" {
    endpoints = {
      s3 = "https://e334a8146ecc36d6c72387c7e99630ee.r2.cloudflarestorage.com"
    }
    bucket                      = "tfstate"
    key                         = "pke/tailscale/terraform.tfstate"
    region                      = "auto"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}

provider "tailscale" {
  # API key is configured via TAILSCALE_API_KEY environment variable
  # or via the api_key argument
}

# Tailscale ACL configuration
resource "tailscale_acl" "main" {
  acl = jsonencode({
    "tagOwners" = {
      "tag:kkg-external" = ["group:kkg"],
      "tag:service"      = ["group:service"],
      "tag:operation"    = ["group:operation"],
    }

    "groups" = {
      "group:kkg"         = ["Soli0222@github"],
      "group:service"     = ["Soli0222@github"],
      "group:operation"   = ["Soli0222@github"],
    }

    "grants" = [
      {
        "src" = ["tag:kkg-external"],
        "dst" = ["192.168.21.101/32"],
        "ip"  = ["*:*"],
      },
    ]

    "acls" = [
      {
        "action" = "accept",
        "src"    = ["*"],
        "dst"    = ["*:*"],
      },
    ]

    "ssh" = [
      {
        "action" = "check",
        "src"    = ["tag:operation"],
        "dst"    = ["tag:service"],
        "users"  = ["ubuntu"],
        "action" = "accept",
      },
    ]
  })
}
