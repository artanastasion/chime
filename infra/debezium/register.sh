#!/bin/sh
set -eu

CONNECTOR=chime-catalog

until curl -fsS "$CONNECT_URL/connectors" >/dev/null; do
  echo "waiting for Kafka Connect at $CONNECT_URL"
  sleep 2
done

curl -fsS -X PUT \
  -H "Content-Type: application/json" \
  --data @/init/catalog-connector.json \
  "$CONNECT_URL/connectors/$CONNECTOR/config"
echo

for _ in $(seq 1 30); do
  STATE=$(curl -fsS "$CONNECT_URL/connectors/$CONNECTOR/status" | grep -o '"state":"[A-Z]*"' | head -n1 || true)
  echo "connector $CONNECTOR $STATE"
  case "$STATE" in
    *RUNNING*) exit 0 ;;
    *FAILED*) curl -fsS "$CONNECT_URL/connectors/$CONNECTOR/status"; exit 1 ;;
  esac
  sleep 2
done

echo "connector $CONNECTOR did not reach RUNNING state" >&2
exit 1
