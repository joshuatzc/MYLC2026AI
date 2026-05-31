#!/usr/bin/env bash
# scripts/corruption.sh
# ─────────────────────────────────────────────────────────────────────────────
# Corruption of Leaders quiz event control.
#
# Usage (from server, SSH'd in):
#   bash scripts/corruption.sh start  [host] [port]
#   bash scripts/corruption.sh stop   [host] [port]
#   bash scripts/corruption.sh status [host] [port]
#
# Examples:
#   bash scripts/corruption.sh start
#   bash scripts/corruption.sh status
#   bash scripts/corruption.sh stop
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

COMMAND="${1:-}"
shift || true

# Load SECRET_KEY from .env
ENV_FILE="$(dirname "$0")/../.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

if [[ -z "${SECRET_KEY:-}" ]]; then
  echo "❌  SECRET_KEY not found in .env" >&2
  exit 1
fi

case "$COMMAND" in
  start)
    HOST="${1:-localhost}"
    PORT="${2:-8000}"
    echo "📜  Starting Corruption of Leaders quiz event..."
    echo "    12 CAC history questions | 20-minute event window"
    echo "    +10% per correct answer  | -5% per wrong answer or timeout (sequential)"
    echo "    20 seconds per question  | Incomplete groups penalised for all unanswered"
    echo

    curl -s -X POST "http://${HOST}:${PORT}/admin/events/corruption/start" \
      -H "Content-Type: application/json" \
      -H "X-Admin-Key: ${SECRET_KEY}" \
      -d '{"duration_minutes": 20}' \
      | python3 -m json.tool
    ;;

  stop)
    HOST="${1:-localhost}"
    PORT="${2:-8000}"
    echo "🛑  Stopping Corruption event..."
    curl -s -X POST "http://${HOST}:${PORT}/admin/events/corruption/stop" \
      -H "X-Admin-Key: ${SECRET_KEY}" \
      | python3 -m json.tool
    ;;

  status)
    HOST="${1:-localhost}"
    PORT="${2:-8000}"
    echo "📊  Corruption event status (per-group progress)..."
    curl -s "http://${HOST}:${PORT}/admin/events/corruption/status" \
      -H "X-Admin-Key: ${SECRET_KEY}" \
      | python3 -m json.tool
    ;;

  *)
    echo "Usage:"
    echo "  bash scripts/corruption.sh start  [host] [port]"
    echo "  bash scripts/corruption.sh stop   [host] [port]"
    echo "  bash scripts/corruption.sh status [host] [port]"
    exit 1
    ;;
esac
