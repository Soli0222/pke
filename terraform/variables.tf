variable "proxmox_hosts" {
  description = "Proxmox host configurations"
  type = map(object({
    api_url      = string
    user         = string
    password     = string
    node_name    = string
    storage      = string
  }))
  default = {
    "kkg-pve1" = {
      api_url   = "https://192.168.20.2:8006/api2/json"
      user      = "root@pam"
      password  = ""  # Set via environment variable or terraform.tfvars
      node_name = "kkg-pve1"
      storage   = "local-lvm"
    }
    "kkg-pve2" = {
      api_url   = "https://192.168.20.3:8006/api2/json"
      user      = "root@pam" 
      password  = ""  # Set via environment variable or terraform.tfvars
      node_name = "kkg-pve2"
      storage   = "local-lvm"
    }
    "kkg-pve3" = {
      api_url   = "https://192.168.20.4:8006/api2/json"
      user      = "root@pam"
      password  = ""  # Set via environment variable or terraform.tfvars
      node_name = "kkg-pve3" 
      storage   = "local-lvm"
    }
  }
}

variable "ssh_public_key" {
  description = "SSH public key for VM access"
  type        = string
  default     = ""
}

variable "network_bridge" {
  description = "Network bridge name"
  type        = string
  default     = "vmbr0"
}

variable "vm_user" {
  description = "Default user for VMs"
  type        = string
  default     = "ubuntu"
}

variable "cloud_image_url" {
  description = "Ubuntu cloud image download URL"
  type        = string
  default     = "https://cloud-images.ubuntu.com/minimal/releases/noble/release-20250727/ubuntu-24.04-minimal-cloudimg-amd64.img"
}
