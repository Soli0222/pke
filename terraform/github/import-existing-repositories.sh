#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

check_only=false

case "${1:-}" in
  "") ;;
  --check) check_only=true ;;
  *)
    echo "usage: $0 [--check]" >&2
    exit 2
    ;;
esac

state_addresses="$(terraform state list)"
missing_count=0

state_has() {
  grep -Fqx -- "$1" <<<"${state_addresses}"
}

remember_state() {
  state_addresses="${state_addresses}"$'\n'"$1"
}

while IFS=$'\t' read -r repository archived; do
  if [[ "${archived}" == "true" ]]; then
    repository_address="github_repository.archived[\"${repository}\"]"
  else
    repository_address="github_repository.repositories[\"${repository}\"]"
  fi

  if state_has "${repository_address}"; then
    echo "repository already imported: ${repository}"
  elif [[ "${check_only}" == "true" ]]; then
    echo "repository missing: ${repository}"
    missing_count=$((missing_count + 1))
  else
    terraform import -parallelism=1 "${repository_address}" "${repository}"
    remember_state "${repository_address}"
    echo "repository imported: ${repository}"
  fi

  if [[ "${archived}" == "true" ]]; then
    continue
  fi

  branch_address="github_branch_default.repositories[\"${repository}\"]"

  if state_has "${branch_address}"; then
    echo "default branch already imported: ${repository}"
  elif [[ "${check_only}" == "true" ]]; then
    echo "default branch missing: ${repository}"
    missing_count=$((missing_count + 1))
  else
    terraform import -parallelism=1 "${branch_address}" "${repository}"
    remember_state "${branch_address}"
    echo "default branch imported: ${repository}"
  fi
done < <(
  ruby -ryaml -e '
    repositories = YAML.load_file("repositories.yaml").fetch("repositories")
    repositories.sort_by { |name, _| name.downcase }.each do |name, settings|
      puts [name, settings.fetch("archived", false)].join("\t")
    end
  '
)

if [[ "${check_only}" == "true" ]]; then
  echo "missing resources: ${missing_count}"
fi
