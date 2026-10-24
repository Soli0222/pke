#!/usr/bin/env bash
# source ./setup.sh: 恒久サービスアカウントで認証する。
_pke_keycloak_setup() {
  local mode="${1:-service-account}"
  local kc_secret kc_cert kc_key soli_password r2_id r2_secret
  case "$mode" in
    service-account) ;;
    *)
      printf '%s\n' 'Usage: source ./setup.sh [service-account]' >&2
      return 1
      ;;
  esac
  kc_secret=$(op read 'op://Kubernetes/terraform keycloak/client-secret') || return
  kc_cert=$(op read 'op://Kubernetes/pke_natsume_mtls/tls.crt') || return
  kc_key=$(op read 'op://Kubernetes/pke_natsume_mtls/tls.key') || return
  soli_password=$(op read 'op://Kubernetes/terraform keycloak/soli-initial-password') || return
  r2_id=$(op item get 'terraform kkg-pve' --fields label=AWS_ACCESS_KEY_ID --format json | jq -er .value) || return
  r2_secret=$(op item get 'terraform kkg-pve' --fields label=AWS_SECRET_ACCESS_KEY --format json | jq -er .value) || return

  # 以前のユーザー認証情報を provider が併用しないよう、取得成功後に切り替える。
  unset KEYCLOAK_USER KEYCLOAK_PASSWORD KEYCLOAK_CLIENT_SECRET
  export KEYCLOAK_REALM=master
  export KEYCLOAK_CLIENT_ID=terraform KEYCLOAK_CLIENT_SECRET="$kc_secret"
  export TF_VAR_terraform_client_secret="$kc_secret" TF_VAR_soli_initial_password="$soli_password"
  export TF_VAR_tls_client_certificate="$kc_cert" TF_VAR_tls_client_private_key="$kc_key"
  export AWS_ACCESS_KEY_ID="$r2_id" AWS_SECRET_ACCESS_KEY="$r2_secret"
}
_pke_keycloak_setup "$@"
