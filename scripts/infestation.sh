#!/usr/bin/env bash
# scripts/infestation.sh
# ─────────────────────────────────────────────────────────────────────────────
# Infestation timed event control.
#
# The cutoff is a single number: church_level + sum of all completed station
# level numbers. Any group whose total score is below the cutoff gets penalised.
#
# Usage (from server, SSH'd in):
#   bash scripts/infestation.sh start <cutoff> [penalty] [host] [port]
#   bash scripts/infestation.sh stop   [host] [port]
#   bash scripts/infestation.sh status [host] [port]
#
# Examples:
#   bash scripts/infestation.sh start 8           # cutoff=8, default penalty 300
#   bash scripts/infestation.sh start 10 500      # cutoff=10, penalty=500
#   bash scripts/infestation.sh status
#   bash scripts/infestation.sh stop
#
# Score formula:
#   church_level + (L1 done? +1) + (L2 done? +2) + (L3 done? +3) + ...
#   e.g. church_level=2, Social Media L1+L2, Worship L1 → score = 2+1+2+1 = 6
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
    CUTOFF="${1:-}"
    PENALTY="${2:-300}"
    HOST="${3:-localhost}"
    PORT="${4:-8000}"

    if [[ -z "$CUTOFF" ]]; then
      echo "Usage: bash scripts/infestation.sh start <cutoff> [penalty] [host] [port]"
      echo ""
      echo "  cutoff  = church_level + sum of completed station level numbers"
      echo "  penalty = members deducted from failing groups (default: 300)"
      exit 1
    fi

    echo "🐛  Starting Infestation event..."
    echo "    Cutoff score : $CUTOFF (church_level + all completed station levels)"
    echo "    Penalty      : $PENALTY members for groups below cutoff"
    echo "    Timer        : 20 minutes"
    echo "    Host         : $HOST:$PORT"
    echo

    curl -s -X POST "http://${HOST}:${PORT}/admin/events/infestation/start" \
      -H "Content-Type: application/json" \
      -H "X-Admin-Key: ${SECRET_KEY}" \
      -d "{\"cutoff\": ${CUTOFF}, \"penalty\": ${PENALTY}}" \
      | python3 -m json.tool
    ;;

  stop)
    HOST="${1:-localhost}"
    PORT="${2:-8000}"
    echo "🛑  Stopping Infestation event..."
    curl -s -X POST "http://${HOST}:${PORT}/admin/events/infestation/stop" \
      -H "X-Admin-Key: ${SECRET_KEY}" \
      | python3 -m json.tool
    ;;

  status)
    HOST="${1:-localhost}"
    PORT="${2:-8000}"
    echo "📊  Infestation event status..."
    curl -s "http://${HOST}:${PORT}/admin/events/infestation/status" \
      -H "X-Admin-Key: ${SECRET_KEY}" \
      | python3 -m json.tool
    ;;

  *)
    echo "Usage:"
    echo "  bash scripts/infestation.sh start <cutoff> [penalty] [host] [port]"
    echo "  bash scripts/infestation.sh stop   [host] [port]"
    echo "  bash scripts/infestation.sh status [host] [port]"
    exit 1
    ;;
esac
