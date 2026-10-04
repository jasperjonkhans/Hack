#!/usr/bin/env bash
# Apply pending migrations from migrations/ (in filename order) to $ACADEMIC_DB_URL,
# read from the environment or the repository-root .env.
# Each migration runs in its own transaction and is recorded in schema_migrations.
set -eo pipefail

DIR=$(cd "$(dirname "$0")" && pwd)
if [ -z "${ACADEMIC_DB_URL:-}" ]; then
  ACADEMIC_DB_URL=$(grep '^ACADEMIC_DB_URL=' "$DIR/../../.env" | cut -d= -f2-)
fi
[ -n "$ACADEMIC_DB_URL" ] || { echo "ACADEMIC_DB_URL is not set (environment or repo-root .env)"; exit 1; }

PSQL=(psql "$ACADEMIC_DB_URL" -X -q -v ON_ERROR_STOP=1)

"${PSQL[@]}" -c "SET client_min_messages TO warning" -c "CREATE TABLE IF NOT EXISTS schema_migrations (
  version    text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
)"

for f in "$DIR"/migrations/*.sql; do
  version=$(basename "$f" .sql)
  applied=$("${PSQL[@]}" -At -c "SELECT 1 FROM schema_migrations WHERE version = '$version'")
  if [ "$applied" = 1 ]; then
    continue
  fi
  echo "applying $version"
  "${PSQL[@]}" -1 -f "$f" -c "INSERT INTO schema_migrations (version) VALUES ('$version')"
done

echo "up to date"
