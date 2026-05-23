from __future__ import annotations

from typing import Any

from collectors.base import BaseCollector, _enable_rate_limit


class HyperliquidCollector(BaseCollector):
    """Hyperliquid is perp-only and API-key optional for read endpoints.

    We still authenticate when keys are available — it raises the rate-limit
    ceiling for orderbook subscriptions.
    """

    name = "hyperliquid"

    async def _build_client(self) -> Any:
        import ccxt.pro as ccxtpro

        from secret_loader import get_exchange_credentials

        try:
            creds = get_exchange_credentials("hyperliquid")
            config = {
                "apiKey": creds["apiKey"],
                "secret": creds["secret"],
                "enableRateLimit": _enable_rate_limit(),
            }
        except RuntimeError:
            # Read-only — no creds required for market data
            config = {"enableRateLimit": _enable_rate_limit()}

        return ccxtpro.hyperliquid(config)
