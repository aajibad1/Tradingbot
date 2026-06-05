"""fx-rate-service — local-currency FX rates for P&L display + tax capture.

Implements FRD §4 / TDD §4:
  FX-01  dashboard rates, refreshed ≤60s (Redis-cached)
  FX-02  execution FX capture — GET /rate gives the rate to store with a trade
  FX-03  Nigeria three-rate (CBN official / parallel / P2P)
  FX-04  provider fallback (primary down → keyless secondary)

Endpoints:
  GET  /healthz
  GET  /status
  GET  /rates                                  current snapshot (cached ≤60s)
  GET  /convert?amount_usd=&currency=&ngn_rate_type=
  GET  /rate?currency=&ngn_rate_type=          single rate (for trade FX capture)
  POST /refresh                                force a provider refresh (scheduler)

Runs locally without GCP/keys: with no OPEN_EXCHANGE_RATES_APP_ID it uses the
free keyless provider for everything; with no REDIS_URL it skips caching.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Query

import rates as rates_mod
from models import ConvertResponse, RatesSnapshot
from providers import ExchangeRateHostProvider, FxProvider, OpenExchangeRatesProvider
from secret_loader import get_secret

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("fx-rate-service")

app = FastAPI(title="fx-rate-service", version="0.1.0")


def _build_providers() -> tuple[FxProvider, FxProvider]:
    fallback = ExchangeRateHostProvider()
    app_id = os.environ.get("OPEN_EXCHANGE_RATES_APP_ID") or get_secret("open-exchange-rates-app-id")
    if app_id:
        return OpenExchangeRatesProvider(app_id), fallback
    logger.warning("OPEN_EXCHANGE_RATES_APP_ID unset — using keyless provider as primary")
    return fallback, fallback


def _redis():
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    from redis import Redis

    return Redis.from_url(url, decode_responses=True, socket_timeout=2.0)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict[str, object]:
    primary, fallback = _build_providers()
    return {
        "primary": primary.name,
        "fallback": fallback.name,
        "cache": "redis" if os.environ.get("REDIS_URL") else "none",
        "supported": ["USD", "ZAR", "NGN", "KES"],
    }


@app.get("/rates", response_model=RatesSnapshot)
def rates() -> RatesSnapshot:
    primary, fallback = _build_providers()
    try:
        return rates_mod.get_rates(primary, fallback, _redis())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"fx providers unavailable: {e}") from e


@app.get("/convert", response_model=ConvertResponse)
def convert(
    amount_usd: float = Query(ge=0.0),
    currency: str = Query(min_length=3, max_length=3),
    ngn_rate_type: str = Query(default="official"),
) -> ConvertResponse:
    primary, fallback = _build_providers()
    snap = rates_mod.get_rates(primary, fallback, _redis())
    try:
        return rates_mod.convert(snap, amount_usd, currency, ngn_rate_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/rate", response_model=ConvertResponse)
def rate(currency: str = Query(min_length=3, max_length=3),
         ngn_rate_type: str = Query(default="official")) -> ConvertResponse:
    """The rate for 1 USD — what the ledger stores as fx_rate_at_execution (FX-02)."""
    return convert(amount_usd=1.0, currency=currency, ngn_rate_type=ngn_rate_type)


@app.post("/refresh", response_model=RatesSnapshot)
def refresh() -> RatesSnapshot:
    """Force a provider refresh and re-cache. Cloud Scheduler calls this ≤60s."""
    primary, fallback = _build_providers()
    redis = _redis()
    try:
        snap = rates_mod.fetch_rates(primary, fallback, redis)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"fx providers unavailable: {e}") from e
    if redis is not None:
        redis.setex(rates_mod.CACHE_KEY, rates_mod.CACHE_TTL_S, snap.model_dump_json())
    return snap
