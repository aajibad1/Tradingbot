#!/usr/bin/env bash
# Single-command LOCAL stack — boots the trading wedge + control plane + the
# intelligence/analytics services together and KEEPS THEM RUNNING for interactive
# testing. No GCP, no Postgres, no Clerk: SQLite for the control plane, local
# Redis for risk state, NullPublisher for Pub/Sub (services log instead of
# publishing — the cross-service event flow is driven over HTTP, same as the
# smoke scripts that this generalizes).
#
# This is the "deploy locally to test" entrypoint: bring it up, then curl the
# endpoints, point the frontend at core-api, or run the smokes against it.
#
# Requirements: python3 with each service's requirements installed, a reachable
# Redis (redis-cli + server), curl.
#
# Usage:
#   ./scripts/local_stack.sh            # boot, print the port map, wait for Ctrl-C
#   REDIS_URL=redis://localhost:6379/0 ./scripts/local_stack.sh
#
# Everything stops when you Ctrl-C (or the script exits).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
CAPITAL_USD="${CAPITAL_USD:-100000}"
RESET_TOKEN="${KILL_SWITCH_RESET_TOKEN:-dev-local-token}"
TMP="$(mktemp -d)"
PIDS=()
declare -a STARTED   # "name port" for the summary + status targets

c_ok()  { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
c_warn(){ printf '\033[1;33m! %s\033[0m\n' "$*"; }
c_info(){ printf '\033[1;36m%s\033[0m\n' "$*"; }
die()   { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

cleanup() {
  c_info "== Shutting down local stack =="
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
  c_info "Logs kept in: $TMP"
}
trap cleanup EXIT INT TERM

command -v redis-cli >/dev/null || die "redis-cli not found — install Redis (brew install redis)."
redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1 \
  || die "Redis not reachable at $REDIS_URL — start one: 'redis-server --daemonize yes' or 'docker run -p 6379:6379 redis:7'."
c_ok "Redis reachable at $REDIS_URL"

# Seed risk state so risk-engine /evaluate works and exchange-health passes.
redis-cli -u "$REDIS_URL" set risk:capital_usd "$CAPITAL_USD" >/dev/null
redis-cli -u "$REDIS_URL" set risk:daily_pnl_usd 0 >/dev/null
redis-cli -u "$REDIS_URL" del risk:kill_switch:active risk:kill_switch:metadata risk:open_positions >/dev/null
redis-cli -u "$REDIS_URL" set health:latency:kraken 50 >/dev/null
redis-cli -u "$REDIS_URL" set health:latency:hyperliquid 50 >/dev/null
c_ok "Seeded risk state (capital=\$$CAPITAL_USD)"

# start <name> <dir> <port> [EXTRA_ENV=val ...]
# Always: GCP_PROJECT_ID unset (NullPublisher), repo on PYTHONPATH, logs to $TMP.
start() {
  local name="$1" dir="$2" port="$3"; shift 3
  ( cd "$REPO_ROOT/services/$dir" \
      && exec env -u GCP_PROJECT_ID PYTHONPATH="$REPO_ROOT:." REDIS_URL="$REDIS_URL" "$@" \
         python3 -m uvicorn main:app --host 127.0.0.1 --port "$port" \
         >"$TMP/$dir.log" 2>&1 ) &
  PIDS+=("$!")
}

wait_healthy() {  # name port  -> 0 if /healthz 200 within ~20s
  local name="$1" port="$2"
  for _ in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  return 1
}

# Bring up <name> <dir> <port> [env...]; record it if healthy, warn (non-fatal) if not.
bring_up() {
  local name="$1" dir="$2" port="$3"; shift 3
  start "$name" "$dir" "$port" "$@"
  if wait_healthy "$name" "$port"; then
    c_ok "$name → http://127.0.0.1:$port"
    STARTED+=("$name $port")
  else
    c_warn "$name failed health on :$port (see $TMP/$dir.log) — continuing"
  fi
}

SQLITE() { echo "DATABASE_URL=sqlite:///$TMP/$1.db"; }

c_info "== Booting local stack (SQLite + Redis + NullPublisher) =="

# Control plane (SQLite). core-api talks to accounts-service over real HTTP.
bring_up accounts-service accounts-service 8090 "$(SQLITE accounts-service)"
bring_up core-api core-api 8080 "$(SQLITE core-api)" \
  ACCOUNTS_SERVICE_URL="http://127.0.0.1:8090" STRIPE_WEBHOOK_SECRET="whsec_local"

# Execution wedge.
bring_up risk-engine risk-engine 8082 KILL_SWITCH_RESET_TOKEN="$RESET_TOKEN"
bring_up paper-trader paper-trader 8081
bring_up opportunity-engine opportunity-engine 8083
bring_up trade-ledger trade-ledger 8084

# Advisory / analytics (non-critical).
bring_up opportunity-ranker opportunity-ranker 8085 "$(SQLITE opportunity-ranker)"
bring_up route-optimizer route-optimizer 8086

# Agent / intelligence plane — A2A (Agent2Agent) mesh. Ports match
# shared/a2a/registry.py defaults so client_for(name) resolves against these
# running services. Each serves GET /.well-known/agent-card.json + POST /a2a.
# agent-registry consults agent-evals OVER A2A for eval-gated activation, so
# evals is booted first and the registry is pointed at it.
bring_up debate-service debate-service 8340
bring_up approval-gate-service approval-gate-service 8341
bring_up agent-evals agent-evals 8343
bring_up agent-registry agent-registry 8342 A2A_AGENT_EVALS_URL="http://127.0.0.1:8343"
bring_up ai-ops-agent ai-ops-agent 8344

# Status aggregator LAST — point it at the mesh it just brought up so /status and
# /slo reflect the real local services (advisory ones only DEGRADE, never DOWN).
targets=""; noncrit="opportunity-ranker,route-optimizer,trade-ledger,opportunity-engine,debate-service,approval-gate-service,agent-registry,agent-evals,ai-ops-agent"
for entry in "${STARTED[@]}"; do
  set -- $entry; targets+="${targets:+,}$1=http://127.0.0.1:$2"
done
bring_up status-service status-service 8087 \
  STATUS_TARGETS="$targets" STATUS_NONCRITICAL="$noncrit"

echo
c_info "== Local stack is UP (${#STARTED[@]} services) =="
printf '  %-22s %s\n' "core-api" "http://127.0.0.1:8080   (onboarding, billing, dashboard)"
printf '  %-22s %s\n' "risk-engine" "http://127.0.0.1:8082   /evaluate /state /kill-switch"
printf '  %-22s %s\n' "trade-ledger" "http://127.0.0.1:8084   /ml-export/{decisions,funnel,advisory-scorecard,route-quality}"
printf '  %-22s %s\n' "route-optimizer" "http://127.0.0.1:8086   POST /rank"
printf '  %-22s %s\n' "status-service" "http://127.0.0.1:8087   /status  /slo/catalog  POST /slo/evaluate"
printf '  %-22s %s\n' "A2A agents" "debate :8340  approval-gate :8341  registry :8342  evals :8343  ai-ops :8344"
echo
c_info "Try it:"
echo "  curl -s 127.0.0.1:8087/status | python3 -m json.tool"
echo "  curl -s 127.0.0.1:8087/slo/catalog | python3 -m json.tool"
echo "  curl -s 127.0.0.1:8080/healthz"
echo "  curl -s 127.0.0.1:8340/.well-known/agent-card.json | python3 -m json.tool   # A2A discovery"
echo "  Frontend:  cd apps/frontend && npm install && npm run dev   (expects core-api on :8080)"
echo
c_info "Press Ctrl-C to stop."
wait
