"""execution-orchestrator — routes risk-approved opportunities through human approval to Hummingbot.

Inbound:
  arb-opportunities  (Pub/Sub) — execute=True only; the rest are dropped.

Endpoints:
  GET  /healthz
  POST /opportunities/submit               — internal: enqueue an opportunity (manual ops)
  POST /slack/interaction                  — Slack interaction webhook
  POST /tick                               — drain approved opps + sweep expired (cron-ping)
  GET  /pending                            — list outstanding approval requests
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from approval_gate import (
    ApprovalStatus,
    KEY_PREFIX_PENDING,
    PendingApproval,
    decide,
    drain_approved,
    submit_for_approval,
    sweep_expired,
    verify_slack_signature,
    _redis,
)
from hummingbot_client import HummingbotClient, HummingbotOrderRequest
from shared.models.opportunity import Opportunity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("execution-orchestrator")


_subscriber_client = None
_futures: list = []


def _on_opportunity(message) -> None:
    try:
        opp = Opportunity(**json.loads(message.data.decode("utf-8")))
        if not opp.execute:
            # Risk-engine has not approved this one — drop silently.
            message.ack()
            return
        submit_for_approval(opp)
        message.ack()
    except Exception:
        logger.exception("failed to process opportunity")
        message.nack()


def _start_subscribers() -> None:
    global _subscriber_client
    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        logger.warning("GCP_PROJECT_ID unset — orchestrator running without Pub/Sub")
        return
    from google.cloud import pubsub_v1

    _subscriber_client = pubsub_v1.SubscriberClient()
    sub = os.environ.get("OPPORTUNITY_SUBSCRIPTION", "arb-opportunities-orchestrator")
    path = _subscriber_client.subscription_path(project_id, sub)
    future = _subscriber_client.subscribe(path, callback=_on_opportunity)
    _futures.append(future)
    logger.info("subscribed: %s", path)


def _stop_subscribers() -> None:
    for f in _futures:
        f.cancel()
    if _subscriber_client is not None:
        _subscriber_client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_subscribers()
    try:
        yield
    finally:
        _stop_subscribers()


app = FastAPI(title="execution-orchestrator", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/opportunities/submit", response_model=PendingApproval)
def submit(opp: Opportunity) -> PendingApproval:
    if not opp.execute:
        raise HTTPException(
            status_code=422,
            detail="opportunity.execute must be True (risk-engine must approve first)",
        )
    return submit_for_approval(opp)


@app.post("/slack/interaction")
async def slack_interaction(request: Request) -> dict[str, Any]:
    body = await request.body()
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET")
    if not signing_secret:
        raise HTTPException(status_code=500, detail="SLACK_SIGNING_SECRET unset")
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")
    if not verify_slack_signature(signing_secret, timestamp, body, signature):
        raise HTTPException(status_code=401, detail="invalid slack signature")

    form = await request.form()
    payload = json.loads(form["payload"])
    user = payload.get("user", {}).get("username", "unknown")
    actions = payload.get("actions", [])
    if not actions:
        raise HTTPException(status_code=400, detail="no action in payload")

    action_value: str = actions[0]["value"]
    verb, _, opportunity_id = action_value.partition(":")
    if verb == "approve":
        status = ApprovalStatus.APPROVED
    elif verb == "reject":
        status = ApprovalStatus.REJECTED
    else:
        raise HTTPException(status_code=400, detail=f"unknown action {verb!r}")

    pending = decide(opportunity_id, status, decided_by=user)
    if pending is None:
        return {"text": f":warning: opportunity `{opportunity_id}` not found or expired"}
    return {
        "replace_original": True,
        "text": (
            f":white_check_mark: Approved by {user}"
            if status is ApprovalStatus.APPROVED
            else f":x: Rejected by {user}"
        ),
    }


@app.post("/tick")
def tick() -> dict[str, Any]:
    """Drain approved opportunities to Hummingbot + sweep expired pending."""
    expired = sweep_expired()
    submitted: list[str] = []
    client = HummingbotClient()
    bot_id = os.environ.get("HUMMINGBOT_BOT_ID", "arb-funding-rate")
    for pending in drain_approved():
        try:
            request = HummingbotOrderRequest.from_opportunity(bot_id, pending.opportunity)
            client.submit_order(request)
            submitted.append(pending.opportunity_id)
        except Exception:
            logger.exception("hummingbot submit failed id=%s", pending.opportunity_id)
    return {"submitted": submitted, "expired": expired, "tick_at": datetime.utcnow().isoformat()}


@app.get("/pending")
def list_pending() -> dict[str, Any]:
    redis = _redis()
    out: list[dict[str, Any]] = []
    for key in redis.scan_iter(match=f"{KEY_PREFIX_PENDING}*"):
        raw = redis.get(key)
        if raw:
            out.append(json.loads(raw))
    return {"pending": out}
