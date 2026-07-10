#!/usr/bin/env bash
# Signal-plane smoke — proves the hybrid chain against LIVE services (docs/03):
#
#   movement-feature-builder → [regime-classifier] → signal-engine → opportunity-engine
#                                                         └→ Topic.SIGNALS (journal)
#
#   features    : synthetic momentum window → movement features
#   regime      : /detect omits regime → auto-classified (trending) → reversion gated
#   journal     : every emitted signal published to arb-signals (NullPublisher log)
#                 with the response's signal_id
#   ingestion   : strong signal → DIRECTIONAL opportunity published (execute=False);
#                 weak signal → refused below the publish threshold
#   fail-soft   : regime-classifier killed → /detect still answers, ungated
#
# No GCP/Redis (NullPublisher). Requirements: python3 + each service's deps, curl.
# Usage: ./scripts/signal_plane_smoke.sh   NOTE: indexed arrays only (macOS bash 3.2).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES=${FEATURES:-8370} SIGENG=${SIGENG:-8371} OPPENG=${OPPENG:-8372} REGIME=${REGIME:-8380}
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

note "Booting the signal plane"
start movement-feature-builder "$FEATURES"
start regime-classifier        "$REGIME"
start signal-engine            "$SIGENG" REGIME_CLASSIFIER_URL="$L:$REGIME"
start opportunity-engine       "$OPPENG"
REGIME_PID="${PIDS[1]}"   # killed later for the fail-soft check
wait_healthy movement-feature-builder "$FEATURES"
wait_healthy regime-classifier "$REGIME"
wait_healthy signal-engine "$SIGENG"
wait_healthy opportunity-engine "$OPPENG"

# ── 1) features: synthetic momentum window → movement features ────────────────
note "1) movement-feature-builder: momentum window → features"
FEATS=$(python3 - "$L:$FEATURES" <<'PY'
import json, sys, urllib.request
base = sys.argv[1]
now = 1_700_000_060_000
window = []
for i in range(30):
    px = 60_000 + i * 45                      # upward grind → momentum
    ts = now - 200 - (29 - i) * 180           # fresh quotes (staleness 200ms)
    window.append({"ts_ms": ts, "venue": "kraken", "bid": px - 1, "ask": px + 1,
                   "bid_size": 9.0, "ask_size": 1.0})
    window.append({"ts_ms": ts, "venue": "crypto.com", "bid": px - 401, "ask": px - 399,
                   "bid_size": 9.0, "ask_size": 1.0})   # lagging venue ~66bps
req = urllib.request.Request(f"{base}/features",
    json.dumps({"symbol": "BTC/USD:PERP", "now_ms": now, "window": window}).encode(),
    {"content-type": "application/json"})
print(json.dumps(json.load(urllib.request.urlopen(req))))
PY
)
MOM=$(echo "$FEATS" | python3 -c "import sys,json;print(json.load(sys.stdin)['momentum_bps'] > 30)")
[[ "$MOM" == True ]] && ok "features carry momentum (>30bps)" || bad "no momentum in features: $FEATS"

# ── 2) regime auto-classified; incompatible family gated ─────────────────────
note "2) signal-engine: regime omitted → classified + gated"
DET=$(curl -s "$L:$SIGENG/detect" -H 'content-type: application/json' -d "$FEATS")
read -r DREG DFAMS DSID <<<"$(echo "$DET" | python3 -c "
import sys,json
d=json.load(sys.stdin)
fams=[s['family'] for s in d['signals']]
print(d.get('regime'), ','.join(fams), d['signals'][0]['signal_id'] if d['signals'] else '-')")"
[[ "$DREG" == trending ]] && ok "regime auto-classified: trending" || bad "expected trending, got '$DREG'"
[[ ",$DFAMS," == *",momentum_dislocation,"* ]] && ok "momentum family emitted" || bad "momentum missing ($DFAMS)"
[[ ",$DFAMS," != *",stat_arb_reversion,"* ]] && ok "reversion gated by regime" || bad "reversion leaked through trending gate"

# ── 3) journal: emitted signal published to arb-signals with its id ──────────
note "3) journal: arb-signals fact carries the response signal_id"
if grep -q "arb-signals" "$LOG_DIR/signal-engine.log" && grep -q "$DSID" "$LOG_DIR/signal-engine.log"; then
  ok "signal $DSID journaled to arb-signals (NullPublisher)"
else
  bad "signal journal missing for $DSID"
fi

# ── 4) ingestion: strong published (execute=False), weak refused ─────────────
note "4) opportunity-engine /signal: threshold + risk-gated publish"
P1=$(curl -s "$L:$OPPENG/signal" -H 'content-type: application/json' \
  -d '{"symbol":"BTC/USD:PERP","direction":"long","gross_edge_bps":75.0,"confidence":0.9,"expiry_ms":3600000}' \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['published'], d['net_edge_bps'])")
read -r PUB1 NET1 <<<"$P1"
[[ "$PUB1" == True ]] && ok "75bps signal → net ${NET1} → published" || bad "strong signal not published ($P1)"
grep -q "arb-opportunities.*directional.*'execute': False" "$LOG_DIR/opportunity-engine.log" \
  && ok "published as DIRECTIONAL with execute=False (risk-engine stays the gate)" \
  || bad "no directional execute=False publish in engine log"
P2=$(curl -s "$L:$OPPENG/signal" -H 'content-type: application/json' \
  -d '{"symbol":"BTC/USD:PERP","direction":"long","gross_edge_bps":20.0,"confidence":0.5,"expiry_ms":2000}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['published'])")
[[ "$P2" == False ]] && ok "20bps signal → refused below the 50bps bar" || bad "weak signal published"

# ── 5) fail-soft: classifier down → detection continues ungated ──────────────
note "5) regime-classifier killed → /detect answers ungated"
kill "$REGIME_PID" 2>/dev/null; sleep 1
DET2=$(curl -s "$L:$SIGENG/detect" -H 'content-type: application/json' -d "$FEATS" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('regime'), len(d['signals']))")
read -r R2 N2 <<<"$DET2"
[[ "$R2" == None && "$N2" -ge 1 ]] && ok "classifier down → ungated detection still answers ($N2 signal(s))" \
  || bad "expected ungated detection, got regime=$R2 n=$N2"

note "Verdict"
if [[ "$FAILS" -eq 0 ]]; then
  ok "Signal plane holds: features → regime-gated detection → journal → threshold-gated DIRECTIONAL publish (execute=False)."
  exit 0
else
  bad "$FAILS assertion(s) failed — see logs in $LOG_DIR."; exit 1
fi
