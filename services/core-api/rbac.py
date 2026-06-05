"""RBAC — granular permissions derived from a tenant member's roles.

Permissions are NOT stored; they are derived from roles via this catalog, so the
authorization surface lives in one auditable place. A user's effective
permissions are the union over their roles.
"""

from __future__ import annotations

from enum import Enum

from db import Role


class Permission(str, Enum):
    VIEW_PORTFOLIO = "view_portfolio"
    VIEW_OPPORTUNITIES = "view_opportunities"
    START_PAPER_TRADING = "start_paper_trading"
    ENABLE_LIVE_TRADING = "enable_live_trading"
    REQUEST_WITHDRAWAL = "request_withdrawal"
    MANAGE_TEAM = "manage_team"
    VIEW_AUDIT_LOG = "view_audit_log"
    MANAGE_RISK_LIMITS = "manage_risk_limits"


P = Permission
_ALL = set(P)

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.OWNER: set(_ALL),
    # Admin runs the account but moving money out (withdrawal) is owner-only.
    Role.ADMIN: _ALL - {P.REQUEST_WITHDRAWAL},
    Role.OPERATOR: {
        P.VIEW_PORTFOLIO, P.VIEW_OPPORTUNITIES, P.START_PAPER_TRADING,
        P.ENABLE_LIVE_TRADING, P.MANAGE_RISK_LIMITS,
    },
    Role.ANALYST: {P.VIEW_PORTFOLIO, P.VIEW_OPPORTUNITIES, P.VIEW_AUDIT_LOG},
    Role.SUPPORT: {P.VIEW_PORTFOLIO, P.VIEW_AUDIT_LOG},
}


def effective_permissions(roles) -> set[Permission]:
    perms: set[Permission] = set()
    for r in roles:
        perms |= ROLE_PERMISSIONS.get(r, set())
    return perms


def has_permission(roles, perm: Permission) -> bool:
    return perm in effective_permissions(roles)
