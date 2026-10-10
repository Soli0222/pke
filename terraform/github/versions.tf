terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    # 旧 data.external を含む state の移行と mock テストで同じ schema を使う。
    # 実行する external data source は定義しない。
    external = {
      source  = "hashicorp/external"
      version = "2.4.2"
    }

    github = {
      source  = "integrations/github"
      version = "6.13.0"
    }
  }

  backend "s3" {
    endpoints = {
      s3 = "https://e334a8146ecc36d6c72387c7e99630ee.r2.cloudflarestorage.com"
    }
    bucket                      = "tfstate"
    key                         = "github/terraform.tfstate"
    use_lockfile                = true
    region                      = "auto"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}

provider "github" {
  owner         = local.github_owner
  legacy_client = false
  cache_path    = "${path.module}/.terraform/github-cache"
}
