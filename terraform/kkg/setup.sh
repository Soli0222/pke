# This script sets up the environment for the Terraform configuration

export TF_VAR_proxmox_api_url=$(op item get 'terraform kkg-pve' --fields label=TF_VAR_proxmox_api_url --format json | jq -r .value)
export TF_VAR_proxmox_tls_insecure=true
export TF_VAR_proxmox_api_id=$(op item get 'terraform kkg-pve' --fields label=TF_VAR_proxmox_api_id --format json | jq -r .value)
export TF_VAR_proxmox_api_secret=$(op item get 'terraform kkg-pve' --fields label=TF_VAR_proxmox_api_secret --format json | jq -r .value)
export TF_VAR_ssh_public_key=$(op item get 'SSH Key' --fields label='public key' --format json | jq -r .value)

export AWS_ACCESS_KEY_ID=$(op item get 'terraform kkg-pve' --fields label=AWS_ACCESS_KEY_ID --format json | jq -r .value)
export AWS_SECRET_ACCESS_KEY=$(op item get 'terraform kkg-pve' --fields label=AWS_SECRET_ACCESS_KEY --format json | jq -r .value)