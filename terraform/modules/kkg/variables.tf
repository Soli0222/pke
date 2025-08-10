variable "proxmox_hosts" {
  description = "Proxmox host configurations"
  type = map(object({
    api_url      = string
    user         = string
    password     = string
    node_name    = string
    storage      = string
  }))
}

variable "ssh_public_key" {
  description = "SSH public key for VM access"
  type        = string
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
