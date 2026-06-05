"""Rate resolution: provider fallback (FX-04), Redis cache (FX-01, 60s), and the
Nigeria three-rate composition (FX-03), plus USD→local conversion."""

from __future__ import annotations

import logging
from datetime import datetime

from models import ConvertResponse, NgnRates, RatesSnapshot
from providers import SYMBOLS, FxProvider

logger = logging.getLogger("fx-rate-service")

CACHE_KEY = "fx:rates:snapshot"
CACHE_TTL_S = 60
# P2P NGN rate is written here by the Africa P2P collector (BT-02 / AFR-05).
NGN_P2P_KEY = "fx:ngn:p2p"


def _try(provider: FxProvider) -> tuple[dict[str, float] | None, str]:
    try:
        return provider.fetch(), provider.name
    except Exception as e:  # noqa: BLE001 — provider down → caller falls back
        logger.warning("fx provider %s failed: %s", provider.name, e)
        return None, provider.name


def fetch_rates(primary: FxProvider, fallback: FxProvider, redis=None) -> RatesSnapshot:
    """Fetch from the primary, falling back to the secondary; compose NGN rates.

    The primary supplies the headline rates + the CBN-official NGN; the fallback
    supplies the parallel NGN. If the primary is down, the fallback becomes the
    headline source.
    """
    main_rates, main_src = _try(primary)
    fb_rates, fb_src = _try(fallback)

    base = main_rates if main_rates is not None else fb_rates
    if base is None:
        raise RuntimeError("all FX providers failed")
    source = main_src if main_rates is not None else fb_src

    official_ngn = (main_rates or {}).get("NGN") or (fb_rates or {}).get("NGN")
    parallel_ngn = (fb_rates or {}).get("NGN")
    p2p_ngn = None
    if redis is not None:
        raw = redis.get(NGN_P2P_KEY)
        if raw is not None:
            try:
                p2p_ngn = float(raw)
            except (TypeError, ValueError):
                pass

    return RatesSnapshot(
        rates={s: base[s] for s in SYMBOLS if s in base},
        ngn=NgnRates(official=official_ngn, parallel=parallel_ngn, p2p=p2p_ngn),
        source=source,
        fetched_at=datetime.utcnow(),
    )


def get_rates(primary: FxProvider, fallback: FxProvider, redis=None) -> RatesSnapshot:
    """Cached rates — refreshes from providers at most once per CACHE_TTL_S."""
    if redis is not None:
        cached = redis.get(CACHE_KEY)
        if cached is not None:
            return RatesSnapshot.model_validate_json(cached)
    snap = fetch_rates(primary, fallback, redis)
    if redis is not None:
        redis.setex(CACHE_KEY, CACHE_TTL_S, snap.model_dump_json())
    return snap


def convert(snap: RatesSnapshot, amount_usd: float, currency: str,
            ngn_rate_type: str = "official") -> ConvertResponse:
    """Convert a USD amount into a local currency at the snapshot's rate."""
    cur = currency.upper()
    if cur == "USD":
        return ConvertResponse(amount_usd=amount_usd, currency="USD", rate=1.0,
                               amount_local=amount_usd, fetched_at=snap.fetched_at)
    if cur == "NGN":
        rate = getattr(snap.ngn, ngn_rate_type, None) or snap.ngn.official
        if rate is None:
            raise ValueError("NGN rate unavailable")
        return ConvertResponse(amount_usd=amount_usd, currency="NGN", rate=rate,
                               amount_local=amount_usd * rate, ngn_rate_type=ngn_rate_type,
                               fetched_at=snap.fetched_at)
    rate = snap.rates.get(cur)
    if rate is None:
        raise ValueError(f"no rate for {cur}")
    return ConvertResponse(amount_usd=amount_usd, currency=cur, rate=rate,
                           amount_local=amount_usd * rate, fetched_at=snap.fetched_at)
