#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/cnpg-fix-restored-owner.sh <app> [context]

Apps:
  spotify-nowplaying
  spotify-reblend
  sui

Default context:
  natsume@soli

This fixes ownership and privileges after restoring a CNPG database with
pg_restore -U postgres --no-owner.
EOF
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage >&2
  exit 2
fi

app="$1"
context="${2:-natsume@soli}"

case "$app" in
  spotify-nowplaying)
    namespace="spotify-nowplaying"
    cluster="spn-cluster"
    database="spn"
    owner="spn"
    ;;
  spotify-reblend)
    namespace="spotify-reblend"
    cluster="reblend-cluster"
    database="reblend"
    owner="reblend"
    ;;
  sui)
    namespace="sui"
    cluster="sui-cluster"
    database="sui"
    owner="sui"
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

primary="$(
  kubectl --context "$context" -n "$namespace" get cluster "$cluster" \
    -o jsonpath='{.status.currentPrimary}'
)"

if [[ -z "$primary" ]]; then
  echo "Could not resolve current primary for $namespace/$cluster on $context" >&2
  exit 1
fi

echo "Fixing restored object ownership:"
echo "  context:   $context"
echo "  namespace: $namespace"
echo "  primary:   $primary"
echo "  database:  $database"
echo "  owner:     $owner"

kubectl --context "$context" -n "$namespace" exec -i -c postgres "$primary" -- \
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
kubectl --context "$context" -n "$namespace" exec -c postgres "$primary" -- \
  psql -U postgres -d "$database" -c '\dt+'
