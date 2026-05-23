from __future__ import annotations

from typing import Any

from collectors.base import BaseCollector, _enable_rate_limit


class KrakenCollector(BaseCollector):
    name = "kraken"

    async def _build_client(self) -> Any:
        import ccxt.pro as ccxtpro

        from secret_loader import get_exchange_credentials

        creds = get_exchange_credentials("kraken")
        return ccxtpro.kraken(
            {
                "apiKey": creds["apiKey"],
                "secret": creds["secret"],
                "enableRateLimit": _enable_rate_limit(),
            }
        )
