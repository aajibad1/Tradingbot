"""Provider/corridor catalog + route resolution (docs/06 Routing). Pure + testable.

Routing picks the best provider for a funding (on-ramp) or payout (off-ramp)
request across the corridors each provider covers, ranked by the caller's
objective (lowest cost or fastest settlement). This is advisory resolution — it
moves no money — so providers here are sandbox stand-ins; real connectors slot in
behind the same interface later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

OBJECTIVE_COST = "cost"
OBJECTIVE_SPEED = "speed"


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    fee_bps: float
    settlement_seconds: int
    directions: frozenset = field(default_factory=lambda: frozenset({"onramp", "offramp"}))
    # corridors covered as {(source, dest)}; ("*", "*") means "any".
    corridors: frozenset = field(default_factory=lambda: frozenset({("*", "*")}))

    def covers(self, direction: str, source: str, dest: str) -> bool:
        if direction not in self.directions:
            return False
        s, d = source.upper(), dest.upper()
        return ("*", "*") in self.corridors or (s, d) in self.corridors


# Sandbox provider catalog (deterministic). Real connectors replace these.
PROVIDERS: tuple[Provider, ...] = (
    Provider("sandbox-fast", "Sandbox Fast", fee_bps=120.0, settlement_seconds=30),
    Provider("sandbox-balanced", "Sandbox Balanced", fee_bps=90.0, settlement_seconds=120),
    # Cheapest, but only covers a few African corridors and is slow.
    Provider(
        "sandbox-cheap", "Sandbox Cheap", fee_bps=55.0, settlement_seconds=900,
        corridors=frozenset({("NGN", "USDC"), ("USDC", "NGN"), ("KES", "USDC"), ("USDC", "KES")}),
    ),
)


def _option(provider: Provider, amount: float) -> dict:
    return {
        "provider_id": provider.id,
        "provider_name": provider.name,
        "fee_bps": provider.fee_bps,
        "estimated_fee": round(amount * provider.fee_bps / 10_000.0, 6),
        "estimated_settlement_seconds": provider.settlement_seconds,
    }


def resolve(direction: str, source: str, dest: str, amount: float,
            objective: str = OBJECTIVE_COST) -> list[dict]:
    """Return matching provider options, best-first for the given objective.

    cost  → lowest fee_bps (tiebreak: faster); speed → fastest (tiebreak: cheaper)."""
    candidates = [p for p in PROVIDERS if p.covers(direction, source, dest)]
    if objective == OBJECTIVE_SPEED:
        candidates.sort(key=lambda p: (p.settlement_seconds, p.fee_bps))
    else:
        candidates.sort(key=lambda p: (p.fee_bps, p.settlement_seconds))
    return [_option(p, amount) for p in candidates]
