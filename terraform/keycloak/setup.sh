#!/usr/bin/env bash
# source ./setup.sh。読み込み失敗時は途中の認証情報で処理を続けない。
_pke_keycloak_setup() {
  local kc_user kc_password kc_cert kc_key sui_password r2_id r2_secret
  kc_user=$(op read 'op://Kubernetes/terraform keycloak/username') || return
  kc_password=$(op read 'op://Kubernetes/terraform keycloak/password') || return
  kc_cert=$(op read 'op://Kubernetes/terraform keycloak/client-certificate') || return
  kc_key=$(op read 'op://Kubernetes/terraform keycloak/client-private-key') || return
  sui_password=$(op read 'op://Kubernetes/terraform keycloak/sui-initial-password') || return
  r2_id=$(op item get 'terraform kkg-pve' --fields label=AWS_ACCESS_KEY_ID --format json | jq -er .value) || return
  r2_secret=$(op item get 'terraform kkg-pve' --fields label=AWS_SECRET_ACCESS_KEY --format json | jq -er .value) || return
  export KEYCLOAK_CLIENT_ID=admin-cli KEYCLOAK_REALM=master
  export KEYCLOAK_USER="$kc_user" KEYCLOAK_PASSWORD="$kc_password"
  export TF_VAR_tls_client_certificate="$kc_cert" TF_VAR_tls_client_private_key="$kc_key"
  export TF_VAR_sui_initial_password="$sui_password"
  export AWS_ACCESS_KEY_ID="$r2_id" AWS_SECRET_ACCESS_KEY="$r2_secret"
}
_pke_keycloak_setup
