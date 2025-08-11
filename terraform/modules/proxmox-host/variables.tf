variable "host" {
  description = "Proxmox host info (node_name, storage)"
  type = object({
    node_name = string
    storage   = string
  })
}

variable "vms" {
  description = "List of VMs to create on this host"
  type = list(object({
    name       = string
    vmid       = number
    role       = string
    ip         = string # with cidr
    cpu        = number
    memory_mb  = number
    disk_gb    = number
  }))
}

variable "network" {
  description = "Network settings"
  type = object({
    bridge        = string
    gateway       = string
    nameserver    = string
    search_domain = string
  })
}

variable "defaults" {
  description = "Default settings (vm_user, template)"
  type = object({
    vm_user  = string
    template = string
  })
}

variable "ssh_public_key" {
  description = "SSH public key"
  type        = string
}
