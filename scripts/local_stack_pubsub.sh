#!/usr/bin/env bash
# LOCAL stack with a REAL Pub/Sub event flow via the emulator.
#
# Unlike local_stack.sh (NullPublisher; flow driven over HTTP), this runs the
# genuine event-driven path through a Pub/Sub emulator — exactly as in cloud,
# just pointed at the emulator + local Redis:
#
#   publish Opportunity → arb-opportunities
#     → risk-engine gates it           → arb-approved + arb-risk-decisions
#       → paper-trader simulates fill   → arb-trade-fills
#         → trade-ledger sinks rows     (local-sink: logged, no BigQuery)
#
# Pub/Sub is decoupled from real GCP (shared/pubsub/publisher.py:pubsub_project_id):
# PUBSUB_EMULATOR_HOST routes Pub/Sub to the emulator while GCP_PROJECT_ID stays
# UNSET, so BigQuery (trade-ledger) and Secret Manager (market-data) remain in
# their no-cloud local modes.
#
# Backend: gcloud's pubsub emulator if a JRE is present, else a Docker image
# (EMULATOR_IMAGE, default google/cloud-sdk:emulators). Requires Redis + python3
# with each service's requirements installed.
#
# Usage: ./scripts/local_stack_pubsub.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
EMU_HOST="${PUBSUB_EMULATOR_HOST:-localhost:8085}"
EMU_PORT="${EMU_HOST##*:}"
PROJECT="${PUBSUB_PROJECT_ID:-local-dev}"
EMULATOR_IMAGE="${EMULATOR_IMAGE:-google/cloud-sdk:emulators}"
CAPITAL_USD="${CAPITAL_USD:-100000}"
TMP="$(mktemp -d)"
PIDS=()
EMU_KIND="" ; EMU_CID=""

ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
info() { printf '\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

cleanup() {
  info "== Shutting down =="
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  [[ "$EMU_KIND" == "gcloud" && -n "${EMU_GCLOUD_PID:-}" ]] && kill "$EMU_GCLOUD_PID" 2>/dev/null || true
  [[ "$EMU_KIND" == "docker" && -n "$EMU_CID" ]] && docker rm -f "$EMU_CID" >/dev/null 2>&1 || true
  wait 2>/dev/null || true
  info "Logs in: $TMP"
}
trap cleanup EXIT INT TERM

command -v redis-cli >/dev/null || die "redis-cli not found (brew install redis)."
redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1 || die "Redis not reachable at $REDIS_URL."
ok "Redis reachable"

# ── start the emulator ───────────────────────────────────────────────────────
start_emulator() {
  if java -version >/dev/null 2>&1; then
    EMU_KIND="gcloud"
    info "Starting gcloud pubsub emulator on :$EMU_PORT"
    ( exec gcloud beta emulators pubsub start --project="$PROJECT" \
        --host-port="0.0.0.0:$EMU_PORT" >"$TMP/emulator.log" 2>&1 ) &
    EMU_GCLOUD_PID=$!
  elif command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    EMU_KIND="docker"
    info "No JRE — starting emulator via Docker ($EMULATOR_IMAGE) on :$EMU_PORT"
    EMU_CID="$(docker run -d -p "$EMU_PORT:8085" "$EMULATOR_IMAGE" \
      gcloud beta emulators pubsub start --project="$PROJECT" \
        --host-port=0.0.0.0:8085 2>"$TMP/emulator.log")" \
      || die "Failed to start emulator container (is '$EMULATOR_IMAGE' pullable?). See $TMP/emulator.log"
  else
    die "No emulator backend: need a JRE for gcloud, or a running Docker."
  fi
}

start_emulator
# Wait for the emulator port to accept connections.
for _ in $(seq 1 60); do
  (exec 3<>"/dev/tcp/127.0.0.1/$EMU_PORT") 2>/dev/null && { exec 3>&- 3<&-; break; }
  sleep 1
done
(exec 3<>"/dev/tcp/127.0.0.1/$EMU_PORT") 2>/dev/null && exec 3>&- 3<&- \
  || die "Emulator did not come up on :$EMU_PORT (see $TMP/emulator.log)"
ok "Emulator up on :$EMU_PORT"

export PUBSUB_EMULATOR_HOST="127.0.0.1:$EMU_PORT"
export PUBSUB_PROJECT_ID="$PROJECT"

# ── bootstrap topics + subscriptions ─────────────────────────────────────────
PYTHONPATH="$REPO_ROOT:." python3 "$REPO_ROOT/scripts/pubsub_emulator_bootstrap.py" \
  || die "bootstrap failed"

# ── seed risk state ──────────────────────────────────────────────────────────
redis-cli -u "$REDIS_URL" set risk:capital_usd "$CAPITAL_USD" >/dev/null
redis-cli -u "$REDIS_URL" set risk:daily_pnl_usd 0 >/dev/null
redis-cli -u "$REDIS_URL" del risk:kill_switch:active risk:kill_switch:metadata risk:open_positions >/dev/null
redis-cli -u "$REDIS_URL" set health:latency:kraken 50 >/dev/null
redis-cli -u "$REDIS_URL" set health:latency:hyperliquid 50 >/dev/null
ok "Seeded risk state"

# ── boot wedge services (emulator Pub/Sub; GCP_PROJECT_ID stays UNSET) ────────
start() {  # name dir port [env...]
  local name="$1" dir="$2" port="$3"; shift 3
  ( cd "$REPO_ROOT/services/$dir" \
      && exec env -u GCP_PROJECT_ID PYTHONPATH="$REPO_ROOT:." REDIS_URL="$REDIS_URL" \
         PUBSUB_EMULATOR_HOST="$PUBSUB_EMULATOR_HOST" PUBSUB_PROJECT_ID="$PROJECT" "$@" \
         python3 -m uvicorn main:app --host 127.0.0.1 --port "$port" \
         >"$TMP/$dir.log" 2>&1 ) &
  PIDS+=("$!")
}
wait_healthy() { for _ in $(seq 1 40); do curl -fsS "http://127.0.0.1:$2/healthz" >/dev/null 2>&1 && return 0; sleep 0.5; done; return 1; }

info "== Booting wedge (emulator-backed Pub/Sub) =="
start risk-engine  risk-engine  8082 KILL_SWITCH_RESET_TOKEN="dev-local-token"
start paper-trader paper-trader 8081
start trade-ledger trade-ledger 8084
for nv in "risk-engine 8082" "paper-trader 8081" "trade-ledger 8084"; do
  set -- $nv; wait_healthy "$1" "$2" && ok "$1 → :$2 (subscribers live)" || warn "$1 failed health"
done

# Give subscribers a moment to attach to the emulator.
sleep 2

# ── drive: publish one Opportunity and watch it flow ─────────────────────────
info "== Publishing a synthetic Opportunity → arb-opportunities =="
PYTHONPATH="$REPO_ROOT:." python3 - <<'PY'
import os
from datetime import datetime, UTC
from google.cloud import pubsub_v1
from shared.pubsub.publisher import Topic
from shared.models.opportunity import Opportunity, StrategyType

opp = Opportunity(
    id="emu-demo-1", strategy=StrategyType.FUNDING_RATE_ARB, asset="BTC",
    long_exchange="kraken", short_exchange="hyperliquid", gross_spread_bps=120.0,
    trading_fees_bps=31.0, slippage_estimate_bps=4.0, funding_rate_annualized_pct=12.0,
    net_edge_bps=75.0, confidence_score=0.85, recommended_size_usd=1000.0,
    min_hold_hours=6.0, detected_at=datetime.now(UTC),
)
pub = pubsub_v1.PublisherClient()
path = pub.topic_path(os.environ["PUBSUB_PROJECT_ID"], Topic.OPPORTUNITIES.value)
print("published msg id:", pub.publish(path, opp.model_dump_json().encode()).result(timeout=10))
PY

# Let the chain run, then show the evidence from each service's log.
sleep 4
echo
info "== Event flow (from service logs) =="
echo "--- risk-engine: gated + forwarded ---"; grep -iE "APPROVED|rejected|arb-approved" "$TMP/risk-engine.log" | tail -3
echo "--- paper-trader: simulated fill ---";   grep -iE "fill|trade|simulat|arb-trade-fills|published" "$TMP/paper-trader.log" | tail -3
echo "--- trade-ledger: local-sink rows ---";  grep -iE "local-sink|risk_decisions|trades|opportunit" "$TMP/trade-ledger.log" | tail -4
echo
ok "Event flowed through the emulator end-to-end (decision + fill + ledger sink)."
info "Services stay up; Ctrl-C to stop. (curl 127.0.0.1:8082/state, etc.)"
wait
