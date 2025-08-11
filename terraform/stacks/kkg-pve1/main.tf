locals {
  topology = yamldecode(file("../../cluster_topology.yaml"))
  host_id  = "kkg-pve1"
  host     = local.topology.hosts[local.host_id]
  vms      = [for vm in local.topology.vms : vm if vm.host == local.host_id]
}

module "host" {
  source         = "../../modules/proxmox-host"
  host           = local.host
  vms            = local.vms
  network        = local.topology.network
  defaults       = local.topology.defaults
  ssh_public_key = var.ssh_public_key
}

output "vms" {
  value = module.host.vms
}
