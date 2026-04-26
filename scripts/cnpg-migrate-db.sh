#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/cnpg-migrate-db.sh <app> [source-context] [destination-context]

Apps:
  spotify-nowplaying
  spotify-reblend
  sui

Defaults:
  source-context:      kkg@admin
  destination-context: natsume@soli

This runs a one-shot logical migration for a CNPG database:
  1. pg_dump from source primary
  2. pg_restore into destination primary
  3. fix restored object ownership and privileges
  4. print ownership summary

Stop the source and destination application Deployments before running this.
EOF
}

if [[ $# -lt 1 || $# -gt 3 ]]; then
  usage >&2
  exit 2
fi

app="$1"
source_context="${2:-kkg@admin}"
destination_context="${3:-natsume@soli}"

case "$app" in
  spotify-nowplaying)
    namespace="spotify-nowplaying"
    cluster="spn-cluster"
    database="spn"
    owner="spn"
    dump_file="/tmp/spn.dump"
    ;;
  spotify-reblend)
    namespace="spotify-reblend"
    cluster="reblend-cluster"
    database="reblend"
    owner="reblend"
    dump_file="/tmp/reblend.dump"
    ;;
  sui)
    namespace="sui"
    cluster="sui-cluster"
    database="sui"
    owner="sui"
    dump_file="/tmp/sui.dump"
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown app: $app" >&2
    usage >&2
    exit 2
    ;;
esac

get_primary() {
  local context="$1"
  kubectl --context "$context" -n "$namespace" get cluster "$cluster" \
    -o jsonpath='{.status.currentPrimary}'
}

source_primary="$(get_primary "$source_context")"
destination_primary="$(get_primary "$destination_context")"

if [[ -z "$source_primary" ]]; then
  echo "Could not resolve source primary for $namespace/$cluster on $source_context" >&2
  exit 1
fi

if [[ -z "$destination_primary" ]]; then
  echo "Could not resolve destination primary for $namespace/$cluster on $destination_context" >&2
  exit 1
fi

cat <<EOF
CNPG database migration:
  app:                 $app
  namespace:           $namespace
  cluster:             $cluster
  database:            $database
  owner:               $owner
  source context:      $source_context
  source primary:      $source_primary
  destination context: $destination_context
  destination primary: $destination_primary
  dump file:           $dump_file
EOF

echo
echo "Creating dump from source..."
rm -f "$dump_file"
kubectl --context "$source_context" -n "$namespace" exec -c postgres "$source_primary" -- \
  pg_dump -U postgres -d "$database" -Fc \
  > "$dump_file"

if [[ ! -s "$dump_file" ]]; then
  echo "Dump file is empty: $dump_file" >&2
  exit 1
fi

ls -lh "$dump_file"

echo
echo "Restoring dump into destination..."
kubectl --context "$destination_context" -n "$namespace" exec -i -c postgres "$destination_primary" -- \
  pg_restore -U postgres -d "$database" --clean --if-exists --no-owner \
  < "$dump_file"

echo
echo "Fixing restored object ownership and privileges..."
kubectl --context "$destination_context" -n "$namespace" exec -i -c postgres "$destination_primary" -- \
  psql -v ON_ERROR_STOP=1 -U postgres -d "$database" <<SQL
GRANT CONNECT, TEMPORARY ON DATABASE "$database" TO "$owner";
GRANT USAGE, CREATE ON SCHEMA public TO "$owner";
ALTER SCHEMA public OWNER TO "$owner";

DO \$\$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT schemaname, tablename
    FROM pg_tables
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', r.schemaname, r.tablename, '$owner');
    EXECUTE format('GRANT ALL PRIVILEGES ON TABLE %I.%I TO %I', r.schemaname, r.tablename, '$owner');
  END LOOP;

  FOR r IN
    SELECT sequence_schema, sequence_name
    FROM information_schema.sequences
    WHERE sequence_schema = 'public'
  LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', r.sequence_schema, r.sequence_name, '$owner');
    EXECUTE format('GRANT ALL PRIVILEGES ON SEQUENCE %I.%I TO %I', r.sequence_schema, r.sequence_name, '$owner');
  END LOOP;

  FOR r IN
    SELECT schemaname, viewname
    FROM pg_views
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER VIEW %I.%I OWNER TO %I', r.schemaname, r.viewname, '$owner');
    EXECUTE format('GRANT ALL PRIVILEGES ON TABLE %I.%I TO %I', r.schemaname, r.viewname, '$owner');
  END LOOP;

  FOR r IN
    SELECT schemaname, matviewname
    FROM pg_matviews
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', r.schemaname, r.matviewname, '$owner');
    EXECUTE format('GRANT ALL PRIVILEGES ON TABLE %I.%I TO %I', r.schemaname, r.matviewname, '$owner');
  END LOOP;

  FOR r IN
    SELECT n.nspname AS schema_name, t.typname AS type_name
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'public'
      AND t.typtype IN ('b', 'c', 'd', 'e', 'r')
      AND t.typrelid = 0
      AND t.typelem = 0
  LOOP
    EXECUTE format('ALTER TYPE %I.%I OWNER TO %I', r.schema_name, r.type_name, '$owner');
  END LOOP;
END
\$\$;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO "$owner";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO "$owner";
SQL

echo
echo "Ownership summary:"
kubectl --context "$destination_context" -n "$namespace" exec -c postgres "$destination_primary" -- \
  psql -U postgres -d "$database" -c '\dt+'

echo
echo "Migration finished for $app."
