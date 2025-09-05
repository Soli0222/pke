terraform {
  required_providers {
    bunnynet = {
      source = "BunnyWay/bunnynet"
      version = "0.9.0"
    }
  }
  backend "s3" {
    endpoints = {
      s3 = "https://e334a8146ecc36d6c72387c7e99630ee.r2.cloudflarestorage.com"
    }
    bucket                      = "tfstate"
    key                         = "pke/bunny/terraform.tfstate"
    region                      = "auto"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}

provider "bunnynet" {
  api_key = var.bunny_api_key
}

