# ADR-0007: Provider adapter contract — per-domain adapter interface, sandbox-first

Status: Accepted (documents an existing, already-implemented decision)
Date: 2026-09-17
Owner: Payments Integration role

## Context
The contract requires a provider-neutral adapter contract (§3.F, §7.4) with
sandbox/deterministic adapters implemented first, and a conformance test
suite every provider must pass.

## Decision
Adapter contracts are defined per-domain, not as one universal interface,
because the domains genuinely differ in shape:
- **Market data**: `shared/connectors` (`MarketDataAdapter`, `VenueHealth`) —
  every exchange collector (`services/market-data/collectors/*.py`)
  implements the same `BaseCollector` contract (`health()`, `is_healthy()`,
  `_to_exchange_tick()`).
- **On/off-ramp providers**: `services/onramp-orchestrator/sandbox_provider.py`
  (and offramp's equivalent) implement a deterministic state-machine
  simulator (`PENDING → PROCESSING → COMPLETED`, with injectable failure
  modes) — this is the pattern to replicate for the contract's other
  required sandbox adapters (KYC/KYB status, sanctions/screening — see
  `docs/STATUS.md` Backlog, not yet built).
- **FX/routing**: `services/fx-rate-service` + `services/routing-service`
  provide quote/route data; `services/corridor-engine` scores Africa-market
  corridors from these plus `corridor-intelligence-service`.
- Every sandbox adapter simulates realistic failure modes, not just the
  happy path — `onramp-orchestrator`'s idempotency-key replay behavior and
  `paper-trader`'s partial-fill/spread-collapse simulators are the existing
  reference implementations of this principle.

## Alternatives considered
- One universal `ProviderAdapter` interface for market data, payments, FX,
  and compliance — rejected: these have almost no common method surface
  (a market-data collector streams ticks; a payout provider processes a
  discrete state machine; a compliance check is a point-in-time
  verdict) — forcing one interface would produce an interface with mostly
  unused methods per implementer, which is worse than no shared contract.

## Consequences
- No single "conformance test suite every provider must pass" exists yet
  across domains — each domain has its own test conventions
  (`test_exchange_symbols.py` for market-data, `test_onramp.py`'s
  idempotency/lifecycle tests for on-ramp). Acceptable given the domains
  don't share behavior to conform to; a cross-domain adapter *within* one
  domain (e.g. a second on-ramp provider) should be required to pass the
  existing domain's test suite unmodified.
- Real (non-sandbox) provider integration is gated on licensing per this
  repo's existing execution posture — no code change needed to enforce
  this; `live_enabled=false` and the SANDBOX-only API plane already do.

## Revisit when
A second concrete provider is added within any one domain (e.g. a second
KYC vendor, a second FX quote source) — that is the point where a formal
conformance suite for that specific domain earns its cost.
