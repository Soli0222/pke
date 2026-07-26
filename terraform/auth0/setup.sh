#!/usr/bin/env bash

# This script sets up the environment for the Terraform configuration.
# Usage: source ./setup.sh

# Auth0 Management API credentials (M2M application).
export AUTH0_DOMAIN=$(op item get 'terraform auth0' --fields label=AUTH0_DOMAIN --format json | jq -r .value)
export AUTH0_CLIENT_ID=$(op item get 'terraform auth0' --fields label=AUTH0_CLIENT_ID --format json | jq -r .value)
export AUTH0_CLIENT_SECRET=$(op item get 'terraform auth0' --fields label=AUTH0_CLIENT_SECRET --format json | jq -r .value)

# Auth0 requires the connection to be enabled for the client that calls
# POST /api/v2/users, so the M2M application itself must be an enabled client.
export TF_VAR_management_client_id="$AUTH0_CLIENT_ID"

# R2 S3-compatible backend credentials.
export AWS_ACCESS_KEY_ID=$(op item get 'terraform kkg-pve' --fields label=AWS_ACCESS_KEY_ID --format json | jq -r .value)
export AWS_SECRET_ACCESS_KEY=$(op item get 'terraform kkg-pve' --fields label=AWS_SECRET_ACCESS_KEY --format json | jq -r .value)
