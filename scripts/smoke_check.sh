#!/usr/bin/env bash
# Smoke-check the live crypto-arb deployment on Cloud Run + its dependencies.
#
# Most services are internal-ingress (not reachable from the public internet),
# so we verify them via Cloud Run's CONTROL PLANE — Ready=True and the latest
# revision serving 100% of traffic. Cloud Run runs the container's /healthz
# liveness probe continuously, so a healthy serving revision means /healthz is
# answering. The one public service (dashboard-api) also gets a real HTTP check.
#
# Also checks Redis is READY and the schedulers are ENABLED.
#
# Exit code: 0 if everything is healthy, 1 otherwise (usable in CI / cron / a
# Cloud Scheduler uptime job).
#
# Usage:  ./scripts/smoke_check.sh            (project agenuit, region us-central1)
#         PROJECT=my-proj REGION=us-central1 ./scripts/smoke_check.sh

set -uo pipefail

PROJECT="${PROJECT:-agenuit}"
REGION="${REGION:-us-central1}"
REDIS_INSTANCE="${REDIS_INSTANCE:-arb-risk-redis-prod}"

SERVICES=(market-data funding-rate-service opportunity-engine risk-engine
          paper-trader trade-ledger ai-ops-agent execution-orchestrator
          sentiment-service dashboard-api fx-rate-service notification-dispatcher)
# dashboard-api is the one public-ingress service. It serves the dashboard at /
# and live KPIs at /api/summary (which reads Redis), but has no /healthz route —
# so /api/summary is the meaningful data-plane liveness probe.
PUBLIC_SERVICE="dashboard-api"
PUBLIC_PATH="/api/summary"
SCHEDULERS=(fx-refresh-prod risk-daily-pnl-reset-prod sentiment-refresh-prod)

ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; FAILS=$((FAILS+1)); }
hdr()  { printf '\n\033[1;36m%s\033[0m\n' "$*"; }

FAILS=0

hdr "Cloud Run services (control plane: Ready + serving latest)"
for s in "${SERVICES[@]}"; do
  read -r ready latest serving <<<"$(gcloud run services describe "$s" \
      --project "$PROJECT" --region "$REGION" \
      --format='value(status.conditions[0].status, status.latestCreatedRevisionName, status.traffic[0].revisionName)' 2>/dev/null)"
  if [[ "$ready" != "True" ]]; then
    bad "$s — not Ready (status=${ready:-unknown})"
  elif [[ -n "$latest" && -n "$serving" && "$latest" != "$serving" ]]; then
    bad "$s — Ready but serving $serving, not latest $latest"
  else
    ok "$s — Ready, serving ${serving:-latest}"
  fi
done

hdr "Public endpoint (data plane HTTP $PUBLIC_PATH)"
URL="$(gcloud run services describe "$PUBLIC_SERVICE" --project "$PROJECT" --region "$REGION" \
       --format='value(status.url)' 2>/dev/null)"
if [[ -z "$URL" ]]; then
  bad "$PUBLIC_SERVICE — no URL"
else
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL$PUBLIC_PATH" 2>/dev/null)"
  [[ "$code" == "200" ]] && ok "$PUBLIC_SERVICE $URL$PUBLIC_PATH → 200" || bad "$PUBLIC_SERVICE $PUBLIC_PATH → HTTP ${code:-timeout}"
fi

hdr "Redis (risk-state store)"
state="$(gcloud redis instances describe "$REDIS_INSTANCE" --project "$PROJECT" --region "$REGION" \
         --format='value(state)' 2>/dev/null)"
[[ "$state" == "READY" ]] && ok "$REDIS_INSTANCE — $state" || bad "$REDIS_INSTANCE — state=${state:-not found}"

hdr "Cloud Scheduler jobs (enabled)"
for j in "${SCHEDULERS[@]}"; do
  st="$(gcloud scheduler jobs describe "$j" --project "$PROJECT" --location "$REGION" \
        --format='value(state)' 2>/dev/null)"
  [[ "$st" == "ENABLED" ]] && ok "$j — $st" || bad "$j — state=${st:-not found}"
done

hdr "Result"
if [[ "$FAILS" -eq 0 ]]; then
  printf '\033[1;32mAll healthy.\033[0m\n'; exit 0
else
  printf '\033[1;31m%d check(s) FAILED.\033[0m\n' "$FAILS"; exit 1
fi
