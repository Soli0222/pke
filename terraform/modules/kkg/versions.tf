terraform {
  required_providers {
    proxmox = {
      source  = "telmate/proxmox"
      version = "~> 2.9"
      configuration_aliases = [
        proxmox.kkg-pve1,
        proxmox.kkg-pve2,
        proxmox.kkg-pve3,
      ]
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}
