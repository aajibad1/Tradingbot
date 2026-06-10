"""Pub/Sub publisher used by every service.

Wraps google-cloud-pubsub with:
  - typed topic enum (no stringly-typed topic names at call sites)
  - JSON-encoded Pydantic payloads
  - structured attributes for filtering
  - sync `publish_blocking` for low-volume events (risk alerts, audit)
  - async `publish` for hot paths (ticks, opportunities)
"""

from __future__ import annotations

import json
import logging
import os
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from google.cloud import pubsub_v1  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)


class Topic(str, Enum):
    MARKET_DATA = "arb-market-data"
    FUNDING_RATES = "arb-funding-rates"
    OPPORTUNITIES = "arb-opportunities"
    # Risk-engine republishes opportunities it APPROVES here, with execute=True.
    # This is the approve→execute bridge: opportunity-engine emits to
    # OPPORTUNITIES (execute=False); risk-engine gates and re-emits approved ones
    # to APPROVED_OPPORTUNITIES; paper-trader / execution-orchestrator consume
    # this topic (never OPPORTUNITIES directly), so only risk-approved trades
    # ever execute. A separate topic avoids risk-engine re-consuming its own output.
    APPROVED_OPPORTUNITIES = "arb-approved"
    RISK_ALERTS = "arb-risk-alerts"
    # Every risk-engine decision (approve AND reject) with its feature snapshot,
    # emitted by ``_on_opportunity`` for the ML substrate (AI Phase A). trade-ledger
    # streams these to ``arb_ml.risk_decisions``; the realized label joins back via
    # opportunity_id. Instrumentation is FAIL-OPEN — a publish failure never blocks
    # the gate. No service gates on this topic; it is data-capture only.
    RISK_DECISIONS = "arb-risk-decisions"
    TRADE_FILLS = "arb-trade-fills"
    AI_PROPOSALS = "arb-ai-proposals"
    AUDIT_LOG = "arb-audit-log"
    # Emitted by ``services/sentiment-service`` on every 4h refresh. The
    # canonical decision signal lives in Redis (``sentiment:*``); this topic
    # exists so the trade-ledger can stream sentiment history into BigQuery
    # for backtesting + dashboard charts. Risk-engine does NOT subscribe —
    # it reads Redis to keep the /evaluate hot path I/O-cheap and synchronous.
    SENTIMENT_EVENTS = "arb-sentiment-events"
    # Africa corridor-engine emits VIABLE corridor opportunities here (alert-only).
    # notification-dispatcher consumes them to alert a human to settle — never
    # auto-executed (settlement is money-transmission/VASP-regulated).
    CORRIDOR_ALERTS = "arb-corridor-alerts"
    # API-plane funding lifecycle (docs/06 on-ramp; docs/07 funding.*).
    # Emitted wrapped in the canonical EventEnvelope (event_type funding.created /
    # funding.processing / funding.completed / funding.failed). SANDBOX-only today:
    # the live Africa rails are gated on money-transmission licensing
    # (docs/REGULATORY_BRIEF.md) — see services/onramp-orchestrator.
    FUNDING_EVENTS = "funding-events"
    # API-plane payout lifecycle (docs/06 off-ramp; docs/07 payout.*). Same envelope
    # + SANDBOX-only stance as FUNDING_EVENTS — see services/offramp-orchestrator.
    PAYOUT_EVENTS = "payout-events"
    # API-plane billing ledger events (docs/06 "billing webhooks and ledger events";
    # docs/07 billing.*). invoice.issued / invoice.paid via the canonical envelope —
    # see services/tenant-billing.
    BILLING_EVENTS = "billing-events"


class EventPublisher:
    def __init__(self, project_id: str | None = None) -> None:
        from google.cloud import pubsub_v1  # type: ignore[attr-defined]  # local import — only needed in prod

        self.project_id = project_id or os.environ["GCP_PROJECT_ID"]
        self.client: pubsub_v1.PublisherClient = pubsub_v1.PublisherClient()

    def _topic_path(self, topic: Topic) -> str:
        return self.client.topic_path(self.project_id, topic.value)

    def publish(
        self,
        topic: Topic,
        payload: BaseModel,
        attributes: dict[str, str] | None = None,
    ) -> str:
        """Blocking publish; waits for and returns the message_id.

        Use for low-volume events where synchronous delivery confirmation
        matters (risk alerts, audit). For per-tick / per-rate hot paths use
        :meth:`publish_nowait` instead — blocking here stalls an async event
        loop for up to 30s on a slow publish.
        """
        data = payload.model_dump_json().encode("utf-8")
        future = self.client.publish(self._topic_path(topic), data, **(attributes or {}))
        return future.result(timeout=30)

    def publish_nowait(
        self,
        topic: Topic,
        payload: BaseModel,
        attributes: dict[str, str] | None = None,
    ) -> None:
        """Fire-and-forget publish for hot paths — never blocks the caller.

        The google-cloud-pubsub client batches in background threads, so we
        hand off the message and attach a callback that logs any delivery
        failure rather than blocking on ``future.result()``. This is the
        ``publish`` for the WebSocket tick loop and the funding poll cycle.
        """
        data = payload.model_dump_json().encode("utf-8")
        future = self.client.publish(self._topic_path(topic), data, **(attributes or {}))
        future.add_done_callback(self._log_publish_error)

    @staticmethod
    def _log_publish_error(future) -> None:
        try:
            future.result()
        except Exception:  # noqa: BLE001 — background delivery error, log only
            logger.exception("async pubsub publish failed")

    def publish_audit(self, source: str, event: str, payload: BaseModel) -> str:
        return self.publish(
            Topic.AUDIT_LOG,
            payload,
            attributes={"source": source, "event": event},
        )

    def publish_event(
        self,
        topic: Topic,
        event_type: str,
        payload: BaseModel | dict,
        *,
        producer: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        version: int = 1,
    ) -> str:
        """Publish a payload wrapped in the canonical event envelope (docs/07).

        Opt-in alternative to :meth:`publish` for new event flows: the message
        data is the full ``EventEnvelope`` JSON, and the envelope's metadata
        (event_type / producer / correlation_id / tenant) is mirrored into Pub/Sub
        attributes for filtering. Returns the message id."""
        from shared.models.event_envelope import EventEnvelope

        env = EventEnvelope.wrap(
            event_type=event_type, payload=payload, producer=producer,
            tenant_id=tenant_id, correlation_id=correlation_id, version=version,
        )
        return self.publish(topic, env, attributes=env.attributes())


class NullPublisher:
    """Stand-in for local/dev runs without GCP credentials. Logs to stdout."""

    def publish(
        self,
        topic: Topic,
        payload: BaseModel,
        attributes: dict[str, str] | None = None,
    ) -> str:
        logger.info(
            "pubsub.null topic=%s attrs=%s payload=%s",
            topic.value,
            attributes or {},
            json.loads(payload.model_dump_json()),
        )
        return "null-message-id"

    def publish_nowait(
        self,
        topic: Topic,
        payload: BaseModel,
        attributes: dict[str, str] | None = None,
    ) -> None:
        """Fire-and-forget mirror of :meth:`EventPublisher.publish_nowait`."""
        self.publish(topic, payload, attributes)

    def publish_audit(self, source: str, event: str, payload: BaseModel) -> str:
        return self.publish(Topic.AUDIT_LOG, payload, {"source": source, "event": event})

    def publish_event(
        self,
        topic: Topic,
        event_type: str,
        payload: BaseModel | dict,
        *,
        producer: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        version: int = 1,
    ) -> str:
        """Local mirror of :meth:`EventPublisher.publish_event` (logs to stdout)."""
        from shared.models.event_envelope import EventEnvelope

        env = EventEnvelope.wrap(
            event_type=event_type, payload=payload, producer=producer,
            tenant_id=tenant_id, correlation_id=correlation_id, version=version,
        )
        return self.publish(topic, env, attributes=env.attributes())


def pubsub_project_id() -> str | None:
    """Project id to use for Pub/Sub, or None when Pub/Sub is disabled.

    Real GCP keys off ``GCP_PROJECT_ID``. The LOCAL emulator keys off
    ``PUBSUB_EMULATOR_HOST`` and uses ``PUBSUB_PROJECT_ID`` (default ``local-dev``)
    — deliberately decoupled from ``GCP_PROJECT_ID`` so the emulator can carry the
    real event flow locally while BigQuery (trade-ledger) and Secret Manager
    (market-data) stay in their no-cloud local modes (those still gate on
    ``GCP_PROJECT_ID``). The google-cloud-pubsub client auto-routes to the emulator
    whenever ``PUBSUB_EMULATOR_HOST`` is set, with anonymous credentials."""
    explicit = os.environ.get("GCP_PROJECT_ID")
    if explicit:
        return explicit
    if os.environ.get("PUBSUB_EMULATOR_HOST"):
        return os.environ.get("PUBSUB_PROJECT_ID", "local-dev")
    return None


def pubsub_enabled() -> bool:
    """True when a real or emulated Pub/Sub backend is configured."""
    return pubsub_project_id() is not None


def get_publisher() -> EventPublisher | NullPublisher:
    """Factory — NullPublisher unless real or emulated Pub/Sub is configured."""
    project_id = pubsub_project_id()
    if project_id is None:
        return NullPublisher()
    return EventPublisher(project_id=project_id)
