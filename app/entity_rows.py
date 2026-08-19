"""Build entity row values from input users and YAML column definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ODATA_IMPORT_FILES = (
    "employee_v2.yaml",
    "user_information.yaml",
    "person_users.yaml",
    "security_user_role_association.yaml",
    "security_user_role_organization.yaml",
)


def load_entity_config(path: Path, *, require_odata: bool = False) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict) or "columns" not in cfg:
        raise ValueError(f"Invalid entity config: {path}")
    if require_odata and not cfg.get("odata_entity"):
        raise ValueError(f"Entity config missing odata_entity: {path}")
    return cfg


def enrich_user(user: dict[str, str]) -> dict[str, str]:
    """Add synthetic fields used by YAML source mappings."""
    enriched = dict(user)
    first = user.get("FirstName", "").strip()
    last = user.get("LastName", "").strip()
    enriched["_display_name"] = f"{first} {last}".strip()
    return enriched


def resolve_row(columns: dict[str, Any], user: dict[str, str]) -> list[Any]:
    """Resolve one row from YAML column definitions and an enriched user record."""
    values: list[Any] = []
    for _name, spec in columns.items():
        if not isinstance(spec, dict):
            spec = {"default": spec}

        source = spec.get("source")
        default = spec.get("default")
        value: Any = None

        if source:
            sourced = user.get(source, "")
            if sourced not in (None, ""):
                value = sourced

        if value in (None, ""):
            value = "" if default is None else default

        values.append(value)
    return values


def build_entity_rows(
    config: dict[str, Any],
    users: list[dict[str, str]],
) -> tuple[list[str], list[list[Any]]]:
    """Build column headers and data rows from entity config and user records."""
    columns: dict[str, Any] = config["columns"]
    headers = list(columns.keys())
    rows = [resolve_row(columns, enrich_user(u)) for u in users]
    return headers, rows
