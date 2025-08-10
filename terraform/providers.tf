# Proxmox provider configurations
provider "proxmox" {
  alias = "kkg-pve1"
  pm_api_url      = var.proxmox_hosts["kkg-pve1"].api_url
  pm_user         = var.proxmox_hosts["kkg-pve1"].user
  pm_password     = var.proxmox_hosts["kkg-pve1"].password
  pm_tls_insecure = true
}

provider "proxmox" {
  alias = "kkg-pve2"
  pm_api_url      = var.proxmox_hosts["kkg-pve2"].api_url
  pm_user         = var.proxmox_hosts["kkg-pve2"].user
  pm_password     = var.proxmox_hosts["kkg-pve2"].password
  pm_tls_insecure = true
}

provider "proxmox" {
  alias = "kkg-pve3"
  pm_api_url      = var.proxmox_hosts["kkg-pve3"].api_url
  pm_user         = var.proxmox_hosts["kkg-pve3"].user
  pm_password     = var.proxmox_hosts["kkg-pve3"].password
  pm_tls_insecure = true
}
