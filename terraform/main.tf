module "kkg" {
  source = "./modules/kkg"
  
  # Pass variables to the module
  proxmox_hosts    = var.proxmox_hosts
  ssh_public_key   = var.ssh_public_key
  network_bridge   = var.network_bridge
  vm_user          = var.vm_user
  cloud_image_url  = var.cloud_image_url
  
  # Pass provider aliases
  providers = {
    proxmox.kkg-pve1 = proxmox.kkg-pve1
    proxmox.kkg-pve2 = proxmox.kkg-pve2
    proxmox.kkg-pve3 = proxmox.kkg-pve3
  }
}