#!/usr/bin/env python3
"""Create all topics + subscriptions in the local Pub/Sub emulator.

The emulator starts empty; nothing flows until topics and subscriptions exist.
This creates every topic from ``shared.pubsub.publisher.Topic`` and the
consumer subscriptions (mirroring the terraform ``subscriptions`` map in
infra/terraform/main.tf). Idempotent — re-running is safe.

Run with the emulator reachable:
  PUBSUB_EMULATOR_HOST=localhost:8085 PUBSUB_PROJECT_ID=local-dev \
    python3 scripts/pubsub_emulator_bootstrap.py
"""

from __future__ import annotations

import os
import sys

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

from shared.pubsub.publisher import Topic

# subscription_id -> topic_id. Keep in sync with infra/terraform/main.tf.
SUBSCRIPTIONS: dict[str, str] = {
    "arb-market-data-opp-engine": "arb-market-data",
    "arb-funding-rates-opp-engine": "arb-funding-rates",
    "arb-opportunities-risk-engine": "arb-opportunities",
    "arb-opportunities-ledger": "arb-opportunities",
    "arb-approved-paper-trader": "arb-approved",
    "arb-approved-orchestrator": "arb-approved",
    "arb-risk-alerts-ledger": "arb-risk-alerts",
    "arb-risk-alerts-ai-ops": "arb-risk-alerts",
    "arb-risk-decisions-ledger": "arb-risk-decisions",
    "arb-market-data-ledger": "arb-market-data",
    "arb-trade-fills-ledger": "arb-trade-fills",
    "arb-trade-fills-risk-engine": "arb-trade-fills",
    "arb-ai-proposals-ledger": "arb-ai-proposals",
    "arb-audit-log-ledger": "arb-audit-log",
    "arb-corridor-alerts-dispatcher": "arb-corridor-alerts",
}


def main() -> int:
    if not os.environ.get("PUBSUB_EMULATOR_HOST"):
        print("PUBSUB_EMULATOR_HOST is not set — refusing to run against real GCP.",
              file=sys.stderr)
        return 2
    project = os.environ.get("PUBSUB_PROJECT_ID", "local-dev")

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()

    created_t = existed_t = 0
    for topic in Topic:
        path = publisher.topic_path(project, topic.value)
        try:
            publisher.create_topic(name=path)
            created_t += 1
        except AlreadyExists:
            existed_t += 1
    print(f"topics: {created_t} created, {existed_t} already existed "
          f"({created_t + existed_t} total)")

    created_s = existed_s = 0
    for sub_id, topic_id in SUBSCRIPTIONS.items():
        sub_path = subscriber.subscription_path(project, sub_id)
        topic_path = publisher.topic_path(project, topic_id)
        try:
            subscriber.create_subscription(name=sub_path, topic=topic_path)
            created_s += 1
        except AlreadyExists:
            existed_s += 1
    print(f"subscriptions: {created_s} created, {existed_s} already existed "
          f"({created_s + existed_s} total)")
    print(f"✓ Pub/Sub emulator bootstrapped (project={project})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
