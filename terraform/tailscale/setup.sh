# This script sets up the environment for the Terraform configuration

export TAILSCALE_API_KEY=$(op item get 'terraform tailscale' --fields label=TAILSCALE_API_KEY --format json | jq -r .value)

export AWS_ACCESS_KEY_ID=$(op item get 'terraform kkg-pve' --fields label=AWS_ACCESS_KEY_ID --format json | jq -r .value)
export AWS_SECRET_ACCESS_KEY=$(op item get 'terraform kkg-pve' --fields label=AWS_SECRET_ACCESS_KEY --format json | jq -r .value)