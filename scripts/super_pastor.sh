#!/usr/bin/env bash
# scripts/super_pastor.sh
# ─────────────────────────────────────────────────────────────────────────────
# Control the live Super Pastor timed event via the API.
#
# Usage (from the project root):
#   bash scripts/super_pastor.sh [start|stop|status] [reward/host] [port]
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

COMMAND="${1:-status}"
HOST="localhost"
PORT="8000"

# ── Resolve the project root (one level up from this script) ─────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_ROOT}/.env"

# ── Load SECRET_KEY from .env ─────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌  .env file not found at: $ENV_FILE"
  exit 1
fi

SECRET_KEY="$(grep -E '^SECRET_KEY=' "$ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '\"' | tr -d "'")"

if [[ -z "$SECRET_KEY" ]]; then
  echo "❌  SECRET_KEY not found in .env"
  exit 1
fi

case "$COMMAND" in
  start)
    REWARD="${2:-1000}"
    HOST="${3:-localhost}"
    PORT="${4:-8000}"
    URL="http://${HOST}:${PORT}/admin/events/super-pastor/start"
    echo "🌟  Starting Super Pastor Event at ${URL} with reward of ${REWARD} …"
    echo
    RESPONSE=$(curl -s -w "\n%{http_code}" \
      -X POST "${URL}" \
      -H "X-Admin-Key: ${SECRET_KEY}" \
      -H "Content-Type: application/json" \
      -d "{\"reward_amount\": ${REWARD}}")
    ;;
  stop)
    HOST="${2:-localhost}"
    PORT="${3:-8000}"
    URL="http://${HOST}:${PORT}/admin/events/super-pastor/stop"
    echo "🛑  Stopping Super Pastor Event at ${URL} …"
    echo
    RESPONSE=$(curl -s -w "\n%{http_code}" \
      -X POST "${URL}" \
      -H "X-Admin-Key: ${SECRET_KEY}" \
      -H "Content-Type: application/json")
    ;;
  status)
    HOST="${2:-localhost}"
    PORT="${3:-8000}"
    URL="http://${HOST}:${PORT}/admin/events/super-pastor/status"
    echo "🔍  Checking Super Pastor Event status at ${URL} …"
    echo
    RESPONSE=$(curl -s -w "\n%{http_code}" \
      -X GET "${URL}" \
      -H "X-Admin-Key: ${SECRET_KEY}" \
      -H "Accept: application/json")
    ;;
  *)
    echo "❌  Unknown command: $COMMAND"
    echo "Usage: bash scripts/super_pastor.sh [start|stop|status] [reward/host] [port]"
    exit 1
    ;;
esac

HTTP_BODY=$(echo "$RESPONSE" | head -n -1)
HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)

if [[ "$HTTP_CODE" == "200" ]]; then
  echo "✅  Command successful (HTTP ${HTTP_CODE})"
  echo "$HTTP_BODY" | python3 -m json.tool 2>/dev/null || echo "$HTTP_BODY"
else
  echo "❌  Command failed (HTTP ${HTTP_CODE})"
  echo "$HTTP_BODY"
  exit 1
fi
