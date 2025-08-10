output "vms" {
  description = "VMs created on this host"
  value = {
    for k, vm in proxmox_vm_qemu.vm : k => {
      id      = vm.vmid
      name    = vm.name
      host    = var.host.node_name
      ip      = local.vms_map[k].ip
      cpu     = vm.cpu[0].cores
      memory  = vm.memory
      role    = local.vms_map[k].role
    }
  }
}
