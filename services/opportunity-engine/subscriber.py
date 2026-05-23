"""Pub/Sub subscriber wrapper.

Each topic gets a streaming-pull subscription; messages are decoded into
Pydantic models and handed to a callback. The callback is responsible for
ack/nack — we ack on successful processing, nack on exception.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from google.cloud import pubsub_v1

logger = logging.getLogger(__name__)


class PubSubSubscriber:
    def __init__(self, project_id: str | None = None) -> None:
        from google.cloud import pubsub_v1

        self.project_id = project_id or os.environ["GCP_PROJECT_ID"]
        self.client: pubsub_v1.SubscriberClient = pubsub_v1.SubscriberClient()
        self._futures: list = []

    def subscribe[T: BaseModel](
        self,
        subscription_id: str,
        model: type[T],
        callback: Callable[[T], None],
    ) -> None:
        subscription_path = self.client.subscription_path(self.project_id, subscription_id)

        def _wrapper(message) -> None:
            try:
                payload = json.loads(message.data.decode("utf-8"))
                callback(model(**payload))
                message.ack()
            except Exception:
                logger.exception("subscriber callback failed on %s", subscription_id)
                message.nack()

        future = self.client.subscribe(subscription_path, callback=_wrapper)
        self._futures.append(future)
        logger.info("subscribed to %s", subscription_path)

    def stop(self) -> None:
        for f in self._futures:
            f.cancel()
        self.client.close()
