#!/usr/bin/env bash
# Deploy the crypto-arb stack to PROD from the CLI — this is the canonical
# deploy path. GitHub Actions only BUILDS images on push; it does not apply
# (terraform-apply-prod is manual-dispatch-only — see .github/workflows/deploy.yml).
#
# What it does, in order:
#   1. preflight — gcloud / terraform / docker present, authed, on the right project
#   2. build + push every service image to Artifact Registry, tagged with the git
#      SHA and :latest                                      [skip with --skip-build]
#   3. terraform init + plan against infra/terraform/environments/prod
#   4. show the plan, ask to confirm, then apply  [--yes skips the prompt,
#      --plan-only stops after the plan]
#   5. print the deployed Cloud Run URLs
#
# Usage:
#   ./scripts/deploy.sh                  # build + plan + (confirm) apply at HEAD SHA
#   ./scripts/deploy.sh --skip-build     # reuse an image tag already in the registry
#   ./scripts/deploy.sh --plan-only      # build (unless skipped) + plan, never apply
#   ./scripts/deploy.sh --yes            # non-interactive apply (automation)
#   ./scripts/deploy.sh --tag <tag>      # deploy a specific tag (e.g. rollback to a SHA)
#   ./scripts/deploy.sh --services a,b   # build only these services (still applies the full stack)
#
# Env overrides (defaults match deploy.yml / environments/prod):
#   GCP_PROJECT_ID=agenuit  GCP_REGION=us-central1  ARTIFACT_REGISTRY_REPO=crypto-arb
#   TF_STATE_BUCKET=agenuit-terraform-state  TF_ENV_DIR=infra/terraform/environments/prod

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROJECT_ID="${GCP_PROJECT_ID:-agenuit}"
REGION="${GCP_REGION:-us-central1}"
AR_REPO="${ARTIFACT_REGISTRY_REPO:-crypto-arb}"
TF_STATE_BUCKET="${TF_STATE_BUCKET:-agenuit-terraform-state}"
TF_ENV_DIR="${TF_ENV_DIR:-infra/terraform/environments/prod}"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}"

# Canonical "what we deploy" list — KEEP ALIGNED with the build matrix in
# .github/workflows/deploy.yml. Each has services/<name>/Dockerfile.
ALL_SERVICES=(
  market-data funding-rate-service opportunity-engine risk-engine
  paper-trader trade-ledger ai-ops-agent execution-orchestrator
  sentiment-service dashboard-api notification-dispatcher fx-rate-service
  core-api accounts-service account-link-service corridor-engine opportunity-ranker signal-engine corridor-intelligence-service debate-service status-service regime-classifier
)

# ── flags ─────────────────────────────────────────────────────────────────────
TAG=""
SKIP_BUILD=false
PLAN_ONLY=false
ASSUME_YES=false
SERVICES=("${ALL_SERVICES[@]}")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)        TAG="${2:?--tag needs a value}"; shift 2 ;;
    --skip-build) SKIP_BUILD=true; shift ;;
    --plan-only)  PLAN_ONLY=true; shift ;;
    --yes|-y)     ASSUME_YES=true; shift ;;
    --services)   IFS=',' read -r -a SERVICES <<< "${2:?--services needs a comma list}"; shift 2 ;;
    -h|--help)    awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)            echo "unknown flag: $1 (try --help)" >&2; exit 2 ;;
  esac
done

note() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. preflight ──────────────────────────────────────────────────────────────
note "Preflight"
for bin in gcloud terraform docker git; do
  command -v "$bin" >/dev/null || die "$bin not found on PATH."
done
gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | grep -q . \
  || die "no active gcloud account — run 'gcloud auth login' and 'gcloud auth application-default login'."
ACTIVE_ACCT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)"

# Resolve the image tag: explicit --tag, else the current commit SHA.
if [[ -z "$TAG" ]]; then
  TAG="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  if ! git -C "$REPO_ROOT" diff --quiet || ! git -C "$REPO_ROOT" diff --cached --quiet; then
    warn "working tree is dirty — deploying tag $TAG (HEAD), which does NOT include uncommitted changes."
  fi
fi

cat <<SUMMARY

  project   : ${PROJECT_ID}
  region    : ${REGION}
  account   : ${ACTIVE_ACCT}
  registry  : ${REGISTRY}
  image tag : ${TAG}
  tf dir    : ${TF_ENV_DIR}  (state bucket: ${TF_STATE_BUCKET})
  build     : $([[ "$SKIP_BUILD" == true ]] && echo "SKIPPED (reuse registry images)" || echo "${#SERVICES[@]} service(s)")
  mode      : $([[ "$PLAN_ONLY" == true ]] && echo "PLAN ONLY (no apply)" || echo "plan + apply")
SUMMARY
ok "Preflight passed"

# ── 2. build + push images ────────────────────────────────────────────────────
if [[ "$SKIP_BUILD" == true ]]; then
  note "Skipping build — expecting ${REGISTRY}/<svc>:${TAG} to already exist"
else
  note "Building + pushing ${#SERVICES[@]} image(s) at tag ${TAG}"
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null
  for svc in "${SERVICES[@]}"; do
    dockerfile="$REPO_ROOT/services/$svc/Dockerfile"
    [[ -f "$dockerfile" ]] || die "no Dockerfile for service '$svc' ($dockerfile)"
    echo "  → $svc"
    docker build -q \
      -f "$dockerfile" \
      -t "${REGISTRY}/${svc}:${TAG}" \
      -t "${REGISTRY}/${svc}:latest" \
      "$REPO_ROOT" >/dev/null
    docker push -q "${REGISTRY}/${svc}:${TAG}" >/dev/null
    docker push -q "${REGISTRY}/${svc}:latest" >/dev/null
    ok "$svc pushed"
  done
fi

# ── 3. terraform init + plan ──────────────────────────────────────────────────
note "Terraform init + plan (${TF_ENV_DIR})"
cd "$REPO_ROOT/$TF_ENV_DIR"
terraform init -input=false -reconfigure \
  -backend-config="bucket=${TF_STATE_BUCKET}" >/dev/null
ok "init complete"

PLAN_FILE="$(mktemp -t tfplan.XXXXXX)"
trap 'rm -f "$PLAN_FILE"' EXIT
terraform plan -input=false -var "image_tag=${TAG}" -out="$PLAN_FILE"

if [[ "$PLAN_ONLY" == true ]]; then
  note "Plan-only mode — not applying. Re-run without --plan-only to deploy."
  exit 0
fi

# ── 4. confirm + apply ────────────────────────────────────────────────────────
if [[ "$ASSUME_YES" != true ]]; then
  note "Review the plan above"
  read -r -p "Apply this plan to PROD (${PROJECT_ID})? type 'yes' to proceed: " reply
  [[ "$reply" == "yes" ]] || die "aborted — nothing applied."
fi

note "Applying"
terraform apply -input=false "$PLAN_FILE"
ok "apply complete"

# ── 5. report ─────────────────────────────────────────────────────────────────
note "Deployed Cloud Run URLs"
terraform output -json cloud_run_service_urls 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(f"  {k:28} {v}") for k,v in sorted(d.items())]' \
  2>/dev/null || warn "could not read cloud_run_service_urls output"

ok "Done — deployed tag ${TAG} to ${PROJECT_ID}"
