"""Hummingbot REST API client.

Hummingbot exposes a JSON HTTP API (gateway-style) on a local port. We
communicate with a long-running strategy instance — we do NOT spawn bots
per trade. The bot is configured once with the strategy script
(funding_rate_arb_config.yml or spot_perp_basis_config.yml) and accepts
parameter updates and on-demand orders.

Request model is intentionally narrow — we expose only what we use.

Margin policy (non-negotiable):

  * Every perp leg is submitted with ``marginMode = "isolated"`` and
    ``leverage = 1``. Cross-margin lets a losing position eat margin
    allocated to other positions; we never want that. 1x leverage keeps
    the perp leg cash-equivalent so it can be paired with the spot leg
    without skew.

  * ``reduceOnly`` is set on close orders so a mis-routed close cannot
    accidentally open new exposure.

These flags are carried in the request body under ``risk_params`` and the
Hummingbot bot script is configured to forward them to the venue.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from shared.models.opportunity import Opportunity

logger = logging.getLogger(__name__)


# Margin policy — see module docstring.
ISOLATED_MARGIN_MODE = "isolated"
ARB_LEVERAGE = 1


def perp_risk_params(reduce_only: bool = False) -> dict[str, Any]:
    """Standard risk params attached to every perp order.

    Centralised so order-construction sites cannot drift from the policy.
    """
    return {
        "marginMode": ISOLATED_MARGIN_MODE,
        "leverage": ARB_LEVERAGE,
        "reduceOnly": reduce_only,
    }


@dataclass
class HummingbotOrderRequest:
    bot_id: str
    asset: str
    long_exchange: str
    short_exchange: str
    size_usd: float
    strategy: str
    opportunity_id: str
    confidence_score: float
    min_hold_hours: float
    risk_params: dict[str, Any] = field(default_factory=lambda: perp_risk_params(reduce_only=False))

    @classmethod
    def from_opportunity(cls, bot_id: str, opp: Opportunity) -> HummingbotOrderRequest:
        return cls(
            bot_id=bot_id,
            asset=opp.asset,
            long_exchange=opp.long_exchange,
            short_exchange=opp.short_exchange,
            size_usd=opp.recommended_size_usd,
            strategy=opp.strategy.value,
            opportunity_id=opp.id,
            confidence_score=opp.confidence_score,
            min_hold_hours=opp.min_hold_hours,
            risk_params=perp_risk_params(reduce_only=False),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "long_exchange": self.long_exchange,
            "short_exchange": self.short_exchange,
            "size_usd": self.size_usd,
            "strategy": self.strategy,
            "opportunity_id": self.opportunity_id,
            "min_hold_hours": self.min_hold_hours,
            "risk_params": self.risk_params,
            "metadata": {"confidence_score": self.confidence_score},
        }


@dataclass
class HummingbotCloseRequest:
    """Close request — always reduceOnly + isolated margin."""

    bot_id: str
    opportunity_id: str
    risk_params: dict[str, Any] = field(default_factory=lambda: perp_risk_params(reduce_only=True))

    def to_payload(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "risk_params": self.risk_params,
        }


class HummingbotClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("HUMMINGBOT_URL", "http://localhost:15888")).rstrip("/")
        self.api_key = api_key or os.environ.get("HUMMINGBOT_API_KEY")

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-Auth-Token"] = self.api_key
        return h

    def submit_order(self, request: HummingbotOrderRequest) -> dict[str, Any]:
        url = f"{self.base_url}/bots/{request.bot_id}/orders"
        logger.info(
            "hummingbot submit bot=%s opp=%s asset=%s margin=%s lev=%s",
            request.bot_id,
            request.opportunity_id,
            request.asset,
            request.risk_params.get("marginMode"),
            request.risk_params.get("leverage"),
        )
        response = httpx.post(url, json=request.to_payload(), headers=self._headers(), timeout=15.0)
        response.raise_for_status()
        return response.json()

    def close_position(self, request: HummingbotCloseRequest) -> dict[str, Any]:
        url = f"{self.base_url}/bots/{request.bot_id}/positions/{request.opportunity_id}/close"
        logger.info(
            "hummingbot close bot=%s opp=%s reduceOnly=%s",
            request.bot_id,
            request.opportunity_id,
            request.risk_params.get("reduceOnly"),
        )
        response = httpx.post(url, json=request.to_payload(), headers=self._headers(), timeout=15.0)
        response.raise_for_status()
        return response.json()

    def get_positions(self, bot_id: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/bots/{bot_id}/positions"
        response = httpx.get(url, headers=self._headers(), timeout=10.0)
        response.raise_for_status()
        return response.json().get("positions", [])

    def get_bot_status(self, bot_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/bots/{bot_id}/status"
        response = httpx.get(url, headers=self._headers(), timeout=10.0)
        response.raise_for_status()
        return response.json()
