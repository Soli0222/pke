#!/usr/bin/env bash
# source ./setup.sh (bash / zsh). Export only after every credential is valid.
case $- in *x*) set +x ;; esac

_pke_github_setup() {
  local github_token r2_item access_key secret_key
  github_token="${GITHUB_TOKEN:-}"
  access_key="${AWS_ACCESS_KEY_ID:-}"
  secret_key="${AWS_SECRET_ACCESS_KEY:-}"

  if [[ -z "$github_token" ]]; then
    github_token="$(gh auth token)" || return 1
  fi
  if [[ -z "$access_key" || -z "$secret_key" ]]; then
    r2_item="$(op item get 'terraform kkg-pve' --format json)" || return 1
    access_key="$(jq -er '.fields[] | select(.label == "AWS_ACCESS_KEY_ID") | .value | select(type == "string" and length > 0)' <<<"$r2_item")" || return 1
    secret_key="$(jq -er '.fields[] | select(.label == "AWS_SECRET_ACCESS_KEY") | .value | select(type == "string" and length > 0)' <<<"$r2_item")" || return 1
  fi
  [[ -n "$github_token" && -n "$access_key" && -n "$secret_key" ]] || return 1
  export GITHUB_TOKEN="$github_token"
  export AWS_ACCESS_KEY_ID="$access_key"
  export AWS_SECRET_ACCESS_KEY="$secret_key"
}

if _pke_github_setup; then
  unset -f _pke_github_setup
else
  unset -f _pke_github_setup
  echo 'GitHub/R2 authentication failed; credentials were not changed.' >&2
  return 1 2>/dev/null || exit 1
fi
