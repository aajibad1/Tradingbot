#!/usr/bin/env bash
# A2A (Agent2Agent) smoke — proves the agent↔agent contract against LIVE services.
# Boots all five A2A-speaking agents and exercises, over real HTTP:
#
#   discovery   : GET /.well-known/agent-card.json advertises each agent's skills;
#                 shared discover_agents() catalogs the live roster + routes by skill
#   message/send: debate (verify-claim), approval-gate (evaluate-action),
#                 agent-evals (eval-verdict), agent-registry (resolve-active-version)
#   invariants  : ai-ops NEVER-tier tools are neither advertised nor invocable;
#                 approval-gate withdrawals stay hard-blocked
#   consumer    : agent-registry consults agent-evals OVER A2A before activation
#                 (both wired-by-env and found-by-capability-discovery)
#
# No GCP/Redis (in-memory sandbox). Requirements: python3 + each service's deps, curl.
# Usage: ./scripts/a2a_smoke.sh        NOTE: indexed arrays only (macOS bash 3.2).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Default ports match shared/a2a/registry.py so client_for(name) would resolve here.
DEBATE=${DEBATE:-8340} GATE=${GATE:-8341} REG=${REG:-8342} EVALS=${EVALS:-8343} OPS=${OPS:-8344}
CORRIDOR=${CORRIDOR:-8345}   # an A2A *consumer* (not an agent): consults debate
STATUS=${STATUS:-8347}       # status-service: surfaces the live roster at /a2a/roster
REG2=${REG2:-8348}           # second registry with NO evals env: proves the
                             # capability-discovery fallback (eval-verdict) live
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

# --- A2A helpers (JSON-RPC over HTTP) ----------------------------------------
send_text() {  # port text  → raw JSON-RPC response
  curl -s "$L:$1/a2a" -H 'content-type: application/json' -d \
"{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"message/send\",\"params\":{\"message\":{\"kind\":\"message\",\"role\":\"user\",\"parts\":[{\"kind\":\"text\",\"text\":\"$2\"}],\"messageId\":\"m1\"}}}"
}
send_data() {  # port json-data  → raw JSON-RPC response
  curl -s "$L:$1/a2a" -H 'content-type: application/json' -d \
"{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"message/send\",\"params\":{\"message\":{\"kind\":\"message\",\"role\":\"user\",\"parts\":[{\"kind\":\"data\",\"data\":$2}],\"messageId\":\"m1\"}}}"
}
card_name()   { python3 -c "import sys,json;print(json.load(sys.stdin).get('name',''))"; }
card_skills() { python3 -c "import sys,json;print(' '.join(s['id'] for s in json.load(sys.stdin).get('skills',[])))"; }
rpc_state()   { python3 -c "import sys,json;d=json.load(sys.stdin).get('result') or {};print(d.get('status',{}).get('state',''))"; }
rpc_err()     { python3 -c "import sys,json;print((json.load(sys.stdin).get('error') or {}).get('code',''))"; }
rpc_dkey()    { python3 -c "
import sys,json
d=json.load(sys.stdin).get('result') or {}
parts=(d.get('status',{}).get('message') or {}).get('parts',[])
data=next((p.get('data',{}) for p in parts if p.get('kind')=='data'), {})
print(data.get('$1',''))"; }

note "Booting the 5 A2A agents"
start debate-service         "$DEBATE"
start approval-gate-service  "$GATE"
start agent-evals            "$EVALS"
start agent-registry         "$REG" \
  A2A_AGENT_EVALS_URL="$L:$EVALS" \
  A2A_DEBATE_SERVICE_URL="$L:$DEBATE" \
  A2A_APPROVAL_GATE_SERVICE_URL="$L:$GATE" \
  A2A_AGENT_REGISTRY_URL="$L:$REG" \
  A2A_AI_OPS_AGENT_URL="$L:$OPS"
start ai-ops-agent           "$OPS"
# Second registry with NO evals env — activation must DISCOVER the eval authority
# by capability (eval-verdict). Discovery hits evals at its registry.py default
# (8343), so this instance only boots when EVALS is unoverridden.
if [[ "$EVALS" == 8343 ]]; then
  start agent-registry "$REG2" \
    A2A_DEBATE_SERVICE_URL="$L:$DEBATE" \
    A2A_APPROVAL_GATE_SERVICE_URL="$L:$GATE" \
    A2A_AI_OPS_AGENT_URL="$L:$OPS"
fi
# corridor-intelligence is an A2A consumer: point it at debate-service so /assess
# adversarially-verifies the reliability claim over A2A.
start corridor-intelligence-service "$CORRIDOR" A2A_DEBATE_SERVICE_URL="$L:$DEBATE"
# status-service is an A2A *consumer*: /a2a/roster surfaces the live mesh. Point it
# at every agent so roster_catalog resolves the booted ports regardless of overrides.
start status-service "$STATUS" \
  A2A_DEBATE_SERVICE_URL="$L:$DEBATE" \
  A2A_APPROVAL_GATE_SERVICE_URL="$L:$GATE" \
  A2A_AGENT_REGISTRY_URL="$L:$REG" \
  A2A_AGENT_EVALS_URL="$L:$EVALS" \
  A2A_AI_OPS_AGENT_URL="$L:$OPS"
wait_healthy debate-service "$DEBATE"
wait_healthy approval-gate-service "$GATE"
wait_healthy agent-evals "$EVALS"
wait_healthy agent-registry "$REG"
wait_healthy ai-ops-agent "$OPS"
[[ "$EVALS" == 8343 ]] && wait_healthy agent-registry "$REG2"
wait_healthy corridor-intelligence-service "$CORRIDOR"
wait_healthy status-service "$STATUS"

# ── 1) discovery: every agent advertises a card + skills ─────────────────────
note "1) discovery: Agent Cards advertise skills"
check_card() {  # base expected-name expected-skill
  local name skills
  name=$(curl -s "$L:$1/.well-known/agent-card.json" | card_name)
  skills=$(curl -s "$L:$1/.well-known/agent-card.json" | card_skills)
  if [[ "$name" == "$2" && " $skills " == *" $3 "* ]]; then ok "$2 card → skill $3"
  else bad "$2 card wrong (name=$name skills=$skills)"; fi
}
check_card "$DEBATE" debate-service verify-claim
check_card "$GATE"   approval-gate-service evaluate-action
check_card "$EVALS"  agent-evals eval-verdict
check_card "$REG"    agent-registry resolve-active-version
check_card "$OPS"    ai-ops-agent get_balances

# ── 1b) roster-wide discovery: the shared library catalogs the LIVE agents ───
note "1b) shared discover_agents() catalogs the live roster + routes by skill"
DISC=$(env -u GCP_PROJECT_ID \
  A2A_DEBATE_SERVICE_URL="$L:$DEBATE" \
  A2A_APPROVAL_GATE_SERVICE_URL="$L:$GATE" \
  A2A_AGENT_REGISTRY_URL="$L:$REG" \
  A2A_AGENT_EVALS_URL="$L:$EVALS" \
  A2A_AI_OPS_AGENT_URL="$L:$OPS" \
  PYTHONPATH="$REPO_ROOT" python3 -c "
from shared.a2a import discover_agents, find_agents_with_skill
cards = discover_agents()
verify = find_agents_with_skill('verify-claim')
print(len(cards), 'debate-service' in cards, ','.join(verify))")
read -r NCARDS HAS_DEBATE VERIFY <<<"$DISC"
[[ "$NCARDS" == 5 ]] && ok "discover_agents() cataloged all 5 live agents" || bad "expected 5 cards, got $NCARDS"
[[ "$HAS_DEBATE" == True ]] && ok "debate-service present in the catalog" || bad "debate-service missing from catalog"
[[ "$VERIFY" == debate-service ]] && ok "find_agents_with_skill(verify-claim) → debate-service" || bad "expected debate-service, got '$VERIFY'"
# the agent-registry projects the same catalog over plain HTTP (browsable / ops view)
CAT=$(curl -s "$L:$REG/v1/a2a/catalog" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['count'], len(d['unreachable']))")
read -r CCOUNT CUNREACH <<<"$CAT"
[[ "$CCOUNT" == 5 ]] && ok "GET /v1/a2a/catalog lists all 5 live agents" || bad "catalog count=$CCOUNT (expected 5)"
[[ "$CUNREACH" == 0 ]] && ok "catalog reports 0 unreachable" || bad "catalog unreachable=$CUNREACH (expected 0)"
# status-service surfaces the same live roster at /a2a/roster (ops mesh view)
ROST=$(curl -s "$L:$STATUS/a2a/roster" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['count'], d['status'])")
read -r RCOUNT RSTATUS <<<"$ROST"
[[ "$RCOUNT" == 5 && "$RSTATUS" == ok ]] && ok "status-service /a2a/roster → 5 agents, status ok" || bad "roster count=$RCOUNT status=$RSTATUS (expected 5/ok)"

# ── 2) debate: verify a claim → calibrated verdict task ──────────────────────
note "2) debate-service: message/send → verdict task"
R=$(send_text "$DEBATE" "funding carry is profitable")
ST=$(echo "$R" | rpc_state); DEC=$(echo "$R" | rpc_dkey decision)
case " support reject uncertain " in
  *" $DEC "*) [[ "$ST" == completed ]] && ok "verdict task completed (decision=$DEC)" || bad "state=$ST";;
  *) bad "unexpected decision=$DEC (state=$ST)";;
esac

# ── 3) approval-gate: evaluate-action over A2A ───────────────────────────────
note "3) approval-gate: read auto-approved, withdrawal hard-blocked (over A2A)"
C1=$(send_data "$GATE" '{"agent":"ranker","action_type":"read","summary":"read funding"}' | rpc_dkey classification)
C2=$(send_data "$GATE" '{"agent":"x","action_type":"withdrawal","summary":"pull funds"}' | rpc_dkey classification)
[[ "$C1" == auto_approved ]] && ok "read → auto_approved" || bad "expected auto_approved, got $C1"
[[ "$C2" == blocked ]] && ok "withdrawal → blocked" || bad "expected blocked, got $C2"

# ── 4) ai-ops: NEVER-tier neither advertised nor invocable ───────────────────
note "4) ai-ops-agent: NEVER-tier tools hidden + refused (over A2A)"
SK=$(curl -s "$L:$OPS/.well-known/agent-card.json" | card_skills)
[[ " $SK " != *" withdraw_funds "* ]] && ok "withdraw_funds not advertised" || bad "withdraw_funds leaked into card"
E1=$(send_data "$OPS" '{"tool":"withdraw_funds"}' | rpc_err)
E2=$(send_data "$OPS" '{"tool":"does_not_exist"}' | rpc_err)
[[ "$E1" == "-32602" && "$E1" == "$E2" ]] && ok "NEVER tool refused like an unknown one ($E1)" || bad "expected matching -32602, got $E1/$E2"

# ── 5) consumer: registry consults agent-evals OVER A2A before activation ────
note "5) agent-registry → agent-evals eval-gate (consumer over A2A)"
curl -s "$L:$REG/v1/agents" -H 'content-type: application/json' -d '{"name":"ranker","provider":"claude"}' >/dev/null
curl -s "$L:$REG/v1/agents/ranker/prompts" -H 'content-type: application/json' -d '{"version":"v1","content":"Rank it."}' >/dev/null
C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$L:$REG/v1/agents/ranker/activate" -H 'content-type: application/json' -d '{"version":"v1"}')
[[ "$C" == 409 ]] && ok "un-evaluated → activation 409 (registry asked evals over A2A)" || bad "expected 409, got $C"
curl -s "$L:$EVALS/v1/evals/run" -H 'content-type: application/json' \
  -d '{"agent":"ranker","prompt_version":"v1","metrics":{"accuracy":0.95,"hallucination_rate":0.01,"latency_ms":800}}' >/dev/null
A=$(curl -s "$L:$REG/v1/agents/ranker/activate" -H 'content-type: application/json' -d '{"version":"v1"}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('active',''))")
[[ "$A" == v1 ]] && ok "passing eval (via A2A) → activated v1" || bad "expected active=v1, got $A"

# ── 5b) registry with NO evals env: eval authority found by DISCOVERY ────────
if [[ "$EVALS" == 8343 ]]; then
  note "5b) agent-registry (no evals env) → discovers eval-verdict provider"
  curl -s "$L:$REG2/v1/agents" -H 'content-type: application/json' -d '{"name":"scout","provider":"claude"}' >/dev/null
  curl -s "$L:$REG2/v1/agents/scout/prompts" -H 'content-type: application/json' -d '{"version":"v1","content":"Scout it."}' >/dev/null
  C=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$L:$REG2/v1/agents/scout/activate" -H 'content-type: application/json' -d '{"version":"v1"}')
  [[ "$C" == 409 ]] && ok "un-evaluated → 409 (evals peer found via discovery, gate held)" || bad "expected 409, got $C"
  curl -s "$L:$EVALS/v1/evals/run" -H 'content-type: application/json' \
    -d '{"agent":"scout","prompt_version":"v1","metrics":{"accuracy":0.95,"hallucination_rate":0.01,"latency_ms":700}}' >/dev/null
  A=$(curl -s "$L:$REG2/v1/agents/scout/activate" -H 'content-type: application/json' -d '{"version":"v1"}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('active',''))")
  [[ "$A" == v1 ]] && ok "passing eval (peer via discovery) → activated v1" || bad "expected active=v1, got $A"
else
  note "5b) skipped (EVALS port overridden — discovery fallback needs the 8343 default)"
fi

# ── 6) registry resolve-active-version skill over A2A ────────────────────────
note "6) agent-registry: resolve-active-version over A2A"
AV=$(send_data "$REG" '{"agent":"ranker"}' | rpc_dkey active)
[[ "$AV" == v1 ]] && ok "resolve-active-version → v1" || bad "expected v1, got $AV"

note "7) corridor-intelligence → debate verification (consumer over A2A)"
DV=$(curl -s "$L:$CORRIDOR/assess" -H 'content-type: application/json' -d '{"corridor":"NGN->ZAR"}' \
  | python3 -c "import sys,json;print((json.load(sys.stdin).get('debate') or {}).get('decision',''))")
case " support reject uncertain " in
  *" $DV "*) ok "corridor /assess attached a debate verdict over A2A (decision=$DV)";;
  *) bad "expected a debate verdict on /assess, got '$DV'";;
esac

note "Verdict"
if [[ "$FAILS" -eq 0 ]]; then
  ok "A2A holds: discovery + message/send across 5 agents; invariants + consumer paths (registry→evals by env AND by discovery, corridor→debate)."
  exit 0
else
  bad "$FAILS assertion(s) failed — see logs in $LOG_DIR."; exit 1
fi
