terraform {
  required_version = ">= 1.11.0"
  required_providers {
    keycloak = {
      source  = "keycloak/keycloak"
      version = "5.10.0"
    }
  }
  backend "s3" {
    endpoints = {
      s3 = "https://e334a8146ecc36d6c72387c7e99630ee.r2.cloudflarestorage.com"
    }
    bucket                      = "tfstate"
    key                         = "pke/keycloak/terraform.tfstate"
    region                      = "auto"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}

provider "keycloak" {
  url = var.admin_url
  # 管理 API の認証は KEYCLOAK_*。mTLS は OAuth client 認証とは独立。
  tls_client_certificate = var.tls_client_certificate
  tls_client_private_key = var.tls_client_private_key
}

variable "tls_client_certificate" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "tls_client_private_key" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "soli_initial_password" {
  type      = string
  sensitive = true
  ephemeral = true
  validation {
    condition     = length(var.soli_initial_password) >= 16
    error_message = "Use an initial password of at least 16 characters."
  }
}
variable "public_url" {
  type    = string
  default = "https://keycloak.pstr.space"
}
variable "admin_url" {
  type    = string
  default = "https://admin.keycloak.pstr.space"
}

variable "sui_image_tag" {
  description = "Bearer JWT 対応の sui PR を含む、公開済みコンテナの固定タグ。切替前にリリースを確認する。"
  type        = string
  validation {
    condition     = length(trimspace(var.sui_image_tag)) > 0 && var.sui_image_tag != "latest"
    error_message = "Specify an immutable release tag that includes Bearer JWT support."
  }
}

variable "soli_email" {
  description = "共通ユーザー soli のメールアドレス。"
  type        = string
}

variable "terraform_client_secret" {
  description = "1Password に事前作成する Terraform 専用クライアントの secret。"
  type        = string
  sensitive   = true
  ephemeral   = true
  validation {
    condition     = length(var.terraform_client_secret) >= 32
    error_message = "Use a random client secret of at least 32 characters."
  }
}
