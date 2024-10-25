resource "proxmox_virtual_environment_vm" "pke-oky-lb-1" {
  name      = "pke-oky-lb-1"
  node_name = "oky-pve-1"

  agent {
    enabled = false
  }
  stop_on_destroy = true

  startup {
    order      = "3"
    up_delay   = "60"
    down_delay = "60"
  }

  cpu {
    cores = 4
  }

  memory {
    dedicated = 3072
  }

  disk {
    datastore_id = "local-lvm"
    file_id = proxmox_virtual_environment_download_file.oky-pve-1-image.id
    interface    = "virtio0"
    iothread     = true
    discard      = "on"
    size         = 50
  }

  initialization {
    ip_config {
      ipv4 {
        address = "192.168.20.31/24"
        gateway = "192.168.20.1"
      }
    }
    
    user_account {
      username = "ubuntu"
      keys     = [trimspace(data.local_file.ssh_public_key.content)]
    }
  }

  network_device {
    bridge = "vmbr0"
  }

}
