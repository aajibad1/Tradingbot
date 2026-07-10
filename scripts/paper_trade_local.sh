#!/usr/bin/env bash
# End-to-end LOCAL paper-trading demo — no GCP required.
#
# Boots risk-engine + paper-trader against a local Redis, then walks one
# opportunity through the real flow:
#
#   1. risk-engine /evaluate            → approves an opportunity
#   2. paper-trader /simulate           → simulates the fill (fees/slippage/funding)
#   3. risk-engine /positions/apply-fill → folds a (losing) fill into risk state
#   4. risk-engine /evaluate (again)    → the drawdown backstop now TRIPS the kill switch
#
# Step 4 demonstrates the loss backstop that used to be a dead check
# (risk:* state was never written). Everything runs in NullPublisher mode
# (GCP_PROJECT_ID unset) so no Pub/Sub or cloud credentials are touched.
#
# Requirements: a reachable Redis (redis-cli + a server on REDIS_URL), python3
# with each service's requirements installed, and curl.
#
# Usage:
#   ./scripts/paper_trade_local.sh
#
# Override ports / redis if needed:
#   RISK_PORT=8082 PAPER_PORT=8081 REDIS_URL=redis://localhost:6379/0 ./scripts/paper_trade_local.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RISK_PORT="${RISK_PORT:-8082}"
RISK2_PORT="${RISK2_PORT:-8092}"   # sleeve-enabled twin for the directional-budget check
PAPER_PORT="${PAPER_PORT:-8081}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
CAPITAL_USD="${CAPITAL_USD:-100000}"
RESET_TOKEN="dev-local-token"

# Per-trade size must be <= 2% of capital and net edge >= 50 bps, or risk-engine
# rejects on position-limits / min-edge.
SIZE_USD="$(python3 -c "print(${CAPITAL_USD} * 0.02)")"

LOG_DIR="$(mktemp -d)"
PIDS=()

note()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
die()   { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

cleanup() {
  note "Shutting down"
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "Service logs kept in: $LOG_DIR"
}
trap cleanup EXIT

# ── preflight ────────────────────────────────────────────────────────────────
command -v redis-cli >/dev/null || die "redis-cli not found — install Redis or set REDIS_URL to a reachable instance."
command -v curl      >/dev/null || die "curl not found."
redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1 || die "Redis not reachable at $REDIS_URL — start one (e.g. 'redis-server' or 'docker run -p 6379:6379 redis:7')."
ok "Redis reachable at $REDIS_URL"

# ── clean slate in Redis (only the keys this demo touches) ───────────────────
note "Seeding risk state (capital=\$${CAPITAL_USD}, clearing prior demo state)"
redis-cli -u "$REDIS_URL" set risk:capital_usd "$CAPITAL_USD" >/dev/null
redis-cli -u "$REDIS_URL" set risk:daily_pnl_usd 0 >/dev/null
redis-cli -u "$REDIS_URL" del risk:kill_switch:active risk:kill_switch:metadata risk:open_positions >/dev/null
# Exchange-health check fails closed without a latency signal (normally written
# by market-data). Seed healthy latencies so the demo isn't rejected on health.
redis-cli -u "$REDIS_URL" set health:latency:kraken 50 >/dev/null
redis-cli -u "$REDIS_URL" set health:latency:hyperliquid 50 >/dev/null
# clear any leftover exposure/concentration/position keys
for pat in 'risk:exposure:*' 'risk:concentration:*' 'risk:position:*'; do
  keys="$(redis-cli -u "$REDIS_URL" --scan --pattern "$pat")"
  [[ -n "$keys" ]] && echo "$keys" | xargs redis-cli -u "$REDIS_URL" del >/dev/null || true
done
ok "Risk state seeded"

# ── start services (local mode: GCP_PROJECT_ID unset → NullPublisher) ────────
start_service() {
  local name="$1" port="$2"; shift 2
  # exec so the subshell is REPLACED by uvicorn — then $! is uvicorn's own PID
  # and the cleanup trap actually kills it (otherwise the grandchild orphans).
  ( cd "$REPO_ROOT/services/$name" \
      && exec env -u GCP_PROJECT_ID PYTHONPATH="$REPO_ROOT:." REDIS_URL="$REDIS_URL" "$@" \
         python3 -m uvicorn main:app --port "$port" >"$LOG_DIR/$name.log" 2>&1 ) &
  PIDS+=("$!")
}

wait_healthy() {
  local name="$1" port="$2"
  for _ in $(seq 1 40); do
    if curl -fs "localhost:$port/healthz" >/dev/null 2>&1; then ok "$name healthy on :$port"; return 0; fi
    sleep 0.5
  done
  echo "--- $name log ---"; cat "$LOG_DIR/$name.log" || true
  die "$name did not become healthy on :$port (deps installed? 'pip install -r services/$name/requirements.txt')"
}

note "Starting services"
start_service risk-engine  "$RISK_PORT"  KILL_SWITCH_RESET_TOKEN="$RESET_TOKEN"
# Twin engine with the satellite sleeve OPENED via the deploy-time operator knob —
# proves the same directional opportunity flips refused → approved on config only.
start_service risk-engine  "$RISK2_PORT" KILL_SWITCH_RESET_TOKEN="$RESET_TOKEN" \
  MAX_DIRECTIONAL_EXPOSURE_PCT="5.0"
start_service paper-trader "$PAPER_PORT"
wait_healthy risk-engine  "$RISK_PORT"
wait_healthy risk-engine  "$RISK2_PORT"
wait_healthy paper-trader "$PAPER_PORT"

# ── opportunity payload (delta-neutral funding-rate carry) ───────────────────
opp() {
  local execute="$1"
  cat <<JSON
{
  "id": "local-demo-1",
  "strategy": "funding_rate_arb",
  "asset": "BTC",
  "long_exchange": "kraken",
  "short_exchange": "hyperliquid",
  "gross_spread_bps": 0.0,
  "trading_fees_bps": 31.0,
  "slippage_estimate_bps": 4.0,
  "funding_rate_annualized_pct": 30.0,
  "net_edge_bps": 62.0,
  "confidence_score": 0.8,
  "recommended_size_usd": ${SIZE_USD},
  "min_hold_hours": 6.0,
  "detected_at": "2026-06-01T12:00:00Z",
  "execute": ${execute}
}
JSON
}

jq_or_cat() { python3 -m json.tool 2>/dev/null || cat; }

# ── 1. risk-engine approves ──────────────────────────────────────────────────
note "1) risk-engine /evaluate  (size=\$${SIZE_USD}, net_edge=62bps)"
curl -s "localhost:$RISK_PORT/evaluate" -H 'Content-Type: application/json' \
  -d "{\"opportunity\": $(opp false)}" | jq_or_cat

# ── 1b. directional sleeve: refused by default, approved once opened ─────────
dopp() {
  cat <<JSON
{
  "id": "local-directional-1",
  "strategy": "directional",
  "asset": "BTC",
  "long_exchange": "hyperliquid",
  "short_exchange": "hyperliquid",
  "gross_spread_bps": 75.0,
  "trading_fees_bps": 10.0,
  "slippage_estimate_bps": 4.0,
  "funding_rate_annualized_pct": 0.0,
  "net_edge_bps": 58.0,
  "confidence_score": 0.9,
  "recommended_size_usd": 1500.0,
  "min_hold_hours": 1.0,
  "detected_at": "2026-06-01T12:00:00Z",
  "execute": false,
  "direction": "long"
}
JSON
}

note "1b) directional sleeve gate: default 0% refuses; MAX_DIRECTIONAL_EXPOSURE_PCT=5 approves"
D1=$(curl -s "localhost:$RISK_PORT/evaluate" -H 'Content-Type: application/json' -d "{\"opportunity\": $(dopp)}")
echo "$D1" | python3 -c "
import sys, json
d = json.load(sys.stdin)
rules = [v['rule'] for v in d['violations']]
assert d['approved'] is False and 'directional_budget' in rules, d
print('  refused by default sleeve (0%):', rules)
" || die "directional opportunity was NOT refused at the default 0% sleeve"
ok "sleeve closed → directional refused (rule=directional_budget)"
D2=$(curl -s "localhost:$RISK2_PORT/evaluate" -H 'Content-Type: application/json' -d "{\"opportunity\": $(dopp)}")
echo "$D2" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['approved'] is True and d['violations'] == [], d
" || die "directional opportunity was NOT approved with the sleeve open (5%)"
ok "sleeve open (5% via env, config deploy) → same opportunity approved"

# ── 2. paper-trader simulates the approved opportunity ───────────────────────
note "2) paper-trader /simulate  (execute=true)"
curl -s "localhost:$PAPER_PORT/simulate" -H 'Content-Type: application/json' -d "{
  \"opportunity\": $(opp true),
  \"long_reference_price\": 60000.0,
  \"short_reference_price\": 60050.0,
  \"long_book_depth_usd\": 250000.0,
  \"short_book_depth_usd\": 250000.0
}" | jq_or_cat

# ── 3. feed a LOSING fill into risk state (what the arb-trade-fills sub does) ─
note "3) risk-engine /positions/apply-fill  (a -\$1,500 closed trade = 1.5% of capital)"
curl -s "localhost:$RISK_PORT/positions/apply-fill" -H 'Content-Type: application/json' -d '{
  "id":"t-loss","opportunity_id":"local-demo-1","type":"paper",
  "legs":[
    {"exchange":"kraken","side":"buy","asset":"BTC","size":0.03,"fill_price":60000,"fee_usd":5,"slippage_usd":2,"filled_at":"2026-06-01T12:00:00Z"},
    {"exchange":"hyperliquid","side":"sell","asset":"BTC","size":0.03,"fill_price":60050,"fee_usd":5,"slippage_usd":2,"filled_at":"2026-06-01T12:00:00Z"}
  ],
  "gross_pnl_usd":-1500,"net_pnl_usd":-1500,"status":"closed","opened_at":"2026-06-01T12:00:00Z"
}' | jq_or_cat

# ── 4. re-evaluate → drawdown backstop trips the kill switch ─────────────────
note "4) risk-engine /evaluate (again) — daily loss 1.5% > 1% limit → kill switch TRIPS"
curl -s "localhost:$RISK_PORT/evaluate" -H 'Content-Type: application/json' \
  -d "{\"opportunity\": $(opp false)}" | jq_or_cat

note "Done"
echo "The second /evaluate was rejected and the kill switch is now active —"
echo "this is the loss backstop that used to be a dead check (risk:* never written)."
echo
echo "Reset it with:"
echo "  curl -s localhost:$RISK_PORT/kill-switch/reset -H 'Content-Type: application/json' \\"
echo "    -d '{\"auth_token\":\"$RESET_TOKEN\",\"reset_by\":\"you\"}'"
echo
echo "(Services stop when this script exits.)"
