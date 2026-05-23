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
    from google.cloud import pubsub_v1

logger = logging.getLogger(__name__)


class Topic(str, Enum):
    MARKET_DATA = "arb-market-data"
    FUNDING_RATES = "arb-funding-rates"
    OPPORTUNITIES = "arb-opportunities"
    RISK_ALERTS = "arb-risk-alerts"
    TRADE_FILLS = "arb-trade-fills"
    AI_PROPOSALS = "arb-ai-proposals"
    AUDIT_LOG = "arb-audit-log"


class EventPublisher:
    def __init__(self, project_id: str | None = None) -> None:
        from google.cloud import pubsub_v1  # local import — only needed in prod

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
        """Fire-and-forget publish; returns the message_id future result."""
        data = payload.model_dump_json().encode("utf-8")
        future = self.client.publish(self._topic_path(topic), data, **(attributes or {}))
        return future.result(timeout=30)

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

    def publish_audit(self, source: str, event: str, payload: BaseModel) -> str:
        return self.publish(Topic.AUDIT_LOG, payload, {"source": source, "event": event})


def get_publisher() -> EventPublisher | NullPublisher:
    """Factory — returns NullPublisher when GCP_PROJECT_ID is unset."""
    if not os.environ.get("GCP_PROJECT_ID"):
        return NullPublisher()
    return EventPublisher()
