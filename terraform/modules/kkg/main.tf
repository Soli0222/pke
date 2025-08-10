# ============================================================================
# Cloud Image Management (各ノードでcloud imageを準備)
# ============================================================================

resource "null_resource" "prepare_cloud_image" {
  for_each = var.proxmox_hosts

  provisioner "local-exec" {
    command = <<-EOT
      HOST_IP=$(echo ${each.value.api_url} | sed 's|https://||' | sed 's|:.*||')
      ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@$HOST_IP "
        set -e
        cd /var/lib/vz/template/iso
        
        # Cloud imageが存在しない場合のみダウンロード
        if [ ! -f 'ubuntu-24.04-minimal-cloudimg-amd64.img' ]; then
          echo 'Downloading Ubuntu 24.04 cloud image...'
          wget -O ubuntu-24.04-minimal-cloudimg-amd64.img '${var.cloud_image_url}'
        else
          echo 'Cloud image already exists'
        fi
        
        # ファイルサイズを確認
        ls -lh ubuntu-24.04-minimal-cloudimg-amd64.img
      "
    EOT
  }

  triggers = {
    image_url = var.cloud_image_url
    host_key  = each.key
  }
}

# ============================================================================
# KKG Host VMs
# ============================================================================

# Kubernetes Control Plane 1 (kkg-pve1)
resource "proxmox_vm_qemu" "kkg_cp1" {
  provider    = proxmox.kkg-pve1
  name        = "kkg-cp1"
  target_node = var.proxmox_hosts["kkg-pve1"].node_name
  vmid        = 2013
  
  # VM Configuration
  cores    = 4
  sockets  = 1
  memory   = 4096
  balloon  = 1024
  
  # Boot from cloud image (no template needed)
  boot    = "order=scsi0"
  scsihw  = "virtio-scsi-pci"
  
  # Cloud image as primary disk
  disk {
    type     = "scsi"
    storage  = var.proxmox_hosts["kkg-pve1"].storage
    size     = "50G"
    format   = "qcow2"
    # Use downloaded cloud image
    file     = "local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img"
    ssd      = 1
    slot     = 0
  }
  
  # Cloud-init drive
  disk {
    type     = "ide"
    storage  = var.proxmox_hosts["kkg-pve1"].storage
    size     = "4M"
    format   = "raw"
    media    = "cdrom"
    slot     = 2
  }
  
  # Network Configuration
  network {
    model  = "virtio"
    bridge = var.network_bridge
  }
  
  # Cloud-init Configuration
  os_type      = "cloud-init"
  ipconfig0    = "ip=192.168.20.13/24,gw=192.168.20.1"
  nameserver   = "192.168.20.1"
  searchdomain = "local"
  
  ciuser   = var.vm_user
  sshkeys  = var.ssh_public_key
  
  # Ensure cloud image is ready
  depends_on = [
    null_resource.prepare_cloud_image
  ]
  
  lifecycle {
    ignore_changes = [
      network,
      disk,
    ]
  }
}

# Kubernetes Worker Node 2 (kkg-pve2)
resource "proxmox_vm_qemu" "kkg_wk2" {
  provider    = proxmox.kkg-pve2
  name        = "kkg-wk2"
  target_node = var.proxmox_hosts["kkg-pve2"].node_name
  vmid        = 2017
  
  # VM Configuration
  cores    = 4
  sockets  = 1
  memory   = 8192
  balloon  = 1024
  
  # Boot from cloud image
  boot    = "order=scsi0"
  scsihw  = "virtio-scsi-pci"
  
  # Cloud image as primary disk
  disk {
    type     = "scsi"
    storage  = var.proxmox_hosts["kkg-pve2"].storage
    size     = "50G"
    format   = "qcow2"
    file     = "local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img"
    ssd      = 1
    slot     = 0
  }
  
  # Cloud-init drive
  disk {
    type     = "ide"
    storage  = var.proxmox_hosts["kkg-pve2"].storage
    size     = "4M"
    format   = "raw"
    media    = "cdrom"
    slot     = 2
  }
  
  # Network Configuration
  network {
    model  = "virtio"
    bridge = var.network_bridge
  }
  
  # Cloud-init Configuration
  os_type      = "cloud-init"
  ipconfig0    = "ip=192.168.20.17/24,gw=192.168.20.1"
  nameserver   = "192.168.20.1"
  searchdomain = "local"
  
  ciuser   = var.vm_user
  sshkeys  = var.ssh_public_key
  
  depends_on = [
    null_resource.prepare_cloud_image
  ]
  
  lifecycle {
    ignore_changes = [
      network,
      disk,
    ]
  }
}

# ============================================================================
# kkg-pve2 Host Resources
# ============================================================================

# Kubernetes Control Plane 2 (kkg-pve2)
resource "proxmox_vm_qemu" "kkg_cp2" {
  provider    = proxmox.kkg-pve2
  name        = "kkg-cp2"
  target_node = var.proxmox_hosts["kkg-pve2"].node_name
  vmid        = 2014
  
  cores    = 4
  sockets  = 1
  memory   = 4096
  balloon  = 1024
  
  boot    = "order=scsi0"
  scsihw  = "virtio-scsi-pci"
  
  disk {
    type     = "scsi"
    storage  = var.proxmox_hosts["kkg-pve2"].storage
    size     = "50G"
    format   = "qcow2"
    file     = "local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img"
    ssd      = 1
    slot     = 0
  }
  
  disk {
    type     = "ide"
    storage  = var.proxmox_hosts["kkg-pve2"].storage
    size     = "4M"
    format   = "raw"
    media    = "cdrom"
    slot     = 2
  }
  
  network {
    model  = "virtio"
    bridge = var.network_bridge
  }
  
  os_type      = "cloud-init"
  ipconfig0    = "ip=192.168.20.14/24,gw=192.168.20.1"
  nameserver   = "192.168.20.1"
  searchdomain = "local"
  
  ciuser   = var.vm_user
  sshkeys  = var.ssh_public_key
  
  depends_on = [null_resource.prepare_cloud_image]
  
  lifecycle {
    ignore_changes = [network, disk]
  }
}

# Kubernetes Worker Node 3 (kkg-pve3)
resource "proxmox_vm_qemu" "kkg_wk3" {
  provider    = proxmox.kkg-pve3
  name        = "kkg-wk3"
  target_node = var.proxmox_hosts["kkg-pve3"].node_name
  vmid        = 2018
  
  cores    = 8
  sockets  = 1
  memory   = 24576
  balloon  = 2048
  
  boot    = "order=scsi0"
  scsihw  = "virtio-scsi-pci"
  
  disk {
    type     = "scsi"
    storage  = var.proxmox_hosts["kkg-pve3"].storage
    size     = "100G"
    format   = "qcow2"
    file     = "local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img"
    ssd      = 1
    slot     = 0
  }
  
  disk {
    type     = "ide"
    storage  = var.proxmox_hosts["kkg-pve3"].storage
    size     = "4M"
    format   = "raw"
    media    = "cdrom"
    slot     = 2
  }
  
  network {
    model  = "virtio"
    bridge = var.network_bridge
  }
  
  os_type      = "cloud-init"
  ipconfig0    = "ip=192.168.20.18/24,gw=192.168.20.1"
  nameserver   = "192.168.20.1"
  searchdomain = "local"
  
  ciuser   = var.vm_user
  sshkeys  = var.ssh_public_key
  
  depends_on = [null_resource.prepare_cloud_image]
  
  lifecycle {
    ignore_changes = [network, disk]
  }
}

# Load Balancer 1 (kkg-pve1)
resource "proxmox_vm_qemu" "kkg_lb1" {
  provider    = proxmox.kkg-pve1
  name        = "kkg-lb1"
  target_node = var.proxmox_hosts["kkg-pve1"].node_name
  vmid        = 2011
  
  cores    = 4
  sockets  = 1
  memory   = 2048
  balloon  = 512
  
  boot    = "order=scsi0"
  scsihw  = "virtio-scsi-pci"
  
  disk {
    type     = "scsi"
    storage  = var.proxmox_hosts["kkg-pve1"].storage
    size     = "20G"
    format   = "qcow2"
    file     = "local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img"
    ssd      = 1
    slot     = 0
  }
  
  disk {
    type     = "ide"
    storage  = var.proxmox_hosts["kkg-pve1"].storage
    size     = "4M"
    format   = "raw"
    media    = "cdrom"
    slot     = 2
  }
  
  network {
    model  = "virtio"
    bridge = var.network_bridge
  }
  
  os_type      = "cloud-init"
  ipconfig0    = "ip=192.168.20.11/24,gw=192.168.20.1"
  nameserver   = "192.168.20.1"
  searchdomain = "local"
  
  ciuser   = var.vm_user
  sshkeys  = var.ssh_public_key
  
  depends_on = [null_resource.prepare_cloud_image]
  
  lifecycle {
    ignore_changes = [network, disk]
  }
}

# ============================================================================
# kkg-pve3 Host Resources  
# ============================================================================

# Kubernetes Control Plane 3 (kkg-pve3)
resource "proxmox_vm_qemu" "kkg_cp3" {
  provider    = proxmox.kkg-pve3
  name        = "kkg-cp3"
  target_node = var.proxmox_hosts["kkg-pve3"].node_name
  vmid        = 2015
  
  cores    = 8
  sockets  = 1
  memory   = 4096
  balloon  = 1024
  
  boot    = "order=scsi0"
  scsihw  = "virtio-scsi-pci"
  
  disk {
    type     = "scsi"
    storage  = var.proxmox_hosts["kkg-pve3"].storage
    size     = "50G"
    format   = "qcow2"
    file     = "local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img"
    ssd      = 1
    slot     = 0
  }
  
  disk {
    type     = "ide"
    storage  = var.proxmox_hosts["kkg-pve3"].storage
    size     = "4M"
    format   = "raw"
    media    = "cdrom"
    slot     = 2
  }
  
  network {
    model  = "virtio"
    bridge = var.network_bridge
  }
  
  os_type      = "cloud-init"
  ipconfig0    = "ip=192.168.20.15/24,gw=192.168.20.1"
  nameserver   = "192.168.20.1"
  searchdomain = "local"
  
  ciuser   = var.vm_user
  sshkeys  = var.ssh_public_key
  
  depends_on = [null_resource.prepare_cloud_image]
  
  lifecycle {
    ignore_changes = [network, disk]
  }
}

# Kubernetes Worker Node 1 (kkg-pve1)
resource "proxmox_vm_qemu" "kkg_wk1" {
  provider    = proxmox.kkg-pve1
  name        = "kkg-wk1"
  target_node = var.proxmox_hosts["kkg-pve1"].node_name
  vmid        = 2016
  
  cores    = 4
  sockets  = 1
  memory   = 8192
  balloon  = 1024
  
  boot    = "order=scsi0"
  scsihw  = "virtio-scsi-pci"
  
  disk {
    type     = "scsi"
    storage  = var.proxmox_hosts["kkg-pve1"].storage
    size     = "50G"
    format   = "qcow2"
    file     = "local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img"
    ssd      = 1
    slot     = 0
  }
  
  disk {
    type     = "ide"
    storage  = var.proxmox_hosts["kkg-pve1"].storage
    size     = "4M"
    format   = "raw"
    media    = "cdrom"
    slot     = 2
  }
  
  network {
    model  = "virtio"
    bridge = var.network_bridge
  }
  
  os_type      = "cloud-init"
  ipconfig0    = "ip=192.168.20.16/24,gw=192.168.20.1"
  nameserver   = "192.168.20.1"
  searchdomain = "local"
  
  ciuser   = var.vm_user
  sshkeys  = var.ssh_public_key
  
  depends_on = [null_resource.prepare_cloud_image]
  
  lifecycle {
    ignore_changes = [network, disk]
  }
}

# Load Balancer 2 (kkg-pve2)
resource "proxmox_vm_qemu" "kkg_lb2" {
  provider    = proxmox.kkg-pve2
  name        = "kkg-lb2"
  target_node = var.proxmox_hosts["kkg-pve2"].node_name
  vmid        = 2012
  
  cores    = 4
  sockets  = 1
  memory   = 2048
  balloon  = 512
  
  boot    = "order=scsi0"
  scsihw  = "virtio-scsi-pci"
  
  disk {
    type     = "scsi"
    storage  = var.proxmox_hosts["kkg-pve2"].storage
    size     = "20G"
    format   = "qcow2"
    file     = "local:iso/ubuntu-24.04-minimal-cloudimg-amd64.img"
    ssd      = 1
    slot     = 0
  }
  
  disk {
    type     = "ide"
    storage  = var.proxmox_hosts["kkg-pve2"].storage
    size     = "4M"
    format   = "raw"
    media    = "cdrom"
    slot     = 2
  }
  
  network {
    model  = "virtio"
    bridge = var.network_bridge
  }
  
  os_type      = "cloud-init"
  ipconfig0    = "ip=192.168.20.12/24,gw=192.168.20.1"
  nameserver   = "192.168.20.1"
  searchdomain = "local"
  
  ciuser   = var.vm_user
  sshkeys  = var.ssh_public_key
  
  depends_on = [null_resource.prepare_cloud_image]
  
  lifecycle {
    ignore_changes = [network, disk]
  }
}
