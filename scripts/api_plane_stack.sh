#!/usr/bin/env bash
# Boot the whole SANDBOX API plane locally and keep it up for interactive use.
#
# Unlike api_plane_smoke.sh (boots a subset + asserts + exits), this wires every
# API-plane service together and stays running so you can open the consoles:
#
#   Developer Portal :  http://127.0.0.1:8322   (issue keys, sandbox playground)
#   Admin Console    :  http://127.0.0.1:8323   (health, per-tenant oversight)
#   API Gateway      :  http://127.0.0.1:8320   (authenticated front door)
#
# No GCP, no Redis (these services are in-memory sandbox). Live rails + production
# keys stay gated on licensing (docs/REGULATORY_BRIEF.md).
#
# Requirements: python3 with each service's requirements installed, curl.
# Usage: ./scripts/api_plane_stack.sh   (Ctrl-C to stop)
#
# NOTE: indexed arrays only (macOS bash 3.2).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$(mktemp -d)"
PIDS=()

# Ports (override via env if they clash).
AUTH=${AUTH:-8311} METER=${METER:-8312} ROUTE=${ROUTE:-8313} WALLET=${WALLET:-8314}
ONRAMP=${ONRAMP:-8315} OFFRAMP=${OFFRAMP:-8316} SETTLE=${SETTLE:-8317} HOOK=${HOOK:-8318}
BILL=${BILL:-8319} GW=${GW:-8320} STATUS=${STATUS:-8321} PORTAL=${PORTAL:-8322} ADMIN=${ADMIN:-8323}

note() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

cleanup() {
  note "Shutting down"
  for p in "${PIDS[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "Logs in: $LOG_DIR"
  rm -f "$REPO_ROOT/dump.rdb" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

command -v curl >/dev/null || die "curl not found."

start() {  # dir port [env...]
  local dir="$1" port="$2"; shift 2
  ( cd "$REPO_ROOT/services/$dir" \
      && exec env -u GCP_PROJECT_ID PYTHONPATH="$REPO_ROOT:." "$@" \
         python3 -m uvicorn main:app --host 127.0.0.1 --port "$port" \
         >"$LOG_DIR/$dir.log" 2>&1 ) &
  PIDS+=("$!")
}
wait_healthy() {  # dir port
  for _ in $(seq 1 40); do
    curl -fs "127.0.0.1:$2/healthz" >/dev/null 2>&1 && { ok "$1 → :$2"; return 0; }
    sleep 0.5
  done
  echo "--- $1 log ---"; cat "$LOG_DIR/$1.log" 2>/dev/null || true
  die "$1 did not become healthy on :$2 (deps installed?)."
}

L="http://127.0.0.1"
note "Booting sandbox API plane"
start partner-auth        "$AUTH"
start api-metering        "$METER"
start routing-service     "$ROUTE"
start wallet-service      "$WALLET"
start onramp-orchestrator "$ONRAMP"
start offramp-orchestrator "$OFFRAMP"
start settlement-status   "$SETTLE"
start webhook-service     "$HOOK"
start tenant-billing      "$BILL"   API_METERING_URL="$L:$METER"
start public-api-gateway  "$GW" \
  PARTNER_AUTH_URL="$L:$AUTH" API_METERING_URL="$L:$METER" \
  ONRAMP_URL="$L:$ONRAMP" OFFRAMP_URL="$L:$OFFRAMP" WALLET_URL="$L:$WALLET" \
  ROUTING_URL="$L:$ROUTE" SETTLEMENT_URL="$L:$SETTLE"
start status-service      "$STATUS" \
  STATUS_TARGETS="partner-auth=$L:$AUTH,gateway=$L:$GW,onramp=$L:$ONRAMP,billing=$L:$BILL" \
  STATUS_NONCRITICAL="onramp,billing"
start developer-portal    "$PORTAL"  PARTNER_AUTH_URL="$L:$AUTH" GATEWAY_URL="$L:$GW"
start admin-console       "$ADMIN" \
  STATUS_URL="$L:$STATUS" PARTNER_AUTH_URL="$L:$AUTH" API_METERING_URL="$L:$METER" \
  WEBHOOK_URL="$L:$HOOK" BILLING_URL="$L:$BILL"

note "Waiting for health"
for nv in "partner-auth $AUTH" "api-metering $METER" "routing-service $ROUTE" \
          "wallet-service $WALLET" "onramp-orchestrator $ONRAMP" "offramp-orchestrator $OFFRAMP" \
          "settlement-status $SETTLE" "webhook-service $HOOK" "tenant-billing $BILL" \
          "public-api-gateway $GW" "status-service $STATUS" "developer-portal $PORTAL" \
          "admin-console $ADMIN"; do
  set -- $nv; wait_healthy "$1" "$2"
done

note "API plane is up (sandbox)"
echo "  Developer Portal : $L:$PORTAL"
echo "  Admin Console    : $L:$ADMIN"
echo "  API Gateway      : $L:$GW   (e.g. POST $L:$GW/api/onramp/v1/onramp/quotes with a Bearer key)"
echo "  Status           : $L:$STATUS/status"
echo
echo "Open the Developer Portal to issue a sandbox key and try the API. Ctrl-C to stop."
[ "${SMOKE:-}" = "1" ] && { ok "SMOKE=1 — all healthy; exiting."; exit 0; }
wait
