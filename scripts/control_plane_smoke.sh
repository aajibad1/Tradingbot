#!/usr/bin/env bash
# End-to-end LOCAL smoke of the control plane — no GCP, no Postgres, no Clerk.
#
# Boots accounts-service + core-api against SQLite and walks the real onboarding
# → billing → funding → live-enable → dashboard flow over HTTP. Unlike the unit
# tests (which mock the accounts client), this exercises the REAL cross-service
# funding seam (core-api -> accounts-service).
#
# Requirements: python3 with both services' requirements installed, curl, openssl.
# Usage: ./scripts/control_plane_smoke.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE_PORT="${CORE_PORT:-8080}"
ACCT_PORT="${ACCT_PORT:-8090}"
STRIPE_SECRET="whsec_smoke"
TOKEN="local:u-smoke:smoke@example.com"
AUTH=(-H "Authorization: Bearer ${TOKEN}")
TMP="$(mktemp -d)"
PIDS=()

ok()  { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup EXIT

command -v openssl >/dev/null || die "openssl required"

start() {  # name dir port  extra-env...
  local name="$1" dir="$2" port="$3"; shift 3
  ( cd "$REPO_ROOT/services/$dir" \
      && exec env -u GCP_PROJECT_ID PYTHONPATH="$REPO_ROOT:." DATABASE_URL="sqlite:///$TMP/$dir.db" "$@" \
         python3 -m uvicorn main:app --port "$port" >"$TMP/$dir.log" 2>&1 ) &
  PIDS+=("$!")
}

wait_healthy() {
  local name="$1" port="$2"
  for _ in $(seq 1 40); do
    curl -fs "localhost:$port/healthz" >/dev/null 2>&1 && { ok "$name healthy :$port"; return 0; }
    sleep 0.5
  done
  cat "$TMP/$2.log" 2>/dev/null || true
  die "$name not healthy on :$port"
}

j() { python3 -c 'import sys,json; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

echo "== Booting services (SQLite, local auth) =="
start accounts-service accounts-service "$ACCT_PORT"
start core-api core-api "$CORE_PORT" \
  ACCOUNTS_SERVICE_URL="http://localhost:$ACCT_PORT" STRIPE_WEBHOOK_SECRET="$STRIPE_SECRET"
wait_healthy accounts-service "$ACCT_PORT"
wait_healthy core-api "$CORE_PORT"

base="localhost:$CORE_PORT"

echo "== 1. session =="
tid="$(curl -fs "${AUTH[@]}" -X POST "$base/v1/sessions" | j tenant_id)"
[[ -n "$tid" ]] && ok "tenant=$tid"

echo "== 2. onboarding (global/US) -> trading_ready =="
curl -fs "${AUTH[@]}" -H 'Content-Type: application/json' -X POST "$base/v1/onboarding/region" \
  -d '{"market":"global","country":"US"}' >/dev/null
curl -fs "${AUTH[@]}" -H 'Content-Type: application/json' -X POST "$base/v1/onboarding/kyc" \
  -d '{"full_name":"Smoke Test"}' >/dev/null
st="$(curl -fs "${AUTH[@]}" -X POST "$base/v1/onboarding/submit" | j onboarding_status)"
[[ "$st" == "trading_ready" ]] && ok "onboarding=$st" || die "expected trading_ready, got $st"

echo "== 3. Stripe webhook -> pro plan (real signature) =="
payload='{"id":"evt_smoke","type":"checkout.session.completed","data":{"object":{"customer":"cus_s","metadata":{"tenant_id":"'"$tid"'","plan":"pro"}}}}'
ts=1700000000
# $NF = last field: works whether openssl prints "(stdin)= <hex>" or bare "<hex>".
sig="$(printf '%s' "$ts.$payload" | openssl dgst -sha256 -hmac "$STRIPE_SECRET" -hex | awk '{print $NF}')"
curl -fs -H "Stripe-Signature: t=$ts,v1=$sig" -H 'Content-Type: application/json' \
  -X POST "$base/webhooks/stripe" -d "$payload" >/dev/null
plan="$(curl -fs "${AUTH[@]}" "$base/v1/entitlements" | j plan)"
[[ "$plan" == "pro" ]] && ok "plan=$plan" || die "expected pro, got $plan"

echo "== 4. funding deposit -> accounts-service ledger (REAL cross-service call) =="
avail="$(curl -fs "${AUTH[@]}" -H 'Content-Type: application/json' -X POST "$base/v1/funding/deposit" \
  -d '{"asset":"USD","amount":"2500"}' | j available)"
# accounts-service serializes Decimal (e.g. "2500.00000000"); compare on the integer part.
[[ "${avail%%.*}" == "2500" ]] && ok "available=$avail (via real AccountsClient)" || die "expected 2500, got $avail"

echo "== 5. live-enable (perm x plan x onboarding all pass) =="
live="$(curl -fs "${AUTH[@]}" -X POST "$base/v1/trading/live-enable" | j live_enabled)"
[[ "$live" == "True" ]] && ok "live_enabled=$live" || die "expected True, got $live"

echo "== 6. dashboard aggregates everything =="
dash="$(curl -fs "${AUTH[@]}" "$base/v1/dashboard")"
echo "$dash" | python3 -c '
import sys,json; d=json.load(sys.stdin)
usd=[b for b in d["balances"] if b["asset"]=="USD"]
assert d["entitlements"]["live_trading"] is True, d
assert usd and usd[0]["available"]==2500.0, d
print("  dashboard: plan=%s live=%s USD=%s" % (d["entitlements"]["plan"], d["profile"]["live_enabled"], usd[0]["available"]))
'
ok "dashboard verified"

echo
ok "Control-plane smoke PASSED — onboarding → billing → funding → live-enable → dashboard"
echo "(logs in $TMP)"
