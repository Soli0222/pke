#!/usr/bin/env bash
set -euo pipefail

STORAGE_CLASS="${STORAGE_CLASS:-longhorn}"
COPY_IMAGE="${COPY_IMAGE:-alpine:3.20}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-120s}"
ASSUME_YES=false

usage() {
  cat <<'EOF'
Usage:
  scripts/migrate-longhorn-pvcs.sh [--yes] <grafana|uptime-kuma|navidrome|all>

Environment:
  STORAGE_CLASS  Target StorageClass. Default: longhorn
  COPY_IMAGE     Copy pod image. Default: alpine:3.20
  WAIT_TIMEOUT   kubectl wait timeout. Default: 120s

This script:
  - suspends the HelmRelease
  - scales the deployment to 0
  - copies data to temporary Longhorn PVCs
  - recreates the original PVC names on Longhorn
  - copies data back
  - resumes the HelmRelease
  - scales the deployment to 1
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

run() {
  log "+ $*"
  "$@"
}

confirm() {
  if [[ "${ASSUME_YES}" == "true" ]]; then
    return
  fi

  printf 'This will delete and recreate PVCs for "%s". Continue? [y/N] ' "$1"
  read -r answer
  case "${answer}" in
    y|Y|yes|YES) ;;
    *) echo "aborted"; exit 1 ;;
  esac
}

require_tools() {
  local missing=()
  for tool in kubectl flux; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
      missing+=("${tool}")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    printf 'missing required tools: %s\n' "${missing[*]}" >&2
    exit 1
  fi
}

retain_current_pv() {
  local namespace="$1"
  local claim="$2"
  local pv

  pv="$(kubectl -n "${namespace}" get pvc "${claim}" -o jsonpath='{.spec.volumeName}')"
  if [[ -z "${pv}" ]]; then
    printf 'PVC %s/%s has no bound PV\n' "${namespace}" "${claim}" >&2
    exit 1
  fi

  run kubectl patch pv "${pv}" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
}

delete_copy_pod() {
  local namespace="$1"
  local pod="$2"

  kubectl -n "${namespace}" delete pod "${pod}" --ignore-not-found=true >/dev/null
}

create_pvc() {
  local namespace="$1"
  local name="$2"
  local size="$3"
  local release="$4"
  local helm_owned="$5"

  if [[ "${helm_owned}" == "true" ]]; then
    kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${name}
  namespace: ${namespace}
  labels:
    app.kubernetes.io/managed-by: Helm
  annotations:
    meta.helm.sh/release-name: ${release}
    meta.helm.sh/release-namespace: ${namespace}
spec:
  accessModes:
  - ReadWriteOnce
  storageClassName: ${STORAGE_CLASS}
  resources:
    requests:
      storage: ${size}
EOF
  else
    kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${name}
  namespace: ${namespace}
spec:
  accessModes:
  - ReadWriteOnce
  storageClassName: ${STORAGE_CLASS}
  resources:
    requests:
      storage: ${size}
EOF
  fi
}

copy_between_claims() {
  local namespace="$1"
  local pod="$2"
  local source_claim="$3"
  local dest_claim="$4"

  delete_copy_pod "${namespace}" "${pod}"
  kubectl -n "${namespace}" apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${pod}
spec:
  restartPolicy: Never
  containers:
  - name: copy
    image: ${COPY_IMAGE}
    command: ["sh", "-c", "sleep 3600"]
    volumeMounts:
    - name: old
      mountPath: /old
      readOnly: true
    - name: new
      mountPath: /new
  volumes:
  - name: old
    persistentVolumeClaim:
      claimName: ${source_claim}
      readOnly: true
  - name: new
    persistentVolumeClaim:
      claimName: ${dest_claim}
EOF
  run kubectl -n "${namespace}" wait --for=condition=Ready "pod/${pod}" "--timeout=${WAIT_TIMEOUT}"
  run kubectl -n "${namespace}" exec "${pod}" -- sh -c 'cd /old && tar cf - . | tar xf - -C /new && sync'
  run kubectl -n "${namespace}" delete pod "${pod}"
}

migrate_one_claim() {
  local namespace="$1"
  local release="$2"
  local claim="$3"
  local copy_claim="$4"
  local size="$5"

  retain_current_pv "${namespace}" "${claim}"
  create_pvc "${namespace}" "${copy_claim}" "${size}" "${release}" false
  copy_between_claims "${namespace}" "${claim}-copy-to-longhorn" "${claim}" "${copy_claim}"
}

restore_one_claim() {
  local namespace="$1"
  local release="$2"
  local claim="$3"
  local copy_claim="$4"
  local size="$5"

  create_pvc "${namespace}" "${claim}" "${size}" "${release}" true
  copy_between_claims "${namespace}" "${claim}-copy-to-original" "${copy_claim}" "${claim}"
  run kubectl -n "${namespace}" delete pvc "${copy_claim}"
}

finish_workload() {
  local namespace="$1"
  local release="$2"
  local deployment="$3"

  run flux resume helmrelease -n "${namespace}" "${release}"
  run kubectl -n "${namespace}" scale deployment "${deployment}" --replicas=1
  run kubectl -n "${namespace}" get pvc -o wide
  run kubectl -n "${namespace}" get pods -o wide
}

migrate_app() {
  local namespace="$1"
  local release="$2"
  local deployment="$3"
  shift 3
  local claims=("$@")

  log "Migrating ${namespace}/${release}"
  confirm "${namespace}/${release}"

  run flux suspend helmrelease -n "${namespace}" "${release}"
  run kubectl -n "${namespace}" scale deployment "${deployment}" --replicas=0

  local entry claim copy_claim size
  for entry in "${claims[@]}"; do
    IFS=: read -r claim copy_claim size <<<"${entry}"
    migrate_one_claim "${namespace}" "${release}" "${claim}" "${copy_claim}" "${size}"
  done

  local delete_args=()
  for entry in "${claims[@]}"; do
    IFS=: read -r claim copy_claim size <<<"${entry}"
    delete_args+=("${claim}")
  done
  run kubectl -n "${namespace}" delete pvc "${delete_args[@]}"

  for entry in "${claims[@]}"; do
    IFS=: read -r claim copy_claim size <<<"${entry}"
    restore_one_claim "${namespace}" "${release}" "${claim}" "${copy_claim}" "${size}"
  done

  finish_workload "${namespace}" "${release}" "${deployment}"
}

migrate_grafana() {
  migrate_app grafana grafana grafana \
    grafana:grafana-longhorn-copy:10Gi
}

migrate_uptime_kuma() {
  migrate_app uptime-kuma uptime-kuma uptime-kuma \
    uptime-kuma-pvc:uptime-kuma-longhorn-copy:4Gi
}

migrate_navidrome() {
  migrate_app navidrome navidrome navidrome \
    navidrome-data-pvc:navidrome-data-longhorn-copy:10Gi \
    navidrome-music-pvc:navidrome-music-longhorn-copy:100Gi
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
        if [[ -n "${target}" ]]; then
          printf 'unexpected argument: %s\n' "$1" >&2
          usage >&2
          exit 1
        fi
        target="$1"
        shift
        ;;
    esac
  done

  if [[ -z "${target}" ]]; then
    usage >&2
    exit 1
  fi

  require_tools

  case "${target}" in
    grafana) migrate_grafana ;;
    uptime-kuma) migrate_uptime_kuma ;;
    navidrome) migrate_navidrome ;;
    all)
      migrate_grafana
      migrate_uptime_kuma
      migrate_navidrome
      ;;
    *)
      printf 'unknown target: %s\n' "${target}" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
