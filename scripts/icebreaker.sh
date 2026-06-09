#!/usr/bin/env bash
# =============================================================================
# scripts/icebreaker.sh – MYLC Ice Breaker Finalize Console  (SSH / Ethan only)
#
# The points scorer uses the Day Bot (Telegram) to enter results.
# This script is for YOU — SSH in when you're ready to confirm and finalize.
#
# Usage (from project root):
#   bash scripts/icebreaker.sh
#
# Requires:
#   - curl       (HTTP calls to the local API)
#   - jq         (JSON parsing — brew install jq / apt install jq)
#   - API running on localhost:8000
#   - SECRET_KEY in the environment or .env file
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_BASE="${API_BASE:-http://localhost:8000}"
ADMIN_KEY="${SECRET_KEY:-change-me-in-production}"

# Resolve SECRET_KEY from .env if not already in environment
if [[ -f ".env" && "$ADMIN_KEY" == "change-me-in-production" ]]; then
    # shellcheck disable=SC2046
    export $(grep -v '^#' .env | xargs) 2>/dev/null || true
    ADMIN_KEY="${SECRET_KEY:-change-me-in-production}"
fi

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}ℹ  $*${RESET}"; }
success() { echo -e "${GREEN}✔  $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠  $*${RESET}"; }
error()   { echo -e "${RED}✖  $*${RESET}"; }
header()  { echo -e "\n${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; echo -e "${BOLD}${CYAN}  $*${RESET}"; echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"; }

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
api_get()  { curl -sf -H "X-Admin-Key: $ADMIN_KEY" "$API_BASE$1"; }
api_post() { curl -sf -X POST -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" -d "$2" "$API_BASE$1"; }

# ---------------------------------------------------------------------------
# Show current game mode
# ---------------------------------------------------------------------------
show_mode() {
    local mode
    mode=$(api_get "/admin/icebreaker/mode" | jq -r '.game_mode')
    if [[ "$mode" == "icebreaker" ]]; then
        echo -e "  Current mode: ${YELLOW}${BOLD}ICE BREAKER${RESET}  (night bot is live but populations are still at base)"
    else
        echo -e "  Current mode: ${GREEN}${BOLD}NIGHT GAMES${RESET}  (already finalized — bonuses applied)"
    fi
}

# ---------------------------------------------------------------------------
# Show standings
# ---------------------------------------------------------------------------
show_standings() {
    header "Current Ice Breaker Standings"
    local data
    data=$(api_get "/admin/icebreaker/standings")

    printf "\n  ${BOLD}%-4s  %-20s  %-8s  %-6s  %-14s${RESET}\n" "Rank" "Group" "Points" "+Pop" "Starts At"
    echo "  ──────────────────────────────────────────────────────"

    echo "$data" | jq -r '.[] | [.rank, .group_name, .total_points, .starting_pop_bonus, .final_starting_pop] | @tsv' | \
    while IFS=$'\t' read -r rank name pts bonus final; do
        if   [[ "$rank" == "1" ]]; then col="${GREEN}"
        elif [[ "$rank" -le 3  ]]; then col="${YELLOW}"
        else                             col="${RESET}"; fi
        printf "  ${col}%-4s  %-20s  %-8s  +%-5s  %-14s${RESET}\n" \
            "$rank" "$name" "$pts" "$bonus" "$final"
    done
    echo ""
}

# ---------------------------------------------------------------------------
# Show registered games (read-only overview)
# ---------------------------------------------------------------------------
show_games() {
    header "Registered Games (entered via Day Bot)"
    local data
    data=$(api_get "/admin/icebreaker/games")
    local count
    count=$(echo "$data" | jq 'length')

    if [[ "$count" -eq 0 ]]; then
        warn "No games registered yet."
        return
    fi

    echo "$data" | jq -r '.[] | "\(.id)\t\(.name)\t\(.scoring_type)\t\(.results | length)"' | \
    while IFS=$'\t' read -r id name stype res_count; do
        local tag=""
        if [[ "$stype" == "single" ]]; then
            tag=" [1 winner]"
        elif [[ "$stype" == "points" ]]; then
            tag=" [custom points]"
        else
            tag=" [ranking]"
        fi
        printf "  ${BOLD}[%s]${RESET} %s%s — ${CYAN}%s result(s)${RESET}\n" "$id" "$name" "$tag" "$res_count"

        # Print results for this game
        api_get "/admin/icebreaker/games" | \
            jq -r ".[] | select(.id == $id) | .results[] | if .placement == null then \"      \(.group_name): +\(.points) pts\" else \"      \(.placement). \(.group_name) (+\(.points) pts)\" end"
    done
    echo ""
}

# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------
finalize() {
    header "Finalize Ice Breaker → Apply Bonuses & Open Night Games"

    warn "This will:"
    echo "    1. Apply starting population bonuses to all 14 groups."
    echo "    2. Mark the game as Night Games mode."
    echo ""

    show_standings

    local current_mode
    current_mode=$(api_get "/admin/icebreaker/mode" | jq -r '.game_mode')
    if [[ "$current_mode" == "nightgame" ]]; then
        warn "Already in nightgame mode. Running finalize again will re-apply bonuses (idempotent)."
    fi

    read -rp "  Are you sure? Type 'yes' to confirm: " confirm
    if [[ "$confirm" != "yes" ]]; then
        warn "Cancelled. No changes made."
        return
    fi

    local result
    result=$(api_post "/admin/icebreaker/finalize" "{}")
    success "Done! Bonuses applied. Night games mode is ACTIVE."
    echo ""
    info "Final populations:"
    echo "$result" | jq -r '.standings[] | "  Rank \(.rank): \(.group_name) → \(.final_starting_pop) people (+\(.starting_pop_bonus))"'
}

# ---------------------------------------------------------------------------
# Manual mode override (emergency use)
# ---------------------------------------------------------------------------
set_mode() {
    header "Manual Mode Override"
    echo "  [1] Switch to icebreaker mode (locks populations at base)"
    echo "  [2] Switch to nightgame mode  (without applying bonuses)"
    echo "  [0] Cancel"
    echo ""
    read -rp "  Choose: " choice
    case "$choice" in
        1)
            api_post "/admin/icebreaker/mode" '{"mode":"icebreaker"}' > /dev/null
            success "Mode set to: icebreaker"
            ;;
        2)
            api_post "/admin/icebreaker/mode" '{"mode":"nightgame"}' > /dev/null
            success "Mode set to: nightgame"
            ;;
        0) warn "Cancelled." ;;
        *) error "Invalid choice." ;;
    esac
}

# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
main_menu() {
    while true; do
        echo ""
        echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════╗${RESET}"
        echo -e "${BOLD}${CYAN}║    MYLC ICE BREAKER — ADMIN CONSOLE      ║${RESET}"
        echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════╝${RESET}"
        echo -e "  ${CYAN}(Results are entered via the Day Bot on Telegram)${RESET}"
        show_mode
        echo ""
        echo "  [1] View standings"
        echo "  [2] View registered games"
        echo "  [3] 🚀 FINALIZE — Apply bonuses & switch to Night Games"
        echo "  [4] Manual mode override (emergency)"
        echo "  [0] Exit"
        echo ""
        read -rp "  Choose: " choice
        case "$choice" in
            1) show_standings ;;
            2) show_games ;;
            3) finalize ;;
            4) set_mode ;;
            0) echo -e "\n${GREEN}Done!${RESET}\n"; exit 0 ;;
            *) error "Invalid option." ;;
        esac
        echo ""
        read -rp "  Press Enter to return to menu…" _
    done
}

main_menu
