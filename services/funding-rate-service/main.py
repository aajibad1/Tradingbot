"""funding-rate-service — polls every 60s, normalizes, publishes.

Runs all configured sources concurrently each cycle. Per-source failures
do not block other sources; consecutive failures count toward source
health for /healthz.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from models import CycleResult, HealthResponse, SourceHealth
from shared.models.funding_rate import FundingRate
from shared.pubsub.publisher import Topic, get_publisher
from shared.utils.log_redact import redact_secrets
from sources import (
    ArbitrageScannerSource,
    CCXTFundingSource,
    CoinGlassSource,
    FundingSource,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("funding-rate-service")

_POLL_INTERVAL_S = float(os.environ.get("FUNDING_POLL_INTERVAL_S", "60"))

_health: dict[str, SourceHealth] = {}


def _build_sources() -> list[FundingSource]:
    sources: list[FundingSource] = []

    # Try to initialize each source; skip if API key is unavailable
    try:
        sources.append(CoinGlassSource(exchanges=["kraken", "crypto.com", "binance", "hyperliquid"]))
        logger.info("CoinGlassSource initialized")
    except RuntimeError as e:
        logger.warning("CoinGlassSource unavailable: %s", e)

    try:
        sources.append(ArbitrageScannerSource())
        logger.info("ArbitrageScannerSource initialized")
    except RuntimeError as e:
        logger.warning("ArbitrageScannerSource unavailable: %s", e)

    ccxt_exchanges_raw = os.environ.get("CCXT_FUNDING_EXCHANGES", "")
    ccxt_exchanges = [e.strip() for e in ccxt_exchanges_raw.split(",") if e.strip()]
    if ccxt_exchanges:
        try:
            sources.append(CCXTFundingSource(ccxt_exchanges))
            logger.info("CCXTFundingSource initialized with exchanges: %s", ccxt_exchanges)
        except (ValueError, RuntimeError) as e:
            logger.warning("CCXTFundingSource unavailable: %s", e)

    if not sources:
        logger.warning(
            "no funding sources configured — set COINGLASS_API_KEY, "
            "ARBITRAGE_SCANNER_API_KEY, or CCXT_FUNDING_EXCHANGES"
        )

    for s in sources:
        _health[s.name] = SourceHealth(source=s.name)
    return sources


async def _run_cycle(sources: list[FundingSource]) -> list[CycleResult]:
    publisher = get_publisher()

    async def _one(source: FundingSource) -> CycleResult:
        started = time.perf_counter()
        try:
            rates: list[FundingRate] = list(await source.fetch())
        except Exception as e:
            # redact: error reprs can embed request URLs / tokens, and
            # last_error is exposed on /healthz.
            safe_err = redact_secrets(repr(e))
            _health[source.name].consecutive_failures += 1
            _health[source.name].last_error = safe_err
            logger.exception("%s fetch failed", source.name)
            return CycleResult(
                source=source.name,
                rates_published=0,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=safe_err,
            )

        for rate in rates:
            # Fire-and-forget: a slow publish must not block the poll cycle or
            # the other sources gathered alongside this one.
            publisher.publish_nowait(
                Topic.FUNDING_RATES,
                rate,
                attributes={"source": source.name, "exchange": rate.exchange, "asset": rate.asset},
            )

        _health[source.name].consecutive_failures = 0
        _health[source.name].last_success_at = time.time()
        _health[source.name].last_error = None
        return CycleResult(
            source=source.name,
            rates_published=len(rates),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    return list(await asyncio.gather(*(_one(s) for s in sources)))


async def _poll_forever(sources: list[FundingSource], stop: asyncio.Event) -> None:
    while not stop.is_set():
        cycle_start = time.perf_counter()
        results = await _run_cycle(sources)
        elapsed = time.perf_counter() - cycle_start
        for r in results:
            log = logger.warning if r.error else logger.info
            log("cycle %s rates=%d duration_ms=%d err=%s", r.source, r.rates_published, r.duration_ms, r.error)
        sleep_for = max(0.0, _POLL_INTERVAL_S - elapsed)
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
        except TimeoutError:
            continue


_stop = asyncio.Event()
_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _task
    sources = _build_sources()
    if sources:
        _task = asyncio.create_task(_poll_forever(sources, _stop))
    try:
        yield
    finally:
        _stop.set()
        if _task is not None:
            await asyncio.gather(_task, return_exceptions=True)
        # Close any sources holding long-lived clients (e.g. cached CCXT sessions).
        for s in sources:
            close = getattr(s, "close", None)
            if close is not None:
                await asyncio.gather(close(), return_exceptions=True)


app = FastAPI(title="funding-rate-service", version="0.1.0", lifespan=lifespan)


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    sources = list(_health.values())
    degraded = any(s.consecutive_failures >= 3 for s in sources)
    no_sources = len(sources) == 0
    status = "degraded" if degraded or no_sources else "ok"
    return HealthResponse(status=status, sources=sources)
