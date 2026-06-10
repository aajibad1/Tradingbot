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


def get_publisher() -> EventPublisher | NullPublisher:
    """Factory — returns NullPublisher when GCP_PROJECT_ID is unset."""
    if not os.environ.get("GCP_PROJECT_ID"):
        return NullPublisher()
    return EventPublisher()
