#!/usr/bin/env bash
# Post-deploy wiring for every inter-service HTTP/A2A URL in the system.
#
# Started as API-plane-only (gateway→upstreams, portal/admin→services,
# tenant-billing→metering, agent-registry→evals) and now also covers the
# opt-in fail-soft enrichment calls added since (risk-engine→opportunity-ranker,
# market-data→venue-anomaly-detector→connector-runtime, ai-ops-agent→
# approval-gate-service, corridor-engine→corridor-intelligence-service/
# fx-rate-service, route-optimizer→trade-ledger) plus the one A2A peer that
# isn't safe to leave on capability-discovery alone in prod
# (corridor-intelligence-service→debate-service). A Cloud Run service's URL is
# only known after it's created, so terraform can't set these env vars at
# create time without a cycle (same reason core-api's ACCOUNTS_SERVICE_URL is
# wired post-deploy). Run this ONCE after `terraform apply` (and after any
# service is recreated) to patch the env vars from the live service URLs.
#
# Every URL wired here is a soft, fail-soft enrichment on the consuming
# service's side — an unresolved/empty URL just means that service stays
# quiet, never a hard failure. Only the original API-plane trio
# (partner-auth/api-metering/gateway) is a hard requirement.
#
# Reads URLs via `gcloud run services describe` and patches via
# `gcloud run services update --update-env-vars` (each triggers a new revision).
#
# Usage:  ./scripts/wire_api_plane_urls.sh --region <REGION> [--project <PROJECT>]
#         (falls back to gcloud config for region/project)
#
# NOTE: indexed arrays only (macOS bash 3.2).

set -uo pipefail

REGION="" PROJECT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)  REGION="${2:?}"; shift 2 ;;
    --project) PROJECT="${2:?}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
REGION="${REGION:-$(gcloud config get-value run/region 2>/dev/null)}"
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$REGION" && -n "$PROJECT" ]] || { echo "need --region and a project (gcloud config or --project)" >&2; exit 2; }

ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
info() { printf '\033[1;36m%s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null || die "gcloud not found."

url_of() {  # svc -> https URL (cached lookups are cheap enough)
  gcloud run services describe "$1" --region "$REGION" --project "$PROJECT" \
    --format='value(status.url)' 2>/dev/null
}

# Resolve every URL we need once.
info "Resolving service URLs in $PROJECT/$REGION"
PARTNER_AUTH="$(url_of partner-auth)"
METERING="$(url_of api-metering)"
ONRAMP="$(url_of onramp-orchestrator)"
OFFRAMP="$(url_of offramp-orchestrator)"
WALLET="$(url_of wallet-service)"
ROUTING="$(url_of routing-service)"
SETTLEMENT="$(url_of settlement-status)"
WEBHOOK="$(url_of webhook-service)"
BILLING="$(url_of tenant-billing)"
GATEWAY="$(url_of public-api-gateway)"
STATUS="$(url_of status-service)"
EVALS="$(url_of agent-evals)"
OPP_RANKER="$(url_of opportunity-ranker)"
VENUE_ANOMALY="$(url_of venue-anomaly-detector)"
CONNECTOR_RUNTIME="$(url_of connector-runtime)"
APPROVAL_GATE="$(url_of approval-gate-service)"
CORRIDOR_INTEL="$(url_of corridor-intelligence-service)"
FX_RATE="$(url_of fx-rate-service)"
TRADE_LEDGER="$(url_of trade-ledger)"
DEBATE="$(url_of debate-service)"
COMPLIANCE="$(url_of compliance-service)"

for pair in "partner-auth:$PARTNER_AUTH" "api-metering:$METERING" "gateway:$GATEWAY"; do
  [[ -n "${pair#*:}" ]] || die "could not resolve URL for ${pair%%:*} — is it deployed?"
done

update() {  # svc KEY1=VAL1,KEY2=VAL2,...
  local svc="$1" vars="$2"
  gcloud run services update "$svc" --region "$REGION" --project "$PROJECT" \
    --update-env-vars "$vars" --quiet >/dev/null \
    && ok "$svc ← $vars" || die "failed to update $svc"
}

info "Wiring inter-service URLs"
update public-api-gateway \
  "PARTNER_AUTH_URL=$PARTNER_AUTH,API_METERING_URL=$METERING,ONRAMP_URL=$ONRAMP,OFFRAMP_URL=$OFFRAMP,WALLET_URL=$WALLET,ROUTING_URL=$ROUTING,SETTLEMENT_URL=$SETTLEMENT"
update developer-portal "PARTNER_AUTH_URL=$PARTNER_AUTH,GATEWAY_URL=$GATEWAY"
update admin-console \
  "STATUS_URL=$STATUS,PARTNER_AUTH_URL=$PARTNER_AUTH,API_METERING_URL=$METERING,WEBHOOK_URL=$WEBHOOK,BILLING_URL=$BILLING"
update tenant-billing "API_METERING_URL=$METERING"
update agent-registry "AGENT_EVALS_URL=$EVALS"

info "Wiring opt-in enrichment + A2A URLs (fail-soft on the consuming side)"
update risk-engine "OPPORTUNITY_RANKER_URL=$OPP_RANKER"
update market-data "VENUE_ANOMALY_URL=$VENUE_ANOMALY"
update venue-anomaly-detector "CONNECTOR_RUNTIME_URL=$CONNECTOR_RUNTIME"
update ai-ops-agent "APPROVAL_GATE_URL=$APPROVAL_GATE"
update corridor-engine "CORRIDOR_INTEL_URL=$CORRIDOR_INTEL,FX_RATE_SERVICE_URL=$FX_RATE"
update route-optimizer "TRADE_LEDGER_URL=$TRADE_LEDGER"

# COMPLIANCE_SERVICE_URL is the one exception to "fail-soft on the consuming
# side" above: onramp-orchestrator's screening gate (issue #22) fails CLOSED —
# an order created with a screening_check_id but no reachable compliance-service
# is treated as unverifiable and blocked, not silently allowed through. Leaving
# this unset is safe (the gate only activates per-order, opt-in) but means any
# order that DOES request screening will sit in awaiting_review forever.
update onramp-orchestrator "COMPLIANCE_SERVICE_URL=$COMPLIANCE"
# Explicit pin, not capability discovery — A2A_DISCOVER_DEBATE's find_agents_with_skill
# would itself need every other peer's URL resolvable first; the explicit env always
# wins anyway (see corridor-intelligence-service/main.py:_resolve_debate_base).
update corridor-intelligence-service "A2A_DEBATE_SERVICE_URL=$DEBATE"

info "Done. The API plane is wired. Reach internal UIs via:"
echo "  gcloud run services proxy developer-portal --region $REGION --project $PROJECT"
echo "  gcloud run services proxy admin-console    --region $REGION --project $PROJECT"
