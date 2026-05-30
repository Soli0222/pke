terraform {
  required_version = ">= 1.5.0"

  required_providers {
    external = {
      source  = "hashicorp/external"
      version = "2.4.0"
    }

    github = {
      source  = "integrations/github"
      version = "6.12.1"
    }
  }

  backend "s3" {
    endpoints = {
      s3 = "https://e334a8146ecc36d6c72387c7e99630ee.r2.cloudflarestorage.com"
    }
    bucket                      = "tfstate"
    key                         = "github/terraform.tfstate"
    region                      = "auto"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}

provider "github" {
  owner = var.github_owner
}

variable "github_owner" {
  description = "GitHub user or organization that owns the managed repositories."
  type        = string
  default     = "Soli0222"
}

output "managed_repositories" {
  description = "Repositories managed by this Terraform configuration."
  value       = sort(keys(local.repositories))
}
