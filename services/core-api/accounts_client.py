"""Thin client to accounts-service — the funding seam.

Managed custody: a user funds the platform, which records the movement in the
internal double-entry ledger (accounts-service). core-api orchestrates the user
action; accounts-service owns the money. Kept behind this one client so it's a
mockable seam (tests override the FastAPI dependency with an in-memory fake).
"""

from __future__ import annotations

import httpx


class AccountsError(Exception):
    pass


class AccountsInsufficientFunds(AccountsError):
    pass


class AccountsClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, tenant: str, op: str, asset: str, amount) -> dict:
        r = httpx.post(
            f"{self.base_url}/v1/accounts/{tenant}/{op}",
            json={"asset": asset, "amount": str(amount)},
            timeout=self.timeout,
        )
        if r.status_code == 409:
            raise AccountsInsufficientFunds(r.json().get("detail", "insufficient funds"))
        r.raise_for_status()
        return r.json()

    def deposit(self, tenant: str, asset: str, amount) -> dict:
        return self._post(tenant, "deposit", asset, amount)

    def withdraw(self, tenant: str, asset: str, amount) -> dict:
        return self._post(tenant, "withdraw", asset, amount)

    def balances(self, tenant: str, asset: str) -> dict:
        r = httpx.get(
            f"{self.base_url}/v1/accounts/{tenant}/balances",
            params={"asset": asset}, timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()
