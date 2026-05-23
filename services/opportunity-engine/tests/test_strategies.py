from datetime import datetime

from models import MarketSnapshot
from scorer import score
from shared.models.exchange_tick import ExchangeTick
from shared.models.funding_rate import FundingRate
from shared.models.opportunity import StrategyType
from shared.utils.fee_calculator import MIN_VIABLE_NET_EDGE_BPS
from strategies import CrossExchangeStrategy, FundingRateArbStrategy, SpotPerpBasisStrategy


def _tick(
    exchange: str, asset: str, bid: float, ask: float, instrument_type: str = "spot"
) -> ExchangeTick:
    return ExchangeTick(
        exchange=exchange,
        symbol=f"{asset}/USD" if instrument_type == "spot" else f"{asset}/USD:PERP",
        asset=asset,
        bid=bid,
        ask=ask,
        bid_size=10.0,
        ask_size=10.0,
        last=(bid + ask) / 2,
        timestamp=datetime.utcnow(),
        instrument_type=instrument_type,
    )


def _funding(exchange: str, asset: str, rate: float, apr_pct: float) -> FundingRate:
    return FundingRate(
        exchange=exchange,
        asset=asset,
        symbol=f"{asset}/USD:PERP",
        rate_per_period=rate,
        period_hours=8.0,
        annualized_pct=apr_pct,
        observed_at=datetime.utcnow(),
        source="test",
    )


def test_cross_exchange_finds_spread() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", bid=60_000.0, ask=60_010.0))
    snap.update_tick(_tick("crypto.com", "BTC", bid=60_100.0, ask=60_110.0))

    candidates = list(CrossExchangeStrategy().evaluate(snap))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.strategy == StrategyType.CROSS_EXCHANGE
    # Buy at kraken (lower ask), sell at crypto.com (higher bid)
    assert c.long_exchange == "kraken"
    assert c.short_exchange == "crypto.com"
    # (60100 - 60010) / 60010 ≈ 15 bps
    assert 14 < c.gross_spread_bps < 16


def test_cross_exchange_skips_when_no_arb() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", bid=60_000, ask=60_010))
    snap.update_tick(_tick("crypto.com", "BTC", bid=60_005, ask=60_012))
    assert list(CrossExchangeStrategy().evaluate(snap)) == []


def test_cross_exchange_ignores_perp_legs() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000, 60_010, instrument_type="spot"))
    snap.update_tick(_tick("hyperliquid", "BTC", 60_500, 60_510, instrument_type="perp"))
    assert list(CrossExchangeStrategy().evaluate(snap)) == []


def test_spot_perp_basis_picks_correct_direction() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000, 60_010, instrument_type="spot"))
    # Perp trading at 60bps premium
    snap.update_tick(_tick("hyperliquid", "BTC", 60_355, 60_365, instrument_type="perp"))

    candidates = list(SpotPerpBasisStrategy().evaluate(snap))
    assert len(candidates) >= 1
    c = candidates[0]
    assert c.strategy == StrategyType.SPOT_PERP_BASIS
    # Perp expensive → long spot (kraken), short perp (hyperliquid)
    assert c.long_exchange == "kraken"
    assert c.short_exchange == "hyperliquid"
    assert c.gross_spread_bps > 50


def test_funding_rate_arb_collects_positive_funding() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000, 60_010, instrument_type="spot"))
    # ~30% APR = juicy positive funding
    snap.update_funding(_funding("hyperliquid", "BTC", rate=0.000034, apr_pct=30.0))

    candidates = list(FundingRateArbStrategy().evaluate(snap))
    assert candidates, "expected at least one candidate"
    c = candidates[0]
    assert c.strategy == StrategyType.FUNDING_RATE_ARB
    assert c.short_exchange == "hyperliquid"
    assert c.long_exchange == "kraken"
    assert c.funding_rate_annualized_pct == 30.0
    assert c.funding_rate_bps_per_period > 0


def test_funding_rate_arb_skips_low_funding() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000, 60_010))
    snap.update_funding(_funding("hyperliquid", "BTC", rate=0.000001, apr_pct=1.0))
    assert list(FundingRateArbStrategy().evaluate(snap)) == []


def test_scorer_marks_viable_above_threshold() -> None:
    """A 165-bp gross spread yields a net edge well above the 50-bp bar."""
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000.0, 60_010.0))
    snap.update_tick(_tick("crypto.com", "BTC", 61_000.0, 61_010.0))
    cand = next(iter(CrossExchangeStrategy().evaluate(snap)))
    opp = score(cand)
    assert opp.strategy == StrategyType.CROSS_EXCHANGE
    assert opp.net_edge_bps > MIN_VIABLE_NET_EDGE_BPS  # 50.0 bps
    assert opp.execute is False  # risk-engine still has to approve
    assert opp.rejection_reason is None
