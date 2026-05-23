"""READ-tier report generation.

Daily report is a structured summary the AI can either return raw or
render via the `prompts/daily_report.txt` template.
"""

from __future__ import annotations

import os
import pathlib
from datetime import date
from typing import Any

from tools.read_tools import get_balances, get_daily_pnl, get_exchange_health, get_open_positions


_PROMPT_DIR = pathlib.Path(__file__).parent.parent / "prompts"


def generate_daily_report(target_date: date | None = None) -> dict[str, Any]:
    """Assemble a structured daily report.

    Returns a dict with raw stats and a `prompt` field — the prompt is the
    LLM input string, the rest is structured data for downstream tools.
    """
    target_date = target_date or date.today()
    pnl = get_daily_pnl(target_date)
    balances = get_balances()
    positions = get_open_positions()
    health = get_exchange_health()

    template = (_PROMPT_DIR / "daily_report.txt").read_text()
    prompt = template.format(
        date=target_date.isoformat(),
        pnl=pnl,
        balances=balances,
        positions=positions,
        health=health,
        project_id=os.environ.get("GCP_PROJECT_ID", "(unset)"),
    )

    return {
        "date": target_date.isoformat(),
        "pnl": pnl,
        "balances": balances,
        "open_positions": positions,
        "exchange_health": health,
        "prompt": prompt,
    }
