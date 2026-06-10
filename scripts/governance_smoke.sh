#!/usr/bin/env bash
# Agent-governance smoke — proves the doc-10 safety controls against LIVE services.
# No GCP/Redis (in-memory sandbox). Two flows:
#
#   approval-gate : read→auto-approved, sensitive→human, withdrawal→hard-blocked
#   registry+evals: a prompt version cannot be ACTIVATED until it has a passing eval
#
# Requirements: python3 with each service's requirements installed, curl.
# Usage: ./scripts/governance_smoke.sh
#
# NOTE: indexed arrays only (macOS bash 3.2).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE=${GATE:-8261} EVALS=${EVALS:-8262} REG=${REG:-8263}
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
  echo "Logs in: $LOG_DIR"; rm -f "$REPO_ROOT/dump.rdb" 2>/dev/null || true
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
code() {  # method url [data]
  local m="$1" u="$2" d="${3:-}"; local a=(-s -o /dev/null -w '%{http_code}' -X "$m" "$u" -H 'content-type: application/json')
  [[ -n "$d" ]] && a+=(-d "$d"); curl "${a[@]}"
}
field() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1'))"; }

note "Booting governance services"
start approval-gate-service "$GATE"
start agent-evals           "$EVALS"
start agent-registry        "$REG" AGENT_EVALS_URL="$L:$EVALS"
wait_healthy approval-gate-service "$GATE"
wait_healthy agent-evals "$EVALS"
wait_healthy agent-registry "$REG"

# ── approval-gate permission model ───────────────────────────────────────────
note "1) approval-gate: read-only action → auto-approved"
S=$(curl -s "$L:$GATE/v1/proposals" -H 'content-type: application/json' \
  -d '{"agent":"ranker","action_type":"summarize","summary":"summarize opps"}' | field status)
[[ "$S" == "auto_approved" ]] && ok "read → auto_approved" || bad "expected auto_approved, got $S"

note "2) approval-gate: sensitive action → held, then human-approved"
PID=$(curl -s "$L:$GATE/v1/proposals" -H 'content-type: application/json' \
  -d '{"agent":"ai-ops","action_type":"risk_limit_change","summary":"raise max size"}' | field id)
S=$(curl -s "$L:$GATE/v1/proposals/$PID/decide" -H 'content-type: application/json' \
  -d '{"decision":"approve","decided_by":"ada","reason":"reviewed"}' | field status)
[[ "$S" == "approved" ]] && ok "sensitive → human approved" || bad "expected approved, got $S"

note "3) approval-gate: withdrawal → hard-blocked, approve denied (409)"
WID=$(curl -s "$L:$GATE/v1/proposals" -H 'content-type: application/json' \
  -d '{"agent":"ai-ops","action_type":"withdrawal","summary":"move funds out"}' | field id)
BS=$(curl -s "$L:$GATE/v1/proposals/$WID" | field status)
C=$(code POST "$L:$GATE/v1/proposals/$WID/decide" '{"decision":"approve","decided_by":"ada"}')
[[ "$BS" == "blocked" && "$C" == 409 ]] && ok "withdrawal blocked, approve → 409" || bad "expected blocked/409, got $BS/$C"

# ── eval-gated promotion ─────────────────────────────────────────────────────
note "4) registry: register agent + immutable prompt v1"
curl -s "$L:$REG/v1/agents" -H 'content-type: application/json' -d '{"name":"ranker","provider":"claude"}' >/dev/null
curl -s "$L:$REG/v1/agents/ranker/prompts" -H 'content-type: application/json' \
  -d '{"version":"v1","content":"Rank the opportunity."}' >/dev/null
ok "agent + prompt v1 registered"

note "5) activate v1 with NO eval → blocked (409)"
C=$(code POST "$L:$REG/v1/agents/ranker/activate" '{"version":"v1"}')
[[ "$C" == 409 ]] && ok "un-evaluated → activation 409" || bad "expected 409, got $C"

note "6) FAILING eval → still blocked (409)"
curl -s "$L:$EVALS/v1/evals/run" -H 'content-type: application/json' \
  -d '{"agent":"ranker","prompt_version":"v1","metrics":{"accuracy":0.6,"hallucination_rate":0.02,"latency_ms":900}}' >/dev/null
C=$(code POST "$L:$REG/v1/agents/ranker/activate" '{"version":"v1"}')
[[ "$C" == 409 ]] && ok "failing eval → activation 409" || bad "expected 409, got $C"

note "7) PASSING eval → activation succeeds"
curl -s "$L:$EVALS/v1/evals/run" -H 'content-type: application/json' \
  -d '{"agent":"ranker","prompt_version":"v1","metrics":{"accuracy":0.92,"hallucination_rate":0.01,"latency_ms":900}}' >/dev/null
A=$(curl -s "$L:$REG/v1/agents/ranker/activate" -H 'content-type: application/json' -d '{"version":"v1"}' | field active)
[[ "$A" == "v1" ]] && ok "passing eval → activated v1" || bad "expected active=v1, got $A"

note "Verdict"
if [[ "$FAILS" -eq 0 ]]; then
  ok "Governance holds: permission model enforced + no promotion without a passing eval."
  exit 0
else
  bad "$FAILS assertion(s) failed — see logs in $LOG_DIR."; exit 1
fi
