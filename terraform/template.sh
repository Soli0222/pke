#!/bin/bash

# Ubuntu 24.04 minimal cloud image template creation script
# Run this on each Proxmox host (kkg-pve1, kkg-pve2, kkg-pve3)

set -e

# Configuration (consistent across all nodes)
TEMPLATE_ID=9001
CLOUD_IMAGE_URL="https://cloud-images.ubuntu.com/minimal/releases/noble/release-20250727/ubuntu-24.04-minimal-cloudimg-amd64.img"

# 1) 公式 Cloud Image を取得（Ubuntu 24.04）
cd /tmp
wget $CLOUD_IMAGE_URL

# 2) 空VM作成（例: VMID=9000、テンプレ名は defaults.template と一致）
qm create $TEMPLATE_ID --name ubuntu-24.04-minimal-cloudimg --memory 2048 --cores 2 --net0 virtio,bridge=vmbr0

# 3) ディスクを local-lvm にインポートして scsi0 に接続（VirtIO SCSI）
qm importdisk $TEMPLATE_ID ubuntu-24.04-minimal-cloudimg-amd64.img local-lvm
qm set $TEMPLATE_ID --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-$TEMPLATE_ID-disk-0

# 4) Cloud-Init ドライブ（ide2）、シリアル/VGA、Guest Agent
qm set $TEMPLATE_ID --ide2 local-lvm:cloudinit
qm set $TEMPLATE_ID --serial0 socket --vga serial0
qm set $TEMPLATE_ID --agent enabled=1

# 5) CPUタイプを host に設定
qm set $TEMPLATE_ID --cpu host

# 6) ブート順（scsi0）
qm set $TEMPLATE_ID --boot order=scsi0

# 7) テンプレート化
qm template $TEMPLATE_ID