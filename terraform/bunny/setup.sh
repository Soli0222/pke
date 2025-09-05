# This script sets up the environment for the Terraform configuration

export TF_VAR_bunny_api_key=$(op item get 'terraform kkg-pve' --fields label=TF_VAR_bunny_api_key --format json | jq -r .value)

export AWS_ACCESS_KEY_ID=$(op item get 'terraform kkg-pve' --fields label=AWS_ACCESS_KEY_ID --format json | jq -r .value)
export AWS_SECRET_ACCESS_KEY=$(op item get 'terraform kkg-pve' --fields label=AWS_SECRET_ACCESS_KEY --format json | jq -r .value)