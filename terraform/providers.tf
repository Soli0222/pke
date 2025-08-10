# Providers have been moved to per-host stacks under ./stacks.
# See stacks/kkg-pve{1,2,3}/providers.tf

# Proxmox provider configurations
# provider "proxmox" {
#   alias = "kkg-pve1"
#   pm_api_url          = var.proxmox_hosts["kkg-pve1"].api_url
#   pm_api_token_id     = var.proxmox_hosts["kkg-pve1"].user
#   pm_api_token_secret = var.proxmox_hosts["kkg-pve1"].password
#   pm_tls_insecure     = true
# }

# provider "proxmox" {
#   alias = "kkg-pve2"
#   pm_api_url          = var.proxmox_hosts["kkg-pve2"].api_url
#   pm_api_token_id     = var.proxmox_hosts["kkg-pve2"].user
#   pm_api_token_secret = var.proxmox_hosts["kkg-pve2"].password
#   pm_tls_insecure     = true
# }

# provider "proxmox" {
#   alias = "kkg-pve3"
#   pm_api_url          = var.proxmox_hosts["kkg-pve3"].api_url
#   pm_api_token_id     = var.proxmox_hosts["kkg-pve3"].user
#   pm_api_token_secret = var.proxmox_hosts["kkg-pve3"].password
#   pm_tls_insecure     = true
# }
