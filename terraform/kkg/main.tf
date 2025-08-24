terraform {
  required_version = ">= 1.3"
  required_providers {
    proxmox = {
      source  = "telmate/proxmox"
      version = "3.0.2-rc04"
    }
  }
  
  backend "s3" {
    endpoints = {
      s3 = "https://e334a8146ecc36d6c72387c7e99630ee.r2.cloudflarestorage.com"
    }
    bucket                      = "tfstate"
    key                         = "pke/kkg/terraform.tfstate"
    region                      = "auto"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}

provider "proxmox" {
  pm_api_url                  = var.proxmox_api_url
  pm_api_token_id             = var.proxmox_api_id
  pm_api_token_secret         = var.proxmox_api_secret
  pm_tls_insecure             = var.proxmox_tls_insecure
  pm_minimum_permission_check = false
}

# YAMLファイルから設定を読み込み
locals {
  cluster_config = yamldecode(file(var.cluster_config_file))
  
  # VM設定をマップ形式で作成
  vms = {
    for vm in local.cluster_config.vms : vm.name => merge(
      local.cluster_config.vm_types[vm.type],
      vm,
      {
        cluster_name = local.cluster_config.cluster.name
        network      = local.cluster_config.cluster.network
        ssh_keys     = [var.ssh_public_key]
        user         = local.cluster_config.cluster.user
        storage      = local.cluster_config.cluster.storage
        common       = local.cluster_config.cluster.common_settings
        template_id  = local.cluster_config.cluster.template_ids[vm.target_node]
      }
    )
  }
}

# 動的にVMを作成
resource "proxmox_vm_qemu" "vms" {
  for_each = local.vms

  name         = each.value.name
  target_nodes = [each.value.target_node]
  vmid         = each.value.vmid
  clone        = each.value.template_id
  
  memory = each.value.memory

  cpu {
    cores   = each.value.cpu_cores
    sockets = each.value.cpu_sockets
    type    = each.value.common.cpu_type
  }

  full_clone = each.value.common.full_clone

  disks {
    scsi {
      scsi0 {
        disk {
          storage    = each.value.storage.default
          size       = each.value.disk_size
          format     = each.value.common.disk_format
          emulatessd = each.value.common.emulatessd
        }
      }
    }
    ide {
      ide2 {
        cloudinit {
          storage = each.value.storage.default
        }
      }
    }
  }

  network {
    id     = 0
    model  = each.value.common.network_model
    bridge = each.value.common.bridge
  }

  ciuser       = each.value.user
  ipconfig0    = "ip=${each.value.ip}/${each.value.network.subnet},gw=${each.value.network.gateway}"
  nameserver   = each.value.network.nameserver
  searchdomain = each.value.network.searchdomain
  sshkeys      = "${join("\n", each.value.ssh_keys)}\n"

  qemu_os = each.value.common.qemu_os
  scsihw  = each.value.common.scsihw
  boot    = each.value.common.boot

  lifecycle {
    ignore_changes = [
      network.0.macaddr,
      smbios,
      disks,
      memory,
      clone,
      tags,
    ]
  }
}

