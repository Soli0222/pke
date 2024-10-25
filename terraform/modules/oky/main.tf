terraform {
  required_providers {
    proxmox = {
      source = "bpg/proxmox"
      version = "0.66.2"
    }
  }
}

provider "proxmox" {
  endpoint = "${var.proxmox_config.endpoint}"
  username = "${var.proxmox_config.username}"
  password = "${var.proxmox_config.password}"
  insecure = true
}

data "local_file" "ssh_public_key" {
  filename = "${var.proxmox_config.pub_key_file}"
}

resource "proxmox_virtual_environment_download_file" "oky-pve-1-image" {
  content_type = "iso"
  datastore_id = "local"
  node_name    = "oky-pve-1"

  url = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}

resource "proxmox_virtual_environment_download_file" "oky-pve-2-image" {
  content_type = "iso"
  datastore_id = "local"
  node_name    = "oky-pve-2"

  url = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}

resource "proxmox_virtual_environment_download_file" "oky-pve-3-image" {
  content_type = "iso"
  datastore_id = "local"
  node_name    = "oky-pve-3"

  url = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}