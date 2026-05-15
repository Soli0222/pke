#!/usr/bin/env bash

# GitHub provider authentication. The token needs repository administration
# permissions for repository settings and actions secrets.
export GITHUB_TOKEN="$(gh auth token)"

# R2 S3-compatible backend credentials.
export AWS_ACCESS_KEY_ID="$(op item get 'terraform kkg-pve' --fields label=AWS_ACCESS_KEY_ID --format json | jq -r .value)"
export AWS_SECRET_ACCESS_KEY="$(op item get 'terraform kkg-pve' --fields label=AWS_SECRET_ACCESS_KEY --format json | jq -r .value)"
