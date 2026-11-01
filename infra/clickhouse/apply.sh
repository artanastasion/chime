#!/usr/bin/env bash
set -euo pipefail

for migration in /migrations/sql/*.sql; do
  echo "applying $(basename "$migration")"
  clickhouse-client \
    --host clickhouse \
    --user "$CLICKHOUSE_USER" \
    --password "$CLICKHOUSE_PASSWORD" \
    --database "$CLICKHOUSE_DB" \
    --multiquery \
    --queries-file "$migration"
done

echo "clickhouse schema is up to date"
