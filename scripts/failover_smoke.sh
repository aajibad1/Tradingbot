#!/usr/bin/env bash
# Cross-service FAILOVER smoke — proves the status-service criticality semantics
# documented in docs/SLOS.md ("Status semantics") against live services. No GCP.
#
# Trust/reliability is the #1 product priority (docs/MASTER_STRATEGY.md). This
# walks the three states a status page must distinguish, by actually killing
# dependencies and asserting both the HTTP code and the reported status:
#
#   1. all healthy            → GET /status  200  status="ok"
#   2. NON-critical dep down  → GET /status  200  status="degraded"  (still serving)
#   3. CRITICAL dep down      → GET /status  503  status="down"      (monitors trip)
#
# Topology: status-service probes Redis (critical) + risk-engine (critical) +
# paper-trader (NON-critical). We kill paper-trader to force "degraded", then
# kill risk-engine to force "down".
#
# Everything runs in local mode (GCP_PROJECT_ID unset → NullPublisher).
# Requirements: a reachable Redis (redis-cli + server on REDIS_URL), python3 with
# each service's requirements installed, and curl.
#
# Usage:
#   ./scripts/failover_smoke.sh
# Override ports / redis if needed:
#   STATUS_PORT=8087 RISK_PORT=8082 PAPER_PORT=8081 REDIS_URL=redis://localhost:6379/0 \
#     ./scripts/failover_smoke.sh
#
# NOTE: uses only indexed arrays (no `declare -A`) so it runs on macOS bash 3.2.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS_PORT="${STATUS_PORT:-8087}"
RISK_PORT="${RISK_PORT:-8082}"
PAPER_PORT="${PAPER_PORT:-8081}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
CAPITAL_USD="${CAPITAL_USD:-100000}"
RESET_TOKEN="dev-local-token"

LOG_DIR="$(mktemp -d)"
PIDS=()              # everything to kill on exit
LAST_PID=""          # set by start_service
RISK_PID="" ; PAPER_PID="" ; STATUS_PID=""
FAILS=0

note() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
bad()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; FAILS=$((FAILS + 1)); }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

cleanup() {
  note "Shutting down"
  for pid in "${PIDS[@]:-}"; do [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "Service logs kept in: $LOG_DIR"
}
trap cleanup EXIT

# ── preflight ────────────────────────────────────────────────────────────────
command -v redis-cli >/dev/null || die "redis-cli not found — install Redis or set REDIS_URL."
command -v curl      >/dev/null || die "curl not found."
redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1 || die "Redis not reachable at $REDIS_URL."
ok "Redis reachable at $REDIS_URL"

# risk-engine fails loud if capital is unset; seed the minimal state it needs.
redis-cli -u "$REDIS_URL" set risk:capital_usd "$CAPITAL_USD" >/dev/null
ok "Seeded risk:capital_usd"

# Start from a deterministic "ok" state: a prior run (or the paper-trade smoke)
# may have left the kill switch tripped, which makes risk-engine /healthz return
# 503 forever. This smoke drives ok→degraded→down, so it MUST begin un-tripped.
redis-cli -u "$REDIS_URL" del risk:kill_switch:active risk:kill_switch:metadata >/dev/null
redis-cli -u "$REDIS_URL" set risk:daily_pnl_usd 0 >/dev/null
ok "Cleared stale kill-switch state"

# ── boot services (local mode: GCP_PROJECT_ID unset → NullPublisher) ─────────
start_service() {  # name port [env...]  → sets LAST_PID
  local name="$1" port="$2"; shift 2
  ( cd "$REPO_ROOT/services/$name" \
      && exec env -u GCP_PROJECT_ID PYTHONPATH="$REPO_ROOT:." REDIS_URL="$REDIS_URL" "$@" \
         python3 -m uvicorn main:app --host 127.0.0.1 --port "$port" \
         >"$LOG_DIR/$name.log" 2>&1 ) &
  LAST_PID=$!
  PIDS+=("$LAST_PID")
}

wait_healthy() {  # name port
  local name="$1" port="$2"
  for _ in $(seq 1 40); do
    curl -fs "127.0.0.1:$port/healthz" >/dev/null 2>&1 && { ok "$name healthy on :$port"; return 0; }
    sleep 0.5
  done
  echo "--- $name log ---"; cat "$LOG_DIR/$name.log" || true
  die "$name did not become healthy on :$port (deps installed?)."
}

wait_gone() {  # port — wait until a /healthz stops answering
  local port="$1"
  for _ in $(seq 1 20); do
    curl -fs "127.0.0.1:$port/healthz" >/dev/null 2>&1 || return 0
    sleep 0.3
  done
}

note "Starting services"
start_service risk-engine  "$RISK_PORT"  KILL_SWITCH_RESET_TOKEN="$RESET_TOKEN" ; RISK_PID=$LAST_PID
start_service paper-trader "$PAPER_PORT"                                         ; PAPER_PID=$LAST_PID
wait_healthy risk-engine  "$RISK_PORT"
wait_healthy paper-trader "$PAPER_PORT"

# status-service probes Redis (critical) + the two services. paper-trader is the
# only NON-critical target, so killing it degrades without going down.
start_service status-service "$STATUS_PORT" \
  STATUS_TARGETS="risk-engine=http://127.0.0.1:$RISK_PORT,paper-trader=http://127.0.0.1:$PAPER_PORT" \
  STATUS_NONCRITICAL="paper-trader"
STATUS_PID=$LAST_PID
wait_healthy status-service "$STATUS_PORT"

# ── assertion helper: GET /status, check HTTP code + status field ────────────
assert_status() {  # expected_code expected_status label
  local want_code="$1" want_status="$2" label="$3"
  local resp code body status
  resp="$(curl -s -w $'\n%{http_code}' "127.0.0.1:$STATUS_PORT/status")"
  code="${resp##*$'\n'}"
  body="${resp%$'\n'*}"
  status="$(printf '%s' "$body" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])' 2>/dev/null || echo '?')"
  if [[ "$code" == "$want_code" && "$status" == "$want_status" ]]; then
    ok "$label → HTTP $code, status=\"$status\""
  else
    bad "$label → HTTP $code, status=\"$status\" (expected HTTP $want_code, status=\"$want_status\")"
    printf '   body: %s\n' "$body"
  fi
}

# ── 1) all healthy → ok / 200 ────────────────────────────────────────────────
note "1) All dependencies healthy"
assert_status 200 ok "all healthy"

# ── 2) non-critical down → degraded / 200 ────────────────────────────────────
note "2) Killing paper-trader (NON-critical) — platform should DEGRADE, not go down"
kill "$PAPER_PID" 2>/dev/null || true
wait "$PAPER_PID" 2>/dev/null || true   # reap quietly (no job-control "Terminated" notice)
wait_gone "$PAPER_PORT"
assert_status 200 degraded "non-critical down"

# ── 3) critical down → down / 503 ────────────────────────────────────────────
note "3) Killing risk-engine (CRITICAL) — status page should go DOWN (503)"
kill "$RISK_PID" 2>/dev/null || true
wait "$RISK_PID" 2>/dev/null || true   # reap quietly (no job-control "Terminated" notice)
wait_gone "$RISK_PORT"
assert_status 503 down "critical down"

# ── verdict ──────────────────────────────────────────────────────────────────
note "Verdict"
if [[ "$FAILS" -eq 0 ]]; then
  ok "Failover semantics hold: ok(200) → degraded(200) → down(503)."
  echo "  This is docs/SLOS.md 'Status semantics' proven against live services."
  exit 0
else
  bad "$FAILS assertion(s) failed — see bodies above and logs in $LOG_DIR."
  exit 1
fi
