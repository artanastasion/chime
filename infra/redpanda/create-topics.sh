#!/bin/sh
set -eu

BROKERS="${KAFKA_BROKERS:-redpanda:9092}"

create_topic() {
  name="$1"
  partitions="$2"
  retention_ms="$3"
  if rpk topic describe "$name" -X brokers="$BROKERS" >/dev/null 2>&1; then
    echo "topic $name exists"
  else
    rpk topic create "$name" -X brokers="$BROKERS" \
      --partitions "$partitions" \
      --replicas 1 \
      --topic-config retention.ms="$retention_ms"
  fi
}

WEEK_MS=604800000

for table in users artists albums tracks track_artists storage_objects media_assets; do
  create_topic "chime.public.$table" 1 "$WEEK_MS"
done

create_topic chime.api.access-log 3 "$WEEK_MS"
create_topic chime.app.events 3 "$WEEK_MS"
