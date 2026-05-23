"""dashboard-api — read-only aggregator for the ops dashboard.

Single Cloud Run service that:
  * Serves the static dashboard at ``GET /``
  * Aggregates BigQuery + Redis state behind a small JSON API the
    frontend polls every 10s

All endpoints are read-only. The dashboard is the only consumer that
needs to be reachable from a browser, so this service is the only one
that gets `allUsers` invoker permission — see the IAM block in the
Terraform module.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("dashboard-api")

app = FastAPI(title="dashboard-api", version="0.1.0")

# Browsers polling from a different origin (e.g. localhost during dev) need CORS.
# In Cloud Run prod the dashboard is served from the same origin so this is a
# no-op there.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── lazy clients ────────────────────────────────────────────────────────────

def _project_id() -> str:
    pid = os.environ.get("GCP_PROJECT_ID")
    if not pid:
        raise RuntimeError("GCP_PROJECT_ID unset")
    return pid


def _bq():
    from google.cloud import bigquery

    return bigquery.Client(project=_project_id())


def _redis():
    from redis import Redis

    return Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
        socket_timeout=2.0,
    )


# ── models ──────────────────────────────────────────────────────────────────

class KPISummary(BaseModel):
    capital_usd: float
    daily_pnl_usd: float
    daily_pnl_pct: float
    open_positions: int
    live_opportunities: int
    best_spread_bps: float | None = None
    best_spread_pair: str | None = None
    cumulative_pnl_usd: float
    sharpe_30d: float | None = None
    trades_today: int


class ExposureBar(BaseModel):
    label: str
    current_pct: float
    limit_pct: float


class OpportunityRow(BaseModel):
    strategy: str
    asset: str
    long_exchange: str
    short_exchange: str
    net_edge_bps: float
    persist: str  # human-readable persistence
    detected_at: datetime


class AuditEntry(BaseModel):
    time: str
    type: str  # 'info' | 'warn' | 'ok'
    event: str


class PnLPoint(BaseModel):
    d: str
    pnl: float


class DashboardSummary(BaseModel):
    mode: str  # 'paper' | 'live' | 'halted'
    kill_switch_active: bool
    kpis: KPISummary
    risk_bars: list[ExposureBar]
    opportunities: list[OpportunityRow]
    audit_log: list[AuditEntry]
    pnl_history: list[PnLPoint]


# ── helpers ─────────────────────────────────────────────────────────────────

def _fetch_kpis(r) -> KPISummary:
    capital = float(r.get("risk:capital_usd") or 0.0)
    daily_pnl = float(r.get("risk:daily_pnl_usd") or 0.0)
    pct = (daily_pnl / capital * 100.0) if capital > 0 else 0.0

    # Open positions: open trades in BQ. If BQ unreachable, default to 0.
    open_positions = 0
    cumulative = 0.0
    trades_today = 0
    sharpe = None
    try:
        client = _bq()
        rows = list(
            client.query(
                f"""
                SELECT
                  COUNTIF(status = 'open')                                          AS open_pos,
                  SUM(IF(status = 'closed', net_pnl_usd, 0))                        AS cum_pnl,
                  COUNTIF(status = 'closed' AND DATE(opened_at) = CURRENT_DATE())   AS trades_today
                FROM `{_project_id()}.arb_trading.trades`
                WHERE opened_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
                """
            ).result()
        )
        if rows:
            row = rows[0]
            open_positions = int(row.get("open_pos") or 0)
            cumulative = float(row.get("cum_pnl") or 0.0)
            trades_today = int(row.get("trades_today") or 0)
    except Exception:
        logger.exception("BQ summary query failed; returning zeros")

    # Best live opportunity (and overall count)
    best_spread_bps = None
    best_spread_pair = None
    live_count = 0
    try:
        client = _bq()
        rows = list(
            client.query(
                f"""
                SELECT asset, long_exchange, short_exchange, net_edge_bps
                FROM `{_project_id()}.arb_trading.opportunities`
                WHERE detected_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 15 MINUTE)
                  AND net_edge_bps > 8
                ORDER BY net_edge_bps DESC
                LIMIT 25
                """
            ).result()
        )
        live_count = len(rows)
        if rows:
            top = rows[0]
            best_spread_bps = float(top["net_edge_bps"])
            best_spread_pair = f"{top['asset']} {top['long_exchange']}→{top['short_exchange']}"
    except Exception:
        logger.exception("BQ opportunities query failed")

    return KPISummary(
        capital_usd=capital,
        daily_pnl_usd=daily_pnl,
        daily_pnl_pct=pct,
        open_positions=open_positions,
        live_opportunities=live_count,
        best_spread_bps=best_spread_bps,
        best_spread_pair=best_spread_pair,
        cumulative_pnl_usd=cumulative,
        sharpe_30d=sharpe,
        trades_today=trades_today,
    )


def _fetch_risk_bars(r) -> list[ExposureBar]:
    capital = float(r.get("risk:capital_usd") or 0.0)
    daily_pnl = float(r.get("risk:daily_pnl_usd") or 0.0)
    daily_loss_pct = (-daily_pnl / capital * 100.0) if capital > 0 and daily_pnl < 0 else 0.0

    bars: list[ExposureBar] = [
        ExposureBar(label="Daily Drawdown", current_pct=daily_loss_pct, limit_pct=1.0),
    ]
    for key in r.scan_iter(match="risk:exposure:*"):
        venue = key.removeprefix("risk:exposure:")
        bars.append(
            ExposureBar(
                label=f"{venue.title()} Exposure",
                current_pct=float(r.get(key) or 0.0),
                limit_pct=25.0,
            )
        )
    for key in r.scan_iter(match="risk:concentration:*"):
        asset = key.removeprefix("risk:concentration:")
        bars.append(
            ExposureBar(
                label=f"{asset} Concentration",
                current_pct=float(r.get(key) or 0.0),
                limit_pct=20.0,
            )
        )
    leverage = float(r.get("risk:leverage_multiplier") or 1.0)
    bars.append(ExposureBar(label="Max Leverage", current_pct=leverage, limit_pct=1.0))
    return bars


def _fetch_opportunities() -> list[OpportunityRow]:
    out: list[OpportunityRow] = []
    try:
        client = _bq()
        rows = client.query(
            f"""
            SELECT strategy, asset, long_exchange, short_exchange, net_edge_bps, detected_at
            FROM `{_project_id()}.arb_trading.opportunities`
            WHERE detected_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
            ORDER BY detected_at DESC
            LIMIT 25
            """
        ).result()
        now = datetime.utcnow()
        for r in rows:
            age = now - r["detected_at"].replace(tzinfo=None)
            minutes = int(age.total_seconds() / 60)
            persist = f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"
            out.append(
                OpportunityRow(
                    strategy=r["strategy"],
                    asset=r["asset"],
                    long_exchange=r["long_exchange"],
                    short_exchange=r["short_exchange"],
                    net_edge_bps=float(r["net_edge_bps"]),
                    persist=persist,
                    detected_at=r["detected_at"],
                )
            )
    except Exception:
        logger.exception("opportunities feed query failed")
    return out


def _fetch_audit_log() -> list[AuditEntry]:
    """Recent events from trades + opportunities + risk_events, merged + sorted."""
    out: list[AuditEntry] = []
    try:
        client = _bq()
        rows = client.query(
            f"""
            SELECT * FROM (
              SELECT FORMAT_TIMESTAMP('%H:%M:%S', opened_at) AS t,
                     'ok' AS type,
                     FORMAT('Trade opened: %s %s→%s', asset, long_exchange, short_exchange) AS event,
                     opened_at AS ts
              FROM `{_project_id()}.arb_trading.trades`
              WHERE opened_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)
              UNION ALL
              SELECT FORMAT_TIMESTAMP('%H:%M:%S', emitted_at) AS t,
                     severity AS type,
                     CONCAT(alert_type, ': ', message) AS event,
                     emitted_at AS ts
              FROM `{_project_id()}.arb_risk.risk_events`
              WHERE emitted_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)
            )
            ORDER BY ts DESC
            LIMIT 50
            """
        ).result()
        for r in rows:
            out.append(AuditEntry(time=r["t"], type=r["type"], event=r["event"]))
    except Exception:
        logger.exception("audit log query failed")
    return out


def _fetch_pnl_history(days: int = 30) -> list[PnLPoint]:
    out: list[PnLPoint] = []
    try:
        client = _bq()
        rows = client.query(
            f"""
            SELECT
              DATE(opened_at) AS d,
              SUM(net_pnl_usd) AS daily_pnl
            FROM `{_project_id()}.arb_trading.trades`
            WHERE status = 'closed'
              AND opened_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
            GROUP BY d
            ORDER BY d ASC
            """
        ).result()
        running = 0.0
        for r in rows:
            running += float(r["daily_pnl"] or 0.0)
            out.append(PnLPoint(d=str(r["d"]), pnl=running))
    except Exception:
        logger.exception("pnl history query failed")
    return out


# ── routes ──────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/summary", response_model=DashboardSummary)
def summary() -> DashboardSummary:
    try:
        r = _redis()
        # ping to differentiate "Redis unreachable" from "no data yet"
        r.ping()
    except Exception:
        logger.exception("Redis unreachable")
        raise HTTPException(status_code=503, detail="redis_unavailable")

    kill = r.get("risk:kill_switch:active") == "1"
    mode = "halted" if kill else os.environ.get("PAPER_TRADING_MODE", "paper")

    return DashboardSummary(
        mode=mode,
        kill_switch_active=kill,
        kpis=_fetch_kpis(r),
        risk_bars=_fetch_risk_bars(r),
        opportunities=_fetch_opportunities(),
        audit_log=_fetch_audit_log(),
        pnl_history=_fetch_pnl_history(),
    )


_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def index():
    """Serve the dashboard HTML at the root."""
    index_path = _STATIC_DIR / "index.html"
    if not index_path.exists():
        return JSONResponse({"error": "dashboard html not bundled"}, status_code=500)
    return FileResponse(index_path, media_type="text/html")
