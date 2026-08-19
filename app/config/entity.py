"""Build entity row values from input users and YAML column definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.excel_io import REQUIRED_INPUT_COLUMNS
from app.security import OPTIONAL_SECURITY_COLUMNS

ODATA_IMPORT_FILES = (
    "employee_v2.yaml",
    "user_information.yaml",
    "person_users.yaml",
    "security_user_role_association.yaml",
    "security_user_role_organization.yaml",
)

_ALLOWED_SOURCES = frozenset(
    {
        *REQUIRED_INPUT_COLUMNS,
        *OPTIONAL_SECURITY_COLUMNS,
        "_display_name",
        "SecurityRoleName",
        "SecurityOrganizationId",
        "SecurityHierarchyType",
    }
)

_ALLOWED_ODATA_RUNTIME = frozenset({"party_number", "security_role_identifier"})


def _validate_column_spec(path: Path, col_name: str, spec: Any) -> None:
    if isinstance(spec, dict):
        source = spec.get("source")
        if source is not None and str(source) not in _ALLOWED_SOURCES:
            raise ValueError(
                f"{path}: column {col_name!r} has unknown source {source!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_SOURCES))}"
            )
        runtime_key = spec.get("odata_runtime")
        if runtime_key is not None and str(runtime_key) not in _ALLOWED_ODATA_RUNTIME:
            raise ValueError(
                f"{path}: column {col_name!r} has unknown odata_runtime {runtime_key!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_ODATA_RUNTIME))}"
            )
    elif spec is not None and not isinstance(spec, str | int | float | bool):
        raise ValueError(
            f"{path}: column {col_name!r} must be a mapping or scalar default, "
            f"got {type(spec).__name__}"
        )


def load_entity_config(path: Path, *, require_odata: bool = False) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict) or "columns" not in cfg:
        raise ValueError(f"Invalid entity config: {path}")
    if require_odata and not cfg.get("odata_entity"):
        raise ValueError(f"Entity config missing odata_entity: {path}")
    columns = cfg.get("columns")
    if not isinstance(columns, dict):
        raise ValueError(f"Invalid entity config columns in {path}")
    for col_name, spec in columns.items():
        _validate_column_spec(path, str(col_name), spec)
    return cfg


def enrich_user(user: dict[str, str]) -> dict[str, str]:
    """Add synthetic fields used by YAML source mappings."""
    enriched = dict(user)
    first = user.get("FirstName", "").strip()
    last = user.get("LastName", "").strip()
    enriched["_display_name"] = f"{first} {last}".strip()
    return enriched


def resolve_row(columns: dict[str, Any], user: dict[str, str]) -> list[Any]:
    """Resolve one row from YAML column definitions and a user record."""
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
