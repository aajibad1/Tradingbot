"""Invoice pricing logic (docs/06 partner invoices). Pure + testable.

Turns a tenant's metered usage for a period into an invoice with line items: a
plan base fee plus per-unit charges for usage beyond the plan's included units.

Money is rounded to cents as float for the sandbox; production should use Decimal
and a real tax/rounding policy. Pricing is intentionally simple and explicit so
the line items are auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

# Invoice lifecycle statuses (distinct from the async-op Status taxonomy).
DRAFT = "draft"
ISSUED = "issued"
PAID = "paid"
VOID = "void"


@dataclass
class Plan:
    plan_id: str
    base_fee: float            # fixed monthly fee
    included_units: int        # API calls included in the base fee
    per_unit_rate: float       # charge per unit beyond included
    currency: str = "USD"


def _money(x: float) -> float:
    return round(x + 1e-9, 2)


def build_line_items(plan: Plan, usage_total: int) -> tuple[list[dict], float]:
    """Return ``(line_items, total)`` for a plan + usage. The base fee is always a
    line; a usage line is added only when usage exceeds the included allotment."""
    items: list[dict] = [{
        "description": f"{plan.plan_id} base plan fee",
        "quantity": 1,
        "unit_price": _money(plan.base_fee),
        "amount": _money(plan.base_fee),
    }]
    billable = max(0, usage_total - plan.included_units)
    if billable > 0:
        amount = _money(billable * plan.per_unit_rate)
        items.append({
            "description": f"API usage — {billable} units over {plan.included_units} included",
            "quantity": billable,
            "unit_price": plan.per_unit_rate,
            "amount": amount,
        })
    total = _money(sum(i["amount"] for i in items))
    return items, total
