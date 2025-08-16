variable "proxmox_api_url" {
  description = "Proxmox API URL"
  type        = string
}

variable "proxmox_api_id" {
  description = "Proxmox API token ID"
  type        = string
}

variable "proxmox_api_secret" {
  description = "Proxmox API token secret"
  type        = string
  sensitive   = true
}

variable "proxmox_tls_insecure" {
  description = "Skip TLS verification"
  type        = bool
  default     = true
}

variable "cluster_config_file" {
  description = "Path to cluster configuration YAML file"
  type        = string
  default     = "cluster-config.yaml"
}

variable "ssh_public_key" {
  description = "SSH public key for VM access"
  type        = string
}
