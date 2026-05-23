# Tax Compliance Guide

## Overview

The trade-ledger service maintains a permanent audit trail of all trading activity in BigQuery and generates IRS Form 8949-compatible tax reports. This document describes the tax compliance strategy, retention policies, and export procedures.

## IRS Form 8949 Mapping

Form 8949 (Sales and Other Dispositions of Capital Assets) is used to report capital gains and losses from cryptocurrency trades. Each closed `live` trade generates one row in the export.

### Column Mapping

| Form 8949 Column | BigQuery Source | Calculation |
|-----------------|-----------------|-------------|
| (a) Description of Property | `legs.asset`, `legs.size` | Largest buy-side leg quantity + asset symbol (e.g., "1.50000000 BTC") |
| (b) Date Acquired | `opened_at` | Trade open timestamp (MM/DD/YYYY) |
| (c) Date Sold or Disposed | `closed_at` | Trade close timestamp (MM/DD/YYYY) |
| (d) Proceeds | `legs` (sell side) | Sum of (size × fill_price - fee_usd) for all sell legs |
| (e) Cost or Other Basis | `legs` (buy side) | Sum of (size × fill_price + fee_usd) for all buy legs |
| (f) Code(s) | — | Left blank (wash sale tracking not automated) |
| (g) Amount of Adjustment | — | Left blank |
| (h) Gain or (loss) | Calculated | Proceeds - Cost Basis |

### Example

A funding-rate arbitrage trade that goes long 1.0 BTC on Kraken at $60,000 (fee: $15) and short 1.0 BTC on Hyperliquid at $60,100 (fee: $3), held 8 hours and closed, would export as:

```
Description: "1.00000000 BTC"
Date acquired: "01/15/2026"
Date sold: "01/15/2026"
Proceeds: 60097.00    (60100 - 3)
Cost basis: 60015.00  (60000 + 15)
Gain: 82.00
```

## Wash Sale Rules

**Important**: As of 2025, cryptocurrency is NOT subject to IRS wash sale rules (which apply to securities). Traders can realize losses and immediately repurchase the same asset. However, Congress has proposed extending wash sale rules to crypto — monitor IRS guidance.

The export does NOT automatically flag wash sales. If wash sale rules become applicable to crypto, you must:

1. Identify substantially identical positions within 30 days before/after a loss realization.
2. Manually add code 'W' to column (f) and adjust the basis in column (g).

## Record Retention Policy

BigQuery table retention periods comply with IRS recordkeeping requirements:

| Dataset | Tables | Retention | Purpose |
|---------|--------|-----------|---------|
| `arb_trading` | trades, opportunities, funding_events | **7 years** | Tax audit support (IRS statute of limitations) |
| `arb_market_data` | (if created) | 90 days | Operational; not tax-relevant |
| `arb_risk` | risk_events | **Forever** | Audit trail for kill-switch activations, drawdown breaches |
| `arb_audit` | audit_log | **Forever** | System change log (limit adjustments, manual interventions) |

The 7-year retention for `arb_trading` matches the IRS statute of limitations for tax fraud (3 years for simple audits; 6-7 years recommended for complex trading activity).

## Generating Tax Exports

### API Endpoint

```bash
GET /tax-export?year=2026
```

Returns a CSV file named `form_8949_2026.csv` with all closed `live` trades from that tax year.

**Filters applied:**
- `trade_type = 'live'` (paper trades excluded)
- `status = 'closed'` (open positions excluded — they report when closed)
- `EXTRACT(YEAR FROM closed_at) = <year>`

### Example Request

```bash
curl -o form_8949_2026.csv \
  "https://trade-ledger-xxxx-uc.a.run.app/tax-export?year=2026"
```

### Local Testing (No GCP)

When `GCP_PROJECT_ID` is unset, the `/tax-export` endpoint will fail with a runtime error. For local dev, set the env var to a test project or mock the BigQuery client.

## Workflow for Tax Filing

1. **End of tax year**: Generate the export for the calendar year.
   ```bash
   curl -o form_8949_2026.csv \
     "https://trade-ledger-prod.run.app/tax-export?year=2026"
   ```

2. **Review the CSV**: Verify that all trades are included and totals match your expected PnL. Cross-check against the dashboard's annual summary.

3. **Wash sale check** (if applicable): If wash sale rules are extended to crypto, manually review for substantially identical positions within 30 days of a loss.

4. **Hand off to CPA**: Provide the CSV and any supplemental notes (e.g., funding payments, transfer fees) to your tax preparer. The CSV format matches Form 8949, but a CPA should validate the categorization (short-term vs. long-term capital gains, ordinary income from staking, etc.).

5. **Retain BigQuery access**: Keep the GCP project active for 7 years or export the full `arb_trading.trades` table to archival storage (Cloud Storage, S3, etc.) before decommissioning.

## Short-Term vs. Long-Term Capital Gains

The export does NOT automatically classify trades as short-term (<1 year holding period) vs. long-term (≥1 year). Most arbitrage trades are intraday or held <24 hours, so they are **short-term gains** taxed at ordinary income rates.

If you hold a position >1 year (unlikely for arb strategies), manually flag it on Form 8949 and report on Schedule D Part II (long-term).

## Ordinary Income vs. Capital Gains

The following are NOT reported in the Form 8949 export and require separate accounting:

- **Funding payments received**: Currently aggregated in `funding_collected_usd` within each trade. For tax purposes, funding payments may be treated as ordinary income or miscellaneous income — consult a CPA.
- **Staking rewards**: Not applicable to this system (no staking).
- **Airdrops / forks**: Not applicable to this system (no holding for airdrops).

## Cost Basis Method

The export uses **specific identification** (FIFO-equivalent for each trade pair). Each trade is self-contained: you buy X BTC and sell X BTC within the same `Trade` record. There is no need for FIFO/LIFO lot tracking across trades since positions are netted intraday.

If you transfer crypto between exchanges or hold inventory outside the system, you must track cost basis separately.

## Audit Defense

If audited, provide:

1. The Form 8949 CSV export for the year in question.
2. BigQuery console access (read-only) to the `arb_trading.trades` table.
3. The `audit_log` table showing all system changes (limit adjustments, kill-switch activations).
4. The dashboard's PnL summary for the year.

The system's write-once audit trail (trade-ledger is the sole BigQuery writer) provides strong evidence that records were not retroactively altered.

## Multi-Year Exports

To export multiple years:

```bash
for year in 2024 2025 2026; do
  curl -o "form_8949_${year}.csv" \
    "https://trade-ledger-prod.run.app/tax-export?year=${year}"
done
```

## Contact

For tax-specific questions, consult a CPA familiar with cryptocurrency taxation. This document is informational only and does not constitute tax advice.
