terraform {
  required_providers {
    tailscale = {
      source  = "tailscale/tailscale"
      version = "0.30.0"
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
      "tag:service"      = ["group:service"],
      "tag:operation"    = ["group:operation"],
      "tag:k8s-operator" = [],
      "tag:k8s"          = ["tag:k8s-operator"],
      "tag:muse-client"  = ["group:operation"],
      "tag:muse-server"  = ["group:operation"],
    }

    "groups" = {
      "group:service"   = ["Soli0222@github"],
      "group:operation" = ["Soli0222@github"],
    }

    "grants" = [
      {
        "src" = ["tag:muse-client"],
        "dst" = ["tag:muse-server"],
        "ip"  = ["*"],
      },
      {
        "src" = ["tag:muse-server"],
        "dst" = ["tag:service"],
        "ip"  = ["tcp:6443"],
      },
    ]

    "tests" = [
      {
        "src"    = "tag:muse-server",
        "proto"  = "tcp",
        "accept" = ["tag:service:6443"],
        "deny"   = ["tag:service:22", "tag:service:2379", "tag:service:2380", "tag:service:10250"],
      },
      {
        "src"   = "tag:muse-server",
        "proto" = "udp",
        "deny"  = ["tag:service:6443"],
      },
    ]

    "acls" = [
      {
        "action" = "accept",
        "src"    = ["tag:operation"],
        "dst"    = ["tag:service:22", "tag:service:6443"],
        "proto"  = "tcp",
      },
    ]

    "ssh" = [
      {
        "src"    = ["tag:operation"],
        "dst"    = ["tag:service"],
        "users"  = ["soli"],
        "action" = "accept",
      },
      {
        "action" : "accept",
        "src" : ["tag:muse-client"],
        "dst" : ["tag:muse-server"],
        "users" : ["muse"]
      },
    ]
  })
}
