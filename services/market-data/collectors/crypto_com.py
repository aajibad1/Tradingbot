from __future__ import annotations

from typing import Any

from collectors.base import BaseCollector, _enable_rate_limit


class CryptoComCollector(BaseCollector):
    name = "crypto.com"

    async def _build_client(self) -> Any:
        import ccxt.pro as ccxtpro

        from secret_loader import get_exchange_credentials

        creds = get_exchange_credentials("crypto.com")
        return ccxtpro.cryptocom(
            {
                "apiKey": creds["apiKey"],
                "secret": creds["secret"],
                "enableRateLimit": _enable_rate_limit(),
            }
        )
