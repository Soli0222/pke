output "vm_info" {
  description = "Information about created VMs and containers"
  value = {
    # Control Plane nodes
    control_planes = {
      "kkg-cp1" = {
        id       = proxmox_vm_qemu.kkg_cp1.vmid
        name     = proxmox_vm_qemu.kkg_cp1.name
        ip       = "192.168.20.13"
        host     = var.proxmox_hosts["kkg-pve1"].node_name
        cores    = proxmox_vm_qemu.kkg_cp1.cores
        memory   = proxmox_vm_qemu.kkg_cp1.memory
      }
      "kkg-cp2" = {
        id       = proxmox_vm_qemu.kkg_cp2.vmid
        name     = proxmox_vm_qemu.kkg_cp2.name
        ip       = "192.168.20.14"
        host     = var.proxmox_hosts["kkg-pve2"].node_name
        cores    = proxmox_vm_qemu.kkg_cp2.cores
        memory   = proxmox_vm_qemu.kkg_cp2.memory
      }
      "kkg-cp3" = {
        id       = proxmox_vm_qemu.kkg_cp3.vmid
        name     = proxmox_vm_qemu.kkg_cp3.name
        ip       = "192.168.20.15"
        host     = var.proxmox_hosts["kkg-pve3"].node_name
        cores    = proxmox_vm_qemu.kkg_cp3.cores
        memory   = proxmox_vm_qemu.kkg_cp3.memory
      }
    }
    
    # Worker nodes
    workers = {
      "kkg-wk1" = {
        id       = proxmox_vm_qemu.kkg_wk1.vmid
        name     = proxmox_vm_qemu.kkg_wk1.name
        ip       = "192.168.20.16"
        host     = var.proxmox_hosts["kkg-pve1"].node_name
        cores    = proxmox_vm_qemu.kkg_wk1.cores
        memory   = proxmox_vm_qemu.kkg_wk1.memory
      }
      "kkg-wk2" = {
        id       = proxmox_vm_qemu.kkg_wk2.vmid
        name     = proxmox_vm_qemu.kkg_wk2.name
        ip       = "192.168.20.17"
        host     = var.proxmox_hosts["kkg-pve2"].node_name
        cores    = proxmox_vm_qemu.kkg_wk2.cores
        memory   = proxmox_vm_qemu.kkg_wk2.memory
      }
      "kkg-wk3" = {
        id       = proxmox_vm_qemu.kkg_wk3.vmid
        name     = proxmox_vm_qemu.kkg_wk3.name
        ip       = "192.168.20.18"
        host     = var.proxmox_hosts["kkg-pve3"].node_name
        cores    = proxmox_vm_qemu.kkg_wk3.cores
        memory   = proxmox_vm_qemu.kkg_wk3.memory
      }
    }
    
    # Load balancers
    load_balancers = {
      "kkg-lb1" = {
        id       = proxmox_vm_qemu.kkg_lb1.vmid
        name     = proxmox_vm_qemu.kkg_lb1.name
        ip       = "192.168.20.11"
        host     = var.proxmox_hosts["kkg-pve1"].node_name
        cores    = proxmox_vm_qemu.kkg_lb1.cores
        memory   = proxmox_vm_qemu.kkg_lb1.memory
        type     = "VM"
      }
      "kkg-lb2" = {
        id       = proxmox_vm_qemu.kkg_lb2.vmid
        name     = proxmox_vm_qemu.kkg_lb2.name
        ip       = "192.168.20.12"
        host     = var.proxmox_hosts["kkg-pve2"].node_name
        cores    = proxmox_vm_qemu.kkg_lb2.cores
        memory   = proxmox_vm_qemu.kkg_lb2.memory
        type     = "VM"
      }
    }
  }
}
