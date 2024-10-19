resource "proxmox_virtual_environment_vm" "pke-oky-w4" {
  name      = "pke-oky-w4"
  node_name = "oky-pve-2"

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
    dedicated = 4096
  }

  disk {
    datastore_id = "local-lvm"
    file_id = proxmox_virtual_environment_download_file.oky-pve-2-image.id
    interface    = "virtio0"
    iothread     = true
    discard      = "on"
    size         = 50
  }

  initialization {
    ip_config {
      ipv4 {
        address = "192.168.20.16/24"
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
