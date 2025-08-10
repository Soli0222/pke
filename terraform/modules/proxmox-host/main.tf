locals {
  vms_map = { for vm in var.vms : vm.name => vm }
}

resource "proxmox_vm_qemu" "vm" {
  for_each    = local.vms_map
  name        = each.value.name
  target_node = var.host.node_name
  vmid        = each.value.vmid

  clone       = var.defaults.template
  full_clone  = true

  cpu {
    cores   = each.value.cpu
    sockets = 1
  }
  memory   = each.value.memory_mb
  balloon  = min(1024, each.value.memory_mb)

  # Cloud-Init
  os_type      = "cloud-init"
  ipconfig0    = "ip=${each.value.ip},gw=${var.network.gateway}"
  nameserver   = var.network.nameserver
  searchdomain = var.network.search_domain
  ciuser       = var.defaults.vm_user
  sshkeys      = format("%s\n", trimspace(var.ssh_public_key))

  # Network
  network {
    id     = 0
    model  = "virtio"
    bridge = var.network.bridge
  }

  # Ensure cloud-init drive exists on target storage (ide2 is Proxmox default)
  disk {
    type    = "cloudinit"
    slot    = "ide2"
    storage = var.host.storage
  }

  # Root disk (resize/move after clone)
  disk {
    type      = "disk"
    slot      = "scsi0"
    storage   = var.host.storage
    size      = "${each.value.disk_gb}G"
    emulatessd= true
  }

  scsihw = "virtio-scsi-pci"
  boot   = "order=scsi0"

  lifecycle {
    ignore_changes = [network]
  }
}
