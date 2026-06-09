# Connector architecture — pluggable venues, CCXT-first

**Status:** architecture/build spec. The connector layer is the **shared core** for
both businesses (hybrid trading + the Africa crypto API layer): every venue —
exchange, DEX, stablecoin rail, payout endpoint, FX feed — is normalized into one
internal schema so the rest of the platform never depends on a specific vendor.

```
[Exchange / DEX / Rail / FX]
   → [Connector Adapter]        venue-specific I/O, auth, reconnect, sequencing
   → [Normalizer]               → one internal event schema (no venue-native shapes)
   → [Internal Event Schema]    Pub/Sub topics + Redis hot state
   → [opportunity/signal/risk engines]  (internal)  AND  [Africa API layer]  (external)
```

## Principle: do both, in sequence (not one vendor forever)

- **Phase 1 — CCXT Pro** as the centralized-exchange breadth layer (100+ venues,
  unified WebSocket). Fastest path to validate signal quality, scoring, paper
  trading. **Already in the repo**: `services/market-data` uses CCXT Pro
  collectors behind a `BaseCollector` ABC (`_build_client()` + watch streams).
- **Phase 2 — native adapters** for the top 3–5 venues that prove core to the
  edge (latency, sequencing, reconnect, auth/feature gaps CCXT abstracts away).
- **Phase 3 — CCXT Pro stays** as the long-tail + fallback layer behind the same
  adapter contract.

Add native only when a venue is a top liquidity/revenue source, latency/sequence
sensitive, missing features in the abstraction, or needs custom auth/order semantics.

## Connector contract (the seam)

Every connector implements the same adapter interface so the platform stays
unchanged whether the source is CCXT Pro, a native VALR client, or Bitquery:

```python
class MarketDataAdapter(Protocol):
    venue: str
    region: str            # 'global' | 'africa'
    market_type: str       # 'cex' | 'dex' | 'rail' | 'fx'
    async def stream(self, symbols: list[str]) -> AsyncIterator[NormalizedEvent]: ...
    async def health(self) -> VenueHealth: ...

class TradingAdapter(Protocol):           # only venues we execute on
    async def place(self, order: OrderRequest) -> OrderResult: ...
    async def balances(self) -> list[Balance]: ...
```

All adapters emit the **internal event schema** (never venue-native):
`market.ticker.updated`, `market.orderbook.{snapshot,delta}`, `market.trade.printed`,
`market.venue.health`, `fx.rate.updated`, `wallet.balance.updated` — canonical
fields `{event_id, timestamp, source, region, market_type, symbol, base/quote,
bids/asks, sequence, raw}`. Normalization happens once, at the adapter boundary,
**before** any strategy/signal logic runs.

## Connector categories + picks

| Category | Connector | Notes |
|---|---|---|
| CEX breadth | **CCXT Pro** | unified WS; the default. ✅ market-data uses it |
| CEX top venues | native adapters | Coinbase Advanced Trade, Binance-compat, Kraken — Phase 2 |
| On-chain / DEX | **Alchemy + Bitquery** + node libs | contract events, swaps, pools, mempool — separate service `connector-onchain` |
| Africa rails | **Yellow Card, VALR, Luno** | stablecoin/exchange + payout; `connector-africa-rails` |
| FX | local-currency FX providers | NGN/ZAR/KES/GHS — `fx-rate-service` exists |
| Reference | CoinGecko-style aggregator | validation/metadata only, NOT execution |

## Dual-use: one connector, two consumers

The shared-core design is the moat: each connector serves **both** audiences
behind the same normalizer.

- **Internal (trading):** trading-grade events → signal-engine, opportunity-engine,
  risk-engine, route optimizer.
- **External (Africa API layer):** API-grade abstractions → Account Connectivity,
  Balance, On/Off-Ramp, Routing, Compliance APIs (see `docs/PLATFORM_ARCHITECTURE.md`).

So adapters expose both a fast internal event stream **and** request/response
abstractions the API products wrap. Build the connector + normalizer + compliance
primitives **once**; both businesses consume them. API-layer usage also enriches
corridor/settlement data that improves trading routing — the data flywheel.

## Build order

1. **Now (done/partial):** CCXT Pro CEX collectors (`market-data`), FX (`fx-rate-service`).
2. **Next:** formalize the `MarketDataAdapter` Protocol in `shared/`; refactor
   `BaseCollector` to it so CCXT, native, and on-chain adapters are interchangeable.
3. **Then:** `connector-africa-rails` (VALR/Luno/Yellow Card) + `connector-onchain`
   (Bitquery/Alchemy), each emitting the same internal schema.
4. **Later (gated on licensing):** trading adapters (order placement) and the
   on/off-ramp orchestrators for the external API business.

## Guardrails

- Normalize at the boundary — strategy/signal/API code never sees venue-native shapes.
- Per-venue health + reconnect + sequence handling live in the adapter, not callers.
- Credentials in Secret Manager (platform-managed under managed custody).
- The connector layer is plumbing — it never makes trade or payout *decisions*;
  the risk-engine (trading) and compliance-screening (API) remain the authorities.
