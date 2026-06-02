#!/usr/bin/env bash
# Run the ops dashboard UI locally and keep it up (Ctrl+C to stop).
#
# Serves dashboard/index.html via the dashboard-api service at http://localhost:PORT/,
# wired to a local Redis. KPIs, risk bars, and kill-switch state come from Redis;
# the BigQuery-backed panels (opportunities, audit log, PnL history) are empty
# locally — they populate only against a real GCP project.
#
# Point this at the SAME Redis as scripts/paper_trade_local.sh (default
# redis://localhost:6379/0) and the dashboard reflects the demo's activity live.
#
# Requirements: redis-cli + curl; python3 with dashboard-api deps installed.
# If no Redis is reachable, this starts a throwaway redis-server (and stops it
# on exit) when one is available locally.
#
# Usage:
#   ./scripts/run_dashboard_local.sh            # http://localhost:8080
#   PORT=9000 REDIS_URL=redis://localhost:6379/0 ./scripts/run_dashboard_local.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8080}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
CAPITAL_USD="${CAPITAL_USD:-100000}"
STATIC_DIR="$REPO_ROOT/services/dashboard-api/static"

note() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

REDIS_STARTED_BY_US=0
STATIC_CREATED_BY_US=0

cleanup() {
  note "Shutting down"
  if [[ "$STATIC_CREATED_BY_US" == "1" ]]; then rm -rf "$STATIC_DIR"; fi
  if [[ "$REDIS_STARTED_BY_US" == "1" ]]; then
    redis-cli -u "$REDIS_URL" shutdown nosave 2>/dev/null || true
    echo "stopped throwaway redis"
  fi
}
trap cleanup EXIT

command -v redis-cli >/dev/null || die "redis-cli not found."
command -v curl      >/dev/null || die "curl not found."

# ── Redis: reuse if reachable, else start a throwaway one ────────────────────
if redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1; then
  ok "Using existing Redis at $REDIS_URL"
else
  port="${REDIS_URL##*:}"; port="${port%%/*}"
  command -v redis-server >/dev/null || die "Redis not reachable at $REDIS_URL and redis-server not installed."
  note "Starting throwaway redis on :$port"
  redis-server --port "$port" --save '' --daemonize yes >/dev/null 2>&1
  sleep 1
  redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1 || die "failed to start redis on :$port"
  REDIS_STARTED_BY_US=1
  ok "Redis up on :$port"
fi

# Seed capital only if unset, so we never clobber a running paper-trade session.
if [[ -z "$(redis-cli -u "$REDIS_URL" get risk:capital_usd)" ]]; then
  redis-cli -u "$REDIS_URL" set risk:capital_usd "$CAPITAL_USD" >/dev/null
  ok "Seeded risk:capital_usd=$CAPITAL_USD"
else
  ok "Existing risk state preserved (capital already set)"
fi

# ── Bundle the static dashboard the way the Docker build does ────────────────
if [[ ! -f "$STATIC_DIR/index.html" ]]; then
  mkdir -p "$STATIC_DIR"
  cp "$REPO_ROOT/dashboard/index.html" "$STATIC_DIR/index.html"
  STATIC_CREATED_BY_US=1
  ok "Bundled dashboard/index.html → services/dashboard-api/static/"
fi

# ── Run dashboard-api in the FOREGROUND (GCP unset → BigQuery skipped) ───────
note "Dashboard → http://localhost:$PORT/   (Ctrl+C to stop)"
echo "    KPIs/risk/kill-switch come from Redis; BigQuery panels are empty locally."
cd "$REPO_ROOT/services/dashboard-api"
exec env -u GCP_PROJECT_ID PYTHONPATH="$REPO_ROOT:." REDIS_URL="$REDIS_URL" \
  python3 -m uvicorn main:app --port "$PORT"
