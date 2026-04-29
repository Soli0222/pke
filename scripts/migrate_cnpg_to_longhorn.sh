#!/usr/bin/env bash
set -euo pipefail

STORAGE_CLASS="${STORAGE_CLASS:-longhorn}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-600s}"
ASSUME_YES=false

usage() {
  cat <<'EOF'
Usage:
  scripts/migrate_cnpg_to_longhorn.sh [--yes] <spotify-reblend|spotify-nowplaying|sui|misskey|all>
  scripts/migrate_cnpg_to_longhorn.sh [--yes] custom <namespace> <cluster> <final-instances> [flux-kustomization]

Environment:
  STORAGE_CLASS  Target StorageClass to promote from. Default: longhorn
  WAIT_TIMEOUT   kubectl wait timeout. Default: 600s

This script:
  - takes a CNPG backup
  - suspends the Flux Kustomization
  - scales the cluster to final-instances + 1
  - waits for a STORAGE_CLASS-backed PVC to become Bound
  - waits for the matching CNPG instance pod to become Ready
  - promotes that instance
  - waits for that instance to become the current primary
  - destroys old non-STORAGE_CLASS instances and their PVCs
  - scales the cluster to the requested final instance count
  - resumes the Flux Kustomization
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >&2
}

run() {
  log "+ $*"
  "$@"
}

confirm() {
  if [[ "${ASSUME_YES}" == "true" ]]; then
    return
  fi

  printf 'This will promote a Longhorn-backed CNPG instance for "%s/%s". Continue? [y/N] ' "$1" "$2"
  read -r answer
  case "${answer}" in
    y|Y|yes|YES) ;;
    *) echo "aborted"; exit 1 ;;
  esac
}

require_tools() {
  local missing=()
  for tool in kubectl flux jq; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
      missing+=("${tool}")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    printf 'missing required tools: %s\n' "${missing[*]}" >&2
    exit 1
  fi

  if ! kubectl cnpg --help >/dev/null 2>&1; then
    printf 'kubectl cnpg plugin is required\n' >&2
    exit 1
  fi
}

patch_instances() {
  local namespace="$1"
  local cluster="$2"
  local instances="$3"

  run kubectl -n "${namespace}" patch cluster "${cluster}" \
    --type=merge \
    -p "{\"spec\":{\"instances\":${instances}}}"
}

find_storage_instance() {
  local namespace="$1"
  local cluster="$2"

  kubectl -n "${namespace}" get pvc -o json \
    | jq -r --arg cluster "${cluster}" --arg storage_class "${STORAGE_CLASS}" '
        .items[]
        | select(.metadata.labels["cnpg.io/cluster"] == $cluster)
        | select(.spec.storageClassName == $storage_class)
        | select(.status.phase == "Bound")
        | .metadata.labels["cnpg.io/instanceName"]
      ' \
    | grep -v '^null$' \
    | head -n 1 \
    || true
}

find_old_storage_instances() {
  local namespace="$1"
  local cluster="$2"
  local current_primary="$3"

  kubectl -n "${namespace}" get pvc -o json \
    | jq -r --arg cluster "${cluster}" --arg storage_class "${STORAGE_CLASS}" --arg current_primary "${current_primary}" '
        .items[]
        | select(.metadata.labels["cnpg.io/cluster"] == $cluster)
        | select(.spec.storageClassName != $storage_class)
        | .metadata.labels["cnpg.io/instanceName"]
        | select(. != null and . != $current_primary)
      ' \
    | sort -u
}

timeout_seconds() {
  case "${WAIT_TIMEOUT}" in
    *s) printf '%s\n' "${WAIT_TIMEOUT%s}" ;;
    *m) printf '%s\n' "$(( ${WAIT_TIMEOUT%m} * 60 ))" ;;
    *) printf '%s\n' "${WAIT_TIMEOUT}" ;;
  esac
}

wait_storage_instance_ready() {
  local namespace="$1"
  local cluster="$2"
  local deadline
  local instance=""

  deadline=$(( SECONDS + $(timeout_seconds) ))
  log "Waiting for ${STORAGE_CLASS}-backed instance pod for ${namespace}/${cluster}"

  while (( SECONDS < deadline )); do
    instance="$(find_storage_instance "${namespace}" "${cluster}")"
    if [[ -n "${instance}" ]] && kubectl -n "${namespace}" get pod "${instance}" >/dev/null 2>&1; then
      log "+ kubectl -n ${namespace} wait pod/${instance} --for=condition=Ready --timeout=${WAIT_TIMEOUT}"
      kubectl -n "${namespace}" wait "pod/${instance}" \
        --for=condition=Ready \
        "--timeout=${WAIT_TIMEOUT}" >&2
      printf '%s\n' "${instance}"
      return
    fi

    sleep 5
  done

  printf 'timed out waiting for a Bound %s PVC and matching instance pod for %s/%s\n' \
    "${STORAGE_CLASS}" "${namespace}" "${cluster}" >&2
  exit 1
}

wait_current_primary() {
  local namespace="$1"
  local cluster="$2"
  local instance="$3"
  local deadline
  local current_primary=""
  local phase=""

  deadline=$(( SECONDS + $(timeout_seconds) ))
  log "Waiting for ${namespace}/${cluster} current primary to become ${instance}"

  while (( SECONDS < deadline )); do
    current_primary="$(
      kubectl -n "${namespace}" get cluster "${cluster}" -o json \
        | jq -r '.status.currentPrimary // ""'
    )"
    phase="$(
      kubectl -n "${namespace}" get cluster "${cluster}" -o json \
        | jq -r '.status.phase // ""'
    )"

    if [[ "${current_primary}" == "${instance}" ]]; then
      log "Current primary is ${instance}; phase=${phase}"
      return
    fi

    sleep 5
  done

  printf 'timed out waiting for %s/%s current primary to become %s\n' \
    "${namespace}" "${cluster}" "${instance}" >&2
  exit 1
}

wait_ready_instances() {
  local namespace="$1"
  local cluster="$2"
  local expected="$3"
  local deadline
  local ready_instances=""

  deadline=$(( SECONDS + $(timeout_seconds) ))
  log "Waiting for ${namespace}/${cluster} ready instances to become ${expected}"

  while (( SECONDS < deadline )); do
    ready_instances="$(
      kubectl -n "${namespace}" get cluster "${cluster}" -o json \
        | jq -r '.status.readyInstances // 0 | tostring'
    )"

    if [[ "${ready_instances}" == "${expected}" ]]; then
      return
    fi

    sleep 5
  done

  printf 'timed out waiting for %s/%s ready instances to become %s\n' \
    "${namespace}" "${cluster}" "${expected}" >&2
  exit 1
}

wait_no_old_storage_instances() {
  local namespace="$1"
  local cluster="$2"
  local current_primary="$3"
  local deadline

  deadline=$(( SECONDS + $(timeout_seconds) ))
  log "Waiting for old non-${STORAGE_CLASS} PVCs to be removed for ${namespace}/${cluster}"

  while (( SECONDS < deadline )); do
    if [[ -z "$(find_old_storage_instances "${namespace}" "${cluster}" "${current_primary}")" ]]; then
      return
    fi

    sleep 5
  done

  printf 'timed out waiting for old non-%s PVCs to be removed for %s/%s\n' \
    "${STORAGE_CLASS}" "${namespace}" "${cluster}" >&2
  exit 1
}

destroy_old_storage_instances() {
  local namespace="$1"
  local cluster="$2"
  local current_primary="$3"
  local old_instances
  local instance

  old_instances="$(find_old_storage_instances "${namespace}" "${cluster}" "${current_primary}")"
  if [[ -z "${old_instances}" ]]; then
    return
  fi

  while IFS= read -r instance; do
    [[ -n "${instance}" ]] || continue
    run kubectl cnpg destroy -n "${namespace}" "${cluster}" "${instance}"
  done <<<"${old_instances}"

  wait_no_old_storage_instances "${namespace}" "${cluster}" "${current_primary}"
}

show_state() {
  local namespace="$1"
  local cluster="$2"

  run kubectl cnpg status -n "${namespace}" "${cluster}"
  run kubectl -n "${namespace}" get pods -o wide
  run kubectl -n "${namespace}" get pvc -o wide
}

suspend_flux_kustomization() {
  local kustomization="$1"

  run flux suspend kustomization -n flux-system "${kustomization}"
}

resume_flux_kustomization() {
  local kustomization="$1"

  run flux resume kustomization -n flux-system "${kustomization}"
}

migrate_cnpg_to_longhorn() {
  local namespace="$1"
  local cluster="$2"
  local final_instances="$3"
  local flux_kustomization="$4"
  local longhorn_instance
  local temporary_instances

  log "Migrating CNPG cluster ${namespace}/${cluster}; final instances=${final_instances}; flux kustomization=${flux_kustomization}"
  confirm "${namespace}" "${cluster}"

  suspend_flux_kustomization "${flux_kustomization}"

  run kubectl cnpg backup -n "${namespace}" "${cluster}"
  run kubectl cnpg status -n "${namespace}" "${cluster}"

  temporary_instances=$(( final_instances + 1 ))
  patch_instances "${namespace}" "${cluster}" "${temporary_instances}"
  show_state "${namespace}" "${cluster}"

  longhorn_instance="$(wait_storage_instance_ready "${namespace}" "${cluster}")"

  log "Promoting ${namespace}/${cluster} instance ${longhorn_instance}"
  run kubectl cnpg promote -n "${namespace}" "${cluster}" "${longhorn_instance}"
  wait_current_primary "${namespace}" "${cluster}" "${longhorn_instance}"
  show_state "${namespace}" "${cluster}"

  destroy_old_storage_instances "${namespace}" "${cluster}" "${longhorn_instance}"

  patch_instances "${namespace}" "${cluster}" "${final_instances}"
  wait_ready_instances "${namespace}" "${cluster}" "${final_instances}"

  show_state "${namespace}" "${cluster}"
  resume_flux_kustomization "${flux_kustomization}"
}

migrate_preset() {
  case "$1" in
    spotify-reblend)
      migrate_cnpg_to_longhorn spotify-reblend reblend-cluster 1 spotify-reblend
      ;;
    spotify-nowplaying)
      migrate_cnpg_to_longhorn spotify-nowplaying spn-cluster 1 spotify-nowplaying
      ;;
    sui)
      migrate_cnpg_to_longhorn sui sui-cluster 1 sui
      ;;
    misskey)
      migrate_cnpg_to_longhorn misskey misskey-cluster 2 misskey
      ;;
    *)
      printf 'unknown target: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main() {
  local target=""

  while (( $# > 0 )); do
    case "$1" in
      --yes|-y)
        ASSUME_YES=true
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      -*)
        printf 'unknown option: %s\n' "$1" >&2
        usage >&2
        exit 1
        ;;
      *)
        target="$1"
        shift
        break
        ;;
    esac
  done

  if [[ -z "${target}" ]]; then
    usage >&2
    exit 1
  fi

  require_tools

  case "${target}" in
    all)
      migrate_preset spotify-reblend
      migrate_preset spotify-nowplaying
      migrate_preset sui
      ;;
    custom)
      if (( $# != 3 && $# != 4 )); then
        usage >&2
        exit 1
      fi
      migrate_cnpg_to_longhorn "$1" "$2" "$3" "${4:-$1}"
      ;;
    *)
      if (( $# != 0 )); then
        printf 'unexpected arguments: %s\n' "$*" >&2
        usage >&2
        exit 1
      fi
      migrate_preset "${target}"
      ;;
  esac
}

main "$@"
