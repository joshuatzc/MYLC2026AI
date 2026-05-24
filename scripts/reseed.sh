#!/usr/bin/env bash
# scripts/reseed.sh
# ─────────────────────────────────────────────────────────────────────────────
# Trigger a live reseed against the running MYLC Bot container without
# needing to exec into it or expose the database directly.
#
# Usage (from the project root):
#   bash scripts/reseed.sh [HOST] [PORT]
#
# Defaults:
#   HOST=localhost
#   PORT=8000
#
# The script reads SECRET_KEY from your .env file automatically.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

HOST="${1:-localhost}"
PORT="${2:-8000}"
URL="http://${HOST}:${PORT}/admin/reseed"

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

echo "🌱  Triggering reseed at ${URL} …"
echo

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${URL}" \
  -H "X-Admin-Key: ${SECRET_KEY}" \
  -H "Accept: application/json")

HTTP_BODY=$(echo "$RESPONSE" | head -n -1)
HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)

if [[ "$HTTP_CODE" == "200" ]]; then
  echo "✅  Reseed successful (HTTP ${HTTP_CODE})"
  echo "$HTTP_BODY" | python3 -m json.tool 2>/dev/null || echo "$HTTP_BODY"
else
  echo "❌  Reseed failed (HTTP ${HTTP_CODE})"
  echo "$HTTP_BODY"
  exit 1
fi
