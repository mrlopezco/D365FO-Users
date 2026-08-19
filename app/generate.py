"""Generate DMF Employee V2 and User Information Excel files from input users."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.excel_io import read_users, write_workbook
from app.security import (
    assignment_row_to_user_dict,
    expand_org_assignments,
    expand_role_assignments,
)

CONFIG_FILES = (
    "employee_v2.yaml",
    "user_information.yaml",
    "security_user_role_association.yaml",
    "security_user_role_organization.yaml",
)
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
    if not isinstance(cfg, dict) or "columns" not in cfg or "output_filename" not in cfg:
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
    """Resolve one output row from YAML column definitions and an enriched user record."""
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
    """Build DMF header list and data rows from entity config and user records."""
    columns: dict[str, Any] = config["columns"]
    headers = list(columns.keys())
    rows = [resolve_row(columns, enrich_user(u)) for u in users]
    return headers, rows


def build_security_entity_rows(
    config: dict[str, Any],
    users: list[dict[str, str]],
    *,
    organization: bool,
) -> tuple[list[str], list[list[Any]]]:
    """Build DMF rows for security entities (one row per role or org assignment)."""
    columns: dict[str, Any] = config["columns"]
    headers = list(columns.keys())
    if organization:
        assignments = expand_org_assignments(users)
    else:
        assignments = expand_role_assignments(users)
    rows = [
        resolve_row(columns, assignment_row_to_user_dict(a)) for a in assignments
    ]
    return headers, rows


def generate_security_workbook(
    config: dict[str, Any],
    users: list[dict[str, str]],
    output_dir: Path,
    *,
    organization: bool,
) -> Path | None:
    headers, rows = build_security_entity_rows(
        config, users, organization=organization
    )
    if not rows:
        return None
    out_path = output_dir / config["output_filename"]
    write_workbook(out_path, headers, rows)
    return out_path


def generate_entity_workbook(
    config: dict[str, Any],
    users: list[dict[str, str]],
    output_dir: Path,
) -> Path:
    headers, rows = build_entity_rows(config, users)
    out_path = output_dir / config["output_filename"]
    write_workbook(out_path, headers, rows)
    return out_path


def run(
    input_path: Path,
    config_dir: Path,
    output_root: Path,
    timestamp: datetime | None = None,
) -> Path:
    """Generate both DMF import files into output_root/<timestamp>/."""
    users = read_users(input_path)
    ts = timestamp or datetime.now()
    output_dir = output_root / ts.strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for filename in CONFIG_FILES:
        cfg_path = config_dir / filename
        if not cfg_path.exists():
            raise FileNotFoundError(f"Missing config file: {cfg_path}")
        config = load_entity_config(cfg_path)
        if filename == "security_user_role_association.yaml":
            path = generate_security_workbook(
                config, users, output_dir, organization=False
            )
            if path:
                written.append(path)
        elif filename == "security_user_role_organization.yaml":
            path = generate_security_workbook(
                config, users, output_dir, organization=True
            )
            if path:
                written.append(path)
        else:
            written.append(generate_entity_workbook(config, users, output_dir))

    return output_dir
