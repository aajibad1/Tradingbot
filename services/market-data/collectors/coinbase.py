from __future__ import annotations

from typing import Any

from collectors.base import BaseCollector, _enable_rate_limit


class CoinbaseCollector(BaseCollector):
    name = "coinbase"

    async def _build_client(self) -> Any:
        # ccxt.pro ships under `ccxt.pro` on PyPI as part of the unified package.
        import ccxt.pro as ccxtpro

        from secret_loader import get_exchange_credentials

        creds = get_exchange_credentials("coinbase")
        client = ccxtpro.coinbase(
            {
                "apiKey": creds["apiKey"],
                "secret": creds["secret"],
                "password": creds.get("password"),
                "enableRateLimit": _enable_rate_limit(),
            }
        )
        return client
