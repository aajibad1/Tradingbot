"""Tests for the engine hot path: throttling, below-threshold filter, invariants."""

from __future__ import annotations

import ast
import re
from datetime import datetime
from pathlib import Path

import main as engine_main
from models import MarketSnapshot
from scorer import ScoredCandidate, score
from shared.models.exchange_tick import ExchangeTick
from shared.models.funding_rate import FundingRate
from shared.models.opportunity import StrategyType
from shared.utils.fee_calculator import MIN_VIABLE_NET_EDGE_BPS
from shared.utils.slippage_model import estimate_size_tier_bps
from strategies import (
    CrossExchangeStrategy,
    FundingRateArbStrategy,
    SpotPerpBasisStrategy,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Slippage size-tier helper                                                   #
# --------------------------------------------------------------------------- #


def test_slippage_size_tier_anchor_points() -> None:
    assert estimate_size_tier_bps(10_000) == 2.0
    assert estimate_size_tier_bps(50_000) == 5.0
    assert estimate_size_tier_bps(100_000) == 12.0


def test_slippage_size_tier_interpolation_and_floor() -> None:
    # Linear interpolation between 10K and 50K → 30K halfway = 3.5 bps
    mid_low = estimate_size_tier_bps(30_000)
    assert 3.4 < mid_low < 3.6

    # Linear interpolation between 50K and 100K → 75K halfway = 8.5 bps
    mid_high = estimate_size_tier_bps(75_000)
    assert 8.4 < mid_high < 8.6

    # Floor: anything below 10K returns the 10K rate
    assert estimate_size_tier_bps(1_000) == 2.0
    assert estimate_size_tier_bps(0) == 0.0


# --------------------------------------------------------------------------- #
# Throttling: EVAL_INTERVAL_S                                                 #
# --------------------------------------------------------------------------- #


def test_maybe_evaluate_throttles_within_interval(monkeypatch) -> None:
    """Two ticks arriving within EVAL_INTERVAL_S should produce exactly one eval."""

    # Force a generous interval so the test isn't time-sensitive.
    monkeypatch.setattr(engine_main, "_EVAL_INTERVAL_S", 5.0)
    engine_main._last_eval_at = 0.0

    call_count = {"n": 0}

    def fake_score(cand, now=None):
        call_count["n"] += 1
        return score(cand, now=now)

    monkeypatch.setattr(engine_main, "score", fake_score)

    # Seed snapshot with a real cross-exchange opportunity so strategies emit.
    engine_main.snapshot.update_tick(_tick("kraken", "BTC", 60_000.0, 60_010.0))
    engine_main.snapshot.update_tick(_tick("crypto.com", "BTC", 60_500.0, 60_510.0))

    engine_main._maybe_evaluate()
    first = call_count["n"]
    assert first > 0, "first eval should have produced candidates"

    # Second call inside the interval is a no-op.
    engine_main._maybe_evaluate()
    assert call_count["n"] == first, "throttle should suppress second eval"


def test_evaluate_now_resets_throttle(monkeypatch) -> None:
    """POST /evaluate-now must bypass the throttle (resets _last_eval_at)."""

    monkeypatch.setattr(engine_main, "_EVAL_INTERVAL_S", 60.0)
    engine_main._last_eval_at = 0.0

    call_count = {"n": 0}
    monkeypatch.setattr(
        engine_main,
        "score",
        lambda cand, now=None: (call_count.__setitem__("n", call_count["n"] + 1), score(cand, now=now))[1],
    )

    engine_main.snapshot.update_tick(_tick("kraken", "BTC", 60_000.0, 60_010.0))
    engine_main.snapshot.update_tick(_tick("crypto.com", "BTC", 60_500.0, 60_510.0))

    engine_main._maybe_evaluate()
    after_first = call_count["n"]
    assert after_first > 0

    # Same as POST /evaluate-now body: reset last-eval clock.
    engine_main._last_eval_at = 0.0
    engine_main._maybe_evaluate()
    assert call_count["n"] > after_first, "evaluate-now should re-run strategies"


# --------------------------------------------------------------------------- #
# Per-strategy: above + below threshold                                       #
# --------------------------------------------------------------------------- #


def test_cross_exchange_above_threshold_is_viable() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000.0, 60_010.0))
    # ~80 bps gross spread, comfortably above (kraken 26 + crypto.com 7.5 + 4 slip + 3 buffer = ~40.5)
    snap.update_tick(_tick("crypto.com", "BTC", 60_500.0, 60_510.0))

    cands = list(CrossExchangeStrategy().evaluate(snap))
    assert cands, "expected at least one cross-exchange candidate"
    opp = score(cands[0])
    assert opp.strategy == StrategyType.CROSS_EXCHANGE
    assert opp.net_edge_bps > MIN_VIABLE_NET_EDGE_BPS


def test_cross_exchange_below_threshold_not_viable() -> None:
    """Small gross spread → net_edge below MIN_VIABLE_NET_EDGE_BPS."""
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000.0, 60_010.0))
    # ~12 bps gross (60_017 / 60_010 ≈ 12 bps after subtracting ask):
    snap.update_tick(_tick("crypto.com", "BTC", 60_080.0, 60_090.0))

    cands = list(CrossExchangeStrategy().evaluate(snap))
    if not cands:
        # Below the strategy's own _MIN_GROSS_SPREAD_BPS — also acceptable.
        return
    opp = score(cands[0])
    # Fees alone (kraken 26 + crypto.com 7.5 = 33.5) + buffer + slip would
    # easily exceed a 12 bp spread.
    assert opp.net_edge_bps < MIN_VIABLE_NET_EDGE_BPS


def test_spot_perp_basis_above_threshold_is_viable() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000.0, 60_010.0, instrument_type="spot"))
    # 60 bps basis premium on the perp leg
    snap.update_tick(_tick("hyperliquid", "BTC", 60_355.0, 60_365.0, instrument_type="perp"))

    cands = list(SpotPerpBasisStrategy().evaluate(snap))
    assert cands
    opp = score(cands[0])
    assert opp.strategy == StrategyType.SPOT_PERP_BASIS
    assert opp.net_edge_bps > MIN_VIABLE_NET_EDGE_BPS


def test_spot_perp_basis_below_threshold_not_viable() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000.0, 60_010.0, instrument_type="spot"))
    # ~10 bps basis — below threshold once kraken + hyperliquid fees + buffer kick in
    snap.update_tick(_tick("hyperliquid", "BTC", 60_055.0, 60_065.0, instrument_type="perp"))

    cands = list(SpotPerpBasisStrategy().evaluate(snap))
    if not cands:
        return
    opp = score(cands[0])
    assert opp.net_edge_bps < MIN_VIABLE_NET_EDGE_BPS


def test_funding_rate_arb_above_threshold_is_viable() -> None:
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000.0, 60_010.0))
    # 30% APR / 1095 periods per year ≈ 27 bps per 8h period
    snap.update_funding(_funding("hyperliquid", "BTC", rate=0.0027, apr_pct=30.0))

    cands = list(FundingRateArbStrategy().evaluate(snap))
    assert cands
    opp = score(cands[0])
    assert opp.strategy == StrategyType.FUNDING_RATE_ARB
    assert opp.short_exchange == "hyperliquid"
    assert opp.long_exchange == "kraken"
    # gross + funding clears kraken 26 + hyperliquid 5 + slip + buffer
    assert opp.net_edge_bps > MIN_VIABLE_NET_EDGE_BPS


def test_funding_rate_arb_below_threshold_not_viable() -> None:
    """Just-above-min funding APR shouldn't beat fees+buffer."""
    snap = MarketSnapshot()
    snap.update_tick(_tick("kraken", "BTC", 60_000.0, 60_010.0))
    # 9% APR — clears the strategy's 8% gate but barely
    snap.update_funding(_funding("hyperliquid", "BTC", rate=0.00008, apr_pct=9.0))

    cands = list(FundingRateArbStrategy().evaluate(snap))
    assert cands
    opp = score(cands[0])
    assert opp.net_edge_bps < MIN_VIABLE_NET_EDGE_BPS


# --------------------------------------------------------------------------- #
# Invariant: strategies must not reimplement fee/slippage/buffer math         #
# --------------------------------------------------------------------------- #


_STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "strategies"

# Patterns that would indicate a strategy is computing viability itself instead
# of routing through shared.utils.fee_calculator. Checked against the file
# with docstrings/comments stripped so prose mentions of these symbols don't
# trip the gate.
_FORBIDDEN_PATTERNS = [
    r"\bcalculate_net_edge\b",       # only the scorer is allowed to call this
    r"\bsafety_buffer",              # buffer math belongs in shared
    r"\bDEFAULT_SAFETY_BUFFER_BPS",
    r"\bMIN_VIABLE_NET_EDGE_BPS",
    r"\bEXCHANGE_TAKER_FEE_BPS",     # raw fee-table reads are off-limits
]


def _strip_docstrings_and_comments(source: str) -> str:
    """Return source with module/class/function docstrings and comments removed."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef, ast.Module)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return ast.unparse(tree)


def test_strategies_do_not_reimplement_net_edge_math() -> None:
    offenders: list[str] = []
    for py in _STRATEGIES_DIR.glob("*.py"):
        if py.name in {"__init__.py", "base.py"}:
            continue
        stripped = _strip_docstrings_and_comments(py.read_text())
        for pat in _FORBIDDEN_PATTERNS:
            if re.search(pat, stripped):
                offenders.append(f"{py.name}: {pat}")
    assert not offenders, (
        "Strategies must route fee/buffer math through the scorer. "
        "Offending references: " + "; ".join(offenders)
    )


def test_strategies_use_only_taker_fee_lookup_from_fee_calculator() -> None:
    """The only allowed import from fee_calculator is the fail-loud lookup."""
    for py in _STRATEGIES_DIR.glob("*.py"):
        if py.name in {"__init__.py", "base.py"}:
            continue
        text = py.read_text()
        if "from shared.utils.fee_calculator" not in text:
            continue
        line = next(
            line for line in text.splitlines() if "from shared.utils.fee_calculator" in line
        )
        assert "taker_fee_bps" in line and "calculate_net_edge" not in line, (
            f"{py.name} imports something other than taker_fee_bps from fee_calculator: {line}"
        )


# --------------------------------------------------------------------------- #
# Scorer correctness                                                          #
# --------------------------------------------------------------------------- #


def test_scorer_total_costs_match_fee_calculator() -> None:
    """Scorer's trading_fees_bps must equal fee_calculator's per-exchange lookup."""
    cand = ScoredCandidate(
        strategy=StrategyType.CROSS_EXCHANGE,
        asset="BTC",
        long_exchange="kraken",
        short_exchange="crypto.com",
        gross_spread_bps=50.0,
        slippage_long_bps=2.0,
        slippage_short_bps=2.0,
    )
    opp = score(cand)
    # 26 (kraken) + 7.5 (crypto.com)
    assert opp.trading_fees_bps == 33.5
    # net = 50 - 33.5 - 4 - 3 = 9.5
    assert abs(opp.net_edge_bps - 9.5) < 1e-6
