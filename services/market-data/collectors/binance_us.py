from __future__ import annotations

from typing import Any

from collectors.base import BaseCollector, _enable_rate_limit


class BinanceUSCollector(BaseCollector):
    name = "binance.us"

    async def _build_client(self) -> Any:
        import ccxt.pro as ccxtpro

        from secret_loader import get_exchange_credentials

        creds = get_exchange_credentials("binance.us")
        return ccxtpro.binanceus(
            {
                "apiKey": creds["apiKey"],
                "secret": creds["secret"],
                "enableRateLimit": _enable_rate_limit(),
            }
        )
