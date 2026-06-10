#!/usr/bin/env bash
# Sandbox API-plane integration smoke — proves the partner request path end to end
# against LIVE local services. No GCP, no emulator (HTTP path only).
#
#   partner-auth (keys) ──▶ public-api-gateway ──▶ {routing, onramp, wallet}
#                              │  authenticate (partner-auth/verify)
#                              │  meter        (api-metering/record)
#                              └─ proxy        (to the scoped upstream)
#
# Asserts the gateway contract: no key → 401, valid key → proxied 200, missing
# scope → 403, and that api-metering counted the calls.
#
# Requirements: python3 with each service's requirements installed, curl.
# (No Redis/GCP needed — these API-plane services are in-memory sandbox.)
#
# Usage: ./scripts/api_plane_smoke.sh
#
# NOTE: indexed arrays only (macOS bash 3.2).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTH_PORT=8211 METER_PORT=8212 ROUTE_PORT=8213 WALLET_PORT=8214 ONRAMP_PORT=8215 GW_PORT=8218
LOG_DIR="$(mktemp -d)"
PIDS=()
FAILS=0

note() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
bad()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; FAILS=$((FAILS + 1)); }
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

start() {  # name dir port [env...]
  local name="$1" dir="$2" port="$3"; shift 3
  ( cd "$REPO_ROOT/services/$dir" \
      && exec env -u GCP_PROJECT_ID PYTHONPATH="$REPO_ROOT:." "$@" \
         python3 -m uvicorn main:app --host 127.0.0.1 --port "$port" \
         >"$LOG_DIR/$dir.log" 2>&1 ) &
  PIDS+=("$!")
}
wait_healthy() {  # name port
  local name="$1" port="$2"
  for _ in $(seq 1 40); do
    curl -fs "127.0.0.1:$port/healthz" >/dev/null 2>&1 && { ok "$name → :$port"; return 0; }
    sleep 0.5
  done
  echo "--- $name log ---"; cat "$LOG_DIR/$2.log" 2>/dev/null || true
  die "$name did not become healthy on :$port (deps installed?)."
}

# returns the HTTP code of a request; args: method url [data] [authheader]
http_code() {
  local method="$1" url="$2" data="${3:-}" auth="${4:-}"
  local args=(-s -o /dev/null -w '%{http_code}' -X "$method" "$url" -H 'content-type: application/json')
  [[ -n "$auth" ]] && args+=(-H "authorization: Bearer $auth")
  [[ -n "$data" ]] && args+=(-d "$data")
  curl "${args[@]}"
}

note "Booting API-plane services"
start partner-auth       partner-auth       "$AUTH_PORT"
start api-metering       api-metering       "$METER_PORT"
start routing-service    routing-service    "$ROUTE_PORT"
start wallet-service     wallet-service     "$WALLET_PORT"
start onramp-orchestrator onramp-orchestrator "$ONRAMP_PORT"
start public-api-gateway public-api-gateway "$GW_PORT" \
  PARTNER_AUTH_URL="http://127.0.0.1:$AUTH_PORT" \
  API_METERING_URL="http://127.0.0.1:$METER_PORT" \
  ROUTING_URL="http://127.0.0.1:$ROUTE_PORT" \
  WALLET_URL="http://127.0.0.1:$WALLET_PORT" \
  ONRAMP_URL="http://127.0.0.1:$ONRAMP_PORT"
for nv in "partner-auth $AUTH_PORT" "api-metering $METER_PORT" "routing $ROUTE_PORT" \
          "wallet $WALLET_PORT" "onramp $ONRAMP_PORT" "gateway $GW_PORT"; do
  set -- $nv; wait_healthy "$1" "$2"
done

note "Issue a sandbox key (scopes: onramp, routing, wallets — NOT offramp)"
TOK="$(curl -s "127.0.0.1:$AUTH_PORT/v1/partner/keys" -H 'content-type: application/json' \
  -d '{"tenant_id":"ten_smoke","scopes":["onramp","routing","wallets"]}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')"
[[ -n "$TOK" ]] && ok "issued key ${TOK:0:24}..." || die "key issuance failed"

GW="http://127.0.0.1:$GW_PORT"

note "1) no key → 401"
c=$(http_code POST "$GW/api/onramp/v1/onramp/quotes" '{"source_currency":"NGN","dest_asset":"USDC","amount":1000}')
[[ "$c" == 401 ]] && ok "unauthenticated → 401" || bad "expected 401, got $c"

note "2) valid key + scope → routing resolve proxied (200)"
c=$(http_code POST "$GW/api/routes/v1/routes/resolve" '{"direction":"onramp","source":"NGN","dest":"USDC","amount":1000}' "$TOK")
[[ "$c" == 200 ]] && ok "routing resolve via gateway → 200" || bad "expected 200, got $c"

note "3) onramp quote proxied (200)"
q=$(curl -s "$GW/api/onramp/v1/onramp/quotes" -H "authorization: Bearer $TOK" -H 'content-type: application/json' \
  -d '{"source_currency":"NGN","dest_asset":"USDC","amount":160000}')
echo "$q" | python3 -c 'import sys,json;q=json.load(sys.stdin);print("   fee",q["fee"],"dest",q["dest_amount"])' 2>/dev/null \
  && ok "onramp quote via gateway" || bad "onramp quote failed: $q"

note "4) missing scope (offramp) → 403"
c=$(http_code POST "$GW/api/offramp/v1/offramp/quotes" '{"source_asset":"USDC","dest_currency":"NGN","amount":100}' "$TOK")
[[ "$c" == 403 ]] && ok "offramp w/ onramp-only key → 403" || bad "expected 403, got $c"

note "5) wallet create + credit proxied"
WID=$(curl -s "$GW/api/wallets/v1/wallets" -H "authorization: Bearer $TOK" -H 'content-type: application/json' \
  -d '{"tenant_id":"ten_smoke","asset":"USDC"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])' 2>/dev/null)
[[ -n "$WID" ]] && ok "wallet created via gateway ($WID)" || bad "wallet create failed"

note "6) api-metering counted the proxied calls"
TOTAL=$(curl -s "127.0.0.1:$METER_PORT/v1/metering/usage?tenant=ten_smoke" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["total"])' 2>/dev/null)
# routing + onramp-quote + wallet-create = 3 metered (401 + 403 never reach metering)
[[ "${TOTAL:-0}" -ge 3 ]] && ok "metering counted $TOTAL gateway calls" || bad "expected >=3 metered, got ${TOTAL:-0}"

note "Verdict"
if [[ "$FAILS" -eq 0 ]]; then
  ok "API plane integrates end to end: auth → scope → meter → proxy."
  exit 0
else
  bad "$FAILS assertion(s) failed — see logs in $LOG_DIR."
  exit 1
fi
