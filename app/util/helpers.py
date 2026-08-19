"""Shared helpers for import modules."""

from __future__ import annotations

from typing import Any

from app.d365.environments import D365Environment


def user_label(user: dict[str, str]) -> str:
    for key in ("Email", "UserId", "Alias"):
        val = user.get(key, "").strip()
        if val:
            return val
    return user.get("FirstName", "user")


def resolve_company(env: D365Environment, config: dict[str, Any]) -> str | None:
    if env.company:
        return env.company
    columns: dict[str, Any] = config.get("columns") or {}
    for col_name in ("COMPANY", "EMPLOYMENTLEGALENTITYID"):
        spec = columns.get(col_name)
        if isinstance(spec, dict):
            default = spec.get("default")
            if default not in (None, ""):
                return str(default).strip()
    return None
