"""account-link-service — "Connect Exchange" onboarding broker (SKELETON).

Brokers the handshake with an account-connection aggregator (Mesh Connect /
Front Finance) so a user can link an exchange via a hosted OAuth-style widget
instead of pasting API keys. The aggregator returns scoped, withdrawal-disabled
credentials which this service — the SOLE writer of ``user-{uid}-{ex}-*``
secrets — persists into Secret Manager. Everything downstream
(``get_exchange_credentials`` → CCXT) is already tenant-aware and unchanged.

Full design + sequencing + security invariants: docs/EXCHANGE_ONBOARDING.md.

STATUS: skeleton. Every business endpoint returns HTTP 501 Not Implemented.
This is a Phase-4 (platform services) pickup per docs/MULTI_TENANCY.md — it
depends on Firebase auth + Firestore config landing first. Scaffolded now so the
service shape, routes, and invariants are pinned down.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request

from models import LinkStatus, SessionRequest, SessionResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("account-link-service")

app = FastAPI(title="account-link-service", version="0.0.1-skeleton")

_NOT_IMPLEMENTED = "not implemented — Phase 4 (platform services); see docs/EXCHANGE_ONBOARDING.md"


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/link/session", response_model=SessionResponse)
def create_session(req: SessionRequest) -> SessionResponse:
    """Ask the aggregator for a hosted-widget session scoped to trade+read for
    ``{user_id, exchange}`` and return the URL the frontend opens.

    TODO: call Mesh/Front session API; scope = READ+TRADE (never WITHDRAWAL).
    """
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@app.post("/link/callback")
async def link_callback(request: Request) -> dict[str, str]:
    """Aggregator webhook: receive scoped credentials for ``{user_id, exchange}``.

    TODO (in order — see docs/EXCHANGE_ONBOARDING.md security invariants):
      1. verify the webhook signature (aggregator secret in Secret Manager)
      2. assert granted scope is READ/TRADE and NOT withdrawal — else reject
      3. AddVersion ``user-{uid}-{ex}-key`` / ``-secret`` (/ ``-password``)
         via Secret Manager (this service is the SOLE writer of ``user-*``)
      4. grant the tenant's Cloud Run SA ``secretAccessor`` on those ids
      5. record 'connected' in per-user config (Firestore)
    """
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@app.get("/link/status", response_model=LinkStatus)
def link_status(user_id: str, exchange: str) -> LinkStatus:
    """Report whether ``{user_id, exchange}`` is connected and with what scope."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@app.delete("/link/{user_id}/{exchange}")
def revoke_link(user_id: str, exchange: str) -> dict[str, str]:
    """Revoke at the aggregator and disable the user's secret versions."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)
