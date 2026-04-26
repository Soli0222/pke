#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/navidrome-migrate-volumes.sh

Environment variables:
  SRC_CONTEXT       Source kube context. Default: kkg@admin
  DST_CONTEXT       Destination kube context. Default: natsume@soli
  NAMESPACE         Namespace on both clusters. Default: navidrome

  SRC_DATA_CLAIM    Source data PVC. Default: navidrome-data-pvc
  SRC_MUSIC_CLAIM   Source music PVC. Default: navidrome-music-pvc
  DST_DATA_CLAIM    Destination data PVC. Default: navidrome-data-pvc
  DST_MUSIC_CLAIM   Destination music PVC. Default: navidrome-music-pvc

  COPY_DATA         Copy data volume. Default: true
  COPY_MUSIC        Copy music volume. Default: true
  CLEAN_DST         Remove destination contents before restore. Default: false
  KEEP_PODS         Keep temporary copy pods after completion. Default: false
  WAIT_TIMEOUT      Pod/PVC wait timeout. Default: 30m

This migrates Navidrome volumes through temporary copy pods. The destination
copy pod acts as the first consumer for WaitForFirstConsumer PVCs.

Before running, keep Navidrome replicas at 0 on both clusters.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -ne 0 ]]; then
  usage >&2
  exit 2
fi

SRC_CONTEXT="${SRC_CONTEXT:-kkg@admin}"
DST_CONTEXT="${DST_CONTEXT:-natsume@soli}"
NAMESPACE="${NAMESPACE:-navidrome}"

SRC_DATA_CLAIM="${SRC_DATA_CLAIM:-navidrome-data-pvc}"
SRC_MUSIC_CLAIM="${SRC_MUSIC_CLAIM:-navidrome-music-pvc}"
DST_DATA_CLAIM="${DST_DATA_CLAIM:-navidrome-data-pvc}"
DST_MUSIC_CLAIM="${DST_MUSIC_CLAIM:-navidrome-music-pvc}"

COPY_DATA="${COPY_DATA:-true}"
COPY_MUSIC="${COPY_MUSIC:-true}"
CLEAN_DST="${CLEAN_DST:-false}"
KEEP_PODS="${KEEP_PODS:-false}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-30m}"

SRC_POD="navidrome-copy-src"
DST_POD="navidrome-copy-dst"

is_true() {
  case "$1" in
    true|TRUE|1|yes|YES|y|Y) return 0 ;;
    false|FALSE|0|no|NO|n|N) return 1 ;;
    *)
      echo "Invalid boolean value: $1" >&2
      exit 2
      ;;
  esac
}

need_claim() {
  local context="$1"
  local claim="$2"
  kubectl --context "$context" -n "$NAMESPACE" get pvc "$claim" >/dev/null
}

delete_copy_pods() {
  kubectl --context "$SRC_CONTEXT" -n "$NAMESPACE" delete pod "$SRC_POD" --ignore-not-found=true --wait=true
  kubectl --context "$DST_CONTEXT" -n "$NAMESPACE" delete pod "$DST_POD" --ignore-not-found=true --wait=true
}

create_source_pod() {
  kubectl --context "$SRC_CONTEXT" -n "$NAMESPACE" apply -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: ${SRC_POD}
spec:
  restartPolicy: Never
  containers:
    - name: copy
      image: alpine:3.20
      command: ["sh", "-c", "sleep infinity"]
      volumeMounts:
        - name: data
          mountPath: /src/data
          readOnly: true
        - name: music
          mountPath: /src/music
          readOnly: true
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: ${SRC_DATA_CLAIM}
    - name: music
      persistentVolumeClaim:
        claimName: ${SRC_MUSIC_CLAIM}
YAML
}

create_destination_pod() {
  kubectl --context "$DST_CONTEXT" -n "$NAMESPACE" apply -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: ${DST_POD}
spec:
  restartPolicy: Never
  containers:
    - name: copy
      image: alpine:3.20
      command: ["sh", "-c", "sleep infinity"]
      volumeMounts:
        - name: data
          mountPath: /dst/data
        - name: music
          mountPath: /dst/music
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: ${DST_DATA_CLAIM}
    - name: music
      persistentVolumeClaim:
        claimName: ${DST_MUSIC_CLAIM}
YAML
}

wait_pod_ready() {
  local context="$1"
  local pod="$2"

  if ! kubectl --context "$context" -n "$NAMESPACE" wait "pod/$pod" \
    --for=condition=Ready --timeout="$WAIT_TIMEOUT"; then
    echo
    echo "Pod did not become Ready: $context $NAMESPACE/$pod" >&2
    kubectl --context "$context" -n "$NAMESPACE" get pod "$pod" -o wide >&2 || true
    kubectl --context "$context" -n "$NAMESPACE" describe pod "$pod" >&2 || true
    exit 1
  fi
}

clean_destination_path() {
  local path="$1"

  if is_true "$CLEAN_DST"; then
    kubectl --context "$DST_CONTEXT" -n "$NAMESPACE" exec "$DST_POD" -- \
      sh -c "find '$path' -mindepth 1 -maxdepth 1 -exec rm -rf {} +"
  fi
}

copy_path() {
  local name="$1"
  local src_path="$2"
  local dst_path="$3"

  echo
  echo "Copying $name..."
  clean_destination_path "$dst_path"

  kubectl --context "$SRC_CONTEXT" -n "$NAMESPACE" exec "$SRC_POD" -- \
    tar -C "$src_path" -cf - . | \
  kubectl --context "$DST_CONTEXT" -n "$NAMESPACE" exec -i "$DST_POD" -- \
    tar -C "$dst_path" -xf -
}

print_summary() {
  echo
  echo "Destination volume summary:"
  kubectl --context "$DST_CONTEXT" -n "$NAMESPACE" exec "$DST_POD" -- \
    sh -c 'du -sh /dst/data /dst/music; printf "music files: "; find /dst/music -type f | wc -l'
}

cat <<EOF
Navidrome volume migration:
  namespace:        $NAMESPACE
  source context:   $SRC_CONTEXT
  source data PVC:  $SRC_DATA_CLAIM
  source music PVC: $SRC_MUSIC_CLAIM
  dest context:     $DST_CONTEXT
  dest data PVC:    $DST_DATA_CLAIM
  dest music PVC:   $DST_MUSIC_CLAIM
  copy data:        $COPY_DATA
  copy music:       $COPY_MUSIC
  clean dest:       $CLEAN_DST
  keep pods:        $KEEP_PODS
EOF

if ! is_true "$COPY_DATA" && ! is_true "$COPY_MUSIC"; then
  echo "Nothing to copy because COPY_DATA and COPY_MUSIC are both false" >&2
  exit 2
fi

echo
echo "Checking PVCs..."
need_claim "$SRC_CONTEXT" "$SRC_DATA_CLAIM"
need_claim "$SRC_CONTEXT" "$SRC_MUSIC_CLAIM"
need_claim "$DST_CONTEXT" "$DST_DATA_CLAIM"
need_claim "$DST_CONTEXT" "$DST_MUSIC_CLAIM"

echo
echo "Recreating temporary copy pods..."
delete_copy_pods
create_destination_pod
create_source_pod

echo
echo "Waiting for copy pods. The destination pod is the first consumer for WaitForFirstConsumer PVCs..."
wait_pod_ready "$DST_CONTEXT" "$DST_POD"
wait_pod_ready "$SRC_CONTEXT" "$SRC_POD"

echo
echo "Current PVC state:"
kubectl --context "$DST_CONTEXT" -n "$NAMESPACE" get pvc "$DST_DATA_CLAIM" "$DST_MUSIC_CLAIM" -o wide

if is_true "$COPY_DATA"; then
  copy_path "data" /src/data /dst/data
fi

if is_true "$COPY_MUSIC"; then
  copy_path "music" /src/music /dst/music
fi

print_summary

if is_true "$KEEP_PODS"; then
  echo
  echo "Keeping temporary pods:"
  echo "  source:      $SRC_CONTEXT $NAMESPACE/$SRC_POD"
  echo "  destination: $DST_CONTEXT $NAMESPACE/$DST_POD"
else
  echo
  echo "Cleaning up temporary copy pods..."
  delete_copy_pods
fi

echo
echo "Migration finished. Start Navidrome on $DST_CONTEXT after checking the summary."
