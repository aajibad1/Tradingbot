#!/usr/bin/env bash
# One-time setup of the Artifact Registry repository used by .github/workflows/deploy.yml.
#
# Idempotent — re-running after the repo exists prints a notice and exits 0.
#
# Required:
#   * gcloud CLI authenticated as a principal with roles/artifactregistry.admin
#     on the agenuit project.
#   * The Artifact Registry API enabled (see infra/terraform/ if not).
#
# After this completes, the matrix build in .github/workflows/deploy.yml can push
# images to: us-central1-docker.pkg.dev/agenuit/crypto-arb/<service>:<sha>

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-agenuit}"
LOCATION="${GCP_REGION:-us-central1}"
REPO="${ARTIFACT_REGISTRY_REPO:-crypto-arb}"

echo "Setting up Artifact Registry"
echo "  project:  ${PROJECT_ID}"
echo "  location: ${LOCATION}"
echo "  repo:     ${REPO}"

if gcloud artifacts repositories describe "${REPO}" \
      --location="${LOCATION}" \
      --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Repository ${REPO} already exists in ${LOCATION} — nothing to do."
  exit 0
fi

gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${LOCATION}" \
  --description="Crypto arbitrage system Docker images" \
  --project="${PROJECT_ID}"

echo "Created ${LOCATION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"
echo "Next step: configure local Docker auth with:"
echo "  gcloud auth configure-docker ${LOCATION}-docker.pkg.dev --quiet"
