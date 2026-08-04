#!/usr/bin/env bash
# Settlement-flow smoke — proves the funding.* envelope contract end-to-end
# against LIVE services (docs/07):
#
#   onramp-orchestrator --Order--> EventEnvelope.wrap() --> settlement-status
#                                                        \-> webhook-service
#
# Why this exists: settlement-status/webhook-service's own unit tests drive
# HAND-BUILT envelope dicts, not envelopes constructed the way the real
# producer (onramp-orchestrator) actually would. This smoke closes that gap —
# it fetches a REAL Order from a running onramp-orchestrator, wraps it with
# the actual shared.models.event_envelope.EventEnvelope.wrap() (not a
# reimplementation), and feeds that exact envelope into both consumers.
# offramp-orchestrator/payout.* is structurally identical (same Order model,
# same _emit() pattern) and is covered by settlement-status's own unit tests;
# not duplicated here to keep this focused.
#
# No GCP/Redis (NullPublisher — this smoke pushes envelopes over HTTP directly
# instead of relying on a live Pub/Sub subscriber). Requirements: python3 +
# each service's deps, curl. Usage: ./scripts/settlement_flow_smoke.sh
#
# NOTE: indexed arrays only (macOS bash 3.2).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ONRAMP=${ONRAMP:-8271} SETTLEMENT=${SETTLEMENT:-8272} WEBHOOK=${WEBHOOK:-8273}
LOG_DIR="$(mktemp -d)"
PIDS=()
FAILS=0
L="http://127.0.0.1"

note() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
bad()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; FAILS=$((FAILS + 1)); }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

cleanup() {
  note "Shutting down"
  for p in "${PIDS[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "Logs in: $LOG_DIR"
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
wait_healthy() {
  for _ in $(seq 1 40); do curl -fs "$L:$2/healthz" >/dev/null 2>&1 && { ok "$1 → :$2"; return 0; }; sleep 0.5; done
  cat "$LOG_DIR/$1.log" 2>/dev/null || true; die "$1 not healthy on :$2."
}
field() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1'))"; }

note "Booting onramp-orchestrator + settlement-status + webhook-service"
start onramp-orchestrator "$ONRAMP"
start settlement-status   "$SETTLEMENT"
start webhook-service     "$WEBHOOK"
wait_healthy onramp-orchestrator "$ONRAMP"
wait_healthy settlement-status "$SETTLEMENT"
wait_healthy webhook-service "$WEBHOOK"

# ── 1) a real order, from a real onramp-orchestrator ─────────────────────────
note "1) onramp-orchestrator: create a real order"
ORDER=$(curl -s "$L:$ONRAMP/v1/onramp/orders" -H 'content-type: application/json' -d '{
  "source_currency": "NGN", "dest_asset": "USDC", "amount": 250000.0,
  "destination_wallet": "0xSMOKE"
}')
OID=$(echo "$ORDER" | field id)
[[ -n "$OID" && "$OID" != None ]] && ok "order created: $OID" || die "order creation failed: $ORDER"

# ── 2) register a catch-all webhook endpoint ──────────────────────────────────
note "2) webhook-service: register an endpoint (event_types=[] -> matches all)"
EP=$(curl -s "$L:$WEBHOOK/v1/webhooks/endpoints" -H 'content-type: application/json' -d '{
  "url": "http://127.0.0.1:1/unreachable-by-design", "tenant_id": "ten_smoke"
}')
EPID=$(echo "$EP" | field id)
[[ -n "$EPID" && "$EPID" != None ]] && ok "endpoint registered: $EPID" || die "endpoint creation failed: $EP"

# push_envelope <event_type> <order_json> -> wraps with the REAL EventEnvelope.wrap()
# (not a reimplementation) and POSTs it to both consumers.
push_envelope() {
  local event_type="$1" order_json="$2"
  local envelope
  envelope=$(python3 - "$event_type" "$REPO_ROOT" "$order_json" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[2])
from shared.models.event_envelope import EventEnvelope
order = json.loads(sys.argv[3])
env = EventEnvelope.wrap(event_type=sys.argv[1], payload=order, producer="onramp-orchestrator",
                          tenant_id=order.get("tenant_id"), correlation_id=order.get("correlation_id"))
print(env.model_dump_json())
PY
)
  curl -s "$L:$SETTLEMENT/v1/events/ingest" -H 'content-type: application/json' -d "$envelope" >/dev/null
  curl -s "$L:$WEBHOOK/v1/events/deliver" -H 'content-type: application/json' -d "$envelope" >/dev/null
}

# ── 3) walk the real lifecycle: created -> advance -> processing -> advance -> completed
note "3) walk the order through its real lifecycle, ingesting each real state"
push_envelope "funding.created" "$ORDER"

ORDER=$(curl -s -X POST "$L:$ONRAMP/v1/onramp/orders/$OID/advance")
S1=$(echo "$ORDER" | field status)
[[ "$S1" == "processing" ]] && ok "order advanced to processing" || bad "expected processing, got $S1"
push_envelope "funding.processing" "$ORDER"

ORDER=$(curl -s -X POST "$L:$ONRAMP/v1/onramp/orders/$OID/advance")
S2=$(echo "$ORDER" | field status)
[[ "$S2" == "completed" ]] && ok "order advanced to completed" || bad "expected completed, got $S2"
push_envelope "funding.completed" "$ORDER"

# ── 4) settlement-status projected the REAL producer's envelopes correctly ──
note "4) settlement-status: the projection reflects the real lifecycle"
SETTLE=$(curl -s "$L:$SETTLEMENT/v1/settlements/$OID")
SSTATUS=$(echo "$SETTLE" | field status)
SKIND=$(echo "$SETTLE" | field kind)
[[ "$SSTATUS" == "completed" ]] && ok "settlement status=completed" || bad "expected completed, got $SSTATUS ($SETTLE)"
[[ "$SKIND" == "onramp" ]] && ok "settlement kind=onramp" || bad "expected kind=onramp, got $SKIND"

# ── 5) webhook-service matched + attempted delivery for the same envelopes ──
note "5) webhook-service: the endpoint matched all 3 real envelopes"
DELIVERIES=$(curl -s "$L:$WEBHOOK/v1/deliveries")
COUNT=$(echo "$DELIVERIES" | python3 -c "import sys,json;print(json.load(sys.stdin)['count'])")
[[ "$COUNT" -ge 3 ]] && ok "webhook-service recorded $COUNT deliveries for the real envelopes" \
  || bad "expected >=3 deliveries, got $COUNT"

note "Verdict"
if [[ "$FAILS" -eq 0 ]]; then
  ok "Settlement flow holds: a REAL onramp-orchestrator envelope (EventEnvelope.wrap(), not a hand-built test fixture) is correctly consumed by both settlement-status and webhook-service end-to-end."
  exit 0
else
  bad "$FAILS assertion(s) failed — see logs in $LOG_DIR."; exit 1
fi
