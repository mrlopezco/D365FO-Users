"""Map DMF column headers to F&O OData JSON property names (PascalCase)."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from app.d365.fo_client import (
    extract_fo_error_message,
    normalize_environment_url,
    odata_get_json,
)


def normalize_property_key(name: str) -> str:
    return re.sub(r"[_\s]", "", name).casefold()


def build_dmf_to_odata_map(
    dmf_headers: list[str],
    odata_property_names: list[str],
) -> dict[str, str]:
    """Map each DMF header to OData property name (case-insensitive, ignore underscores)."""
    by_norm = {normalize_property_key(p): p for p in odata_property_names}
    mapping: dict[str, str] = {}
    for header in dmf_headers:
        norm = normalize_property_key(header)
        odata_name = by_norm.get(norm)
        if odata_name:
            mapping[header] = odata_name
    return mapping


@dataclass
class EntityODataSchema:
    property_names: list[str]
    sample_types: dict[str, type] = field(default_factory=dict)


def fetch_entity_schema(
    *,
    environment_url: str,
    entity_name: str,
    access_token: str,
    company: str | None = None,
) -> EntityODataSchema:
    """Discover OData property names and scalar types from one existing row ($top=1)."""
    base = normalize_environment_url(environment_url)
    entity_path = urllib.parse.quote(entity_name.strip(), safe="")
    url = f"{base}/data/{entity_path}?$top=1"
    status, body = odata_get_json(url, access_token=access_token, company=company)
    if status < 200 or status >= 300:
        msg = extract_fo_error_message(body) or f"HTTP {status}"
        raise RuntimeError(
            f"Could not read sample row from {entity_name} to discover property names: {msg}"
        )
    if not isinstance(body, dict):
        return EntityODataSchema(property_names=[])
    value = body.get("value")
    if not isinstance(value, list) or not value:
        entity_key = entity_name.strip().casefold()
        if entity_key == "personusers":
            return EntityODataSchema(
                property_names=["UserId", "PartyNumber", "ValidFrom", "ValidTo"],
                sample_types={
                    "UserId": str,
                    "PartyNumber": str,
                    "ValidFrom": str,
                    "ValidTo": str,
                },
            )
        if entity_key == "securityuserroleassociations":
            return EntityODataSchema(
                property_names=[
                    "UserId",
                    "SecurityRoleIdentifier",
                    "SecurityRoleName",
                    "AssignmentStatus",
                    "AssignmentMode",
                ],
                sample_types={
                    "UserId": str,
                    "SecurityRoleIdentifier": str,
                    "SecurityRoleName": str,
                    "AssignmentStatus": str,
                    "AssignmentMode": str,
                },
            )
        if entity_key == "securityuserroleorganizations":
            return EntityODataSchema(
                property_names=[
                    "UserId",
                    "SecurityRoleIdentifier",
                    "OrganizationType",
                    "OrganizationId",
                    "OperatingUnitType",
                    "HierarchyType",
                ],
                sample_types={
                    "UserId": str,
                    "SecurityRoleIdentifier": str,
                    "OrganizationType": str,
                    "OrganizationId": str,
                    "OperatingUnitType": str,
                    "HierarchyType": str,
                },
            )
        raise RuntimeError(
            f"No rows returned for {entity_name}; cannot discover OData property names. "
            "Ensure at least one record exists or set odata_property on columns in YAML."
        )
    row = value[0]
    if not isinstance(row, dict):
        return EntityODataSchema(property_names=[])

    names: list[str] = []
    sample_types: dict[str, type] = {}
    for key, sample in row.items():
        if key.startswith("@"):
            continue
        names.append(key)
        if sample is None:
            sample_types[key] = str
        else:
            sample_types[key] = type(sample)
    return EntityODataSchema(property_names=names, sample_types=sample_types)


def _parse_dmf_number(text: str) -> int | float:
    normalized = text if not text.startswith(".") else f"0{text}"
    if "." in normalized:
        value = float(normalized)
        if value.is_integer():
            return int(value)
        return value
    return int(normalized)


def coerce_odata_value(
    value: Any,
    *,
    odata_property: str,
    sample_types: dict[str, type],
) -> Any:
    """Format scalars for POST; match JSON types F&O expects (from sample row)."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value

    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
        return text.replace(" ", "T") + "Z"

    expected = sample_types.get(odata_property, str)
    if expected is bool:
        lowered = text.lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
        return text
    if expected is int:
        try:
            parsed = _parse_dmf_number(text)
            return parsed if isinstance(parsed, int) else int(parsed)
        except ValueError:
            return text
    if expected is float:
        try:
            return _parse_dmf_number(text)
        except ValueError:
            return text
    return text


def column_included_on_odata_create(
    spec: Any,
    *,
    send_all_defaults: bool,
) -> bool:
    if send_all_defaults:
        if isinstance(spec, dict) and spec.get("odata_skip_create"):
            return False
        return True
    if not isinstance(spec, dict):
        return False
    if spec.get("odata_skip_create"):
        return False
    if spec.get("source") or spec.get("odata_on_create") or spec.get("odata_runtime"):
        return True
    return False


def row_to_odata_payload(
    headers: list[str],
    values: list[Any],
    columns: dict[str, Any],
    dmf_to_odata: dict[str, str],
    sample_types: dict[str, type] | None = None,
    *,
    send_all_defaults: bool = False,
    runtime_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build JSON body using OData property names; omit empty values."""
    types = sample_types or {}
    runtime = runtime_values or {}
    payload: dict[str, Any] = {}
    for header, value in zip(headers, values, strict=True):
        spec = columns.get(header)
        if not column_included_on_odata_create(
            spec, send_all_defaults=send_all_defaults
        ):
            continue
        if isinstance(spec, dict):
            runtime_key = spec.get("odata_runtime")
            if runtime_key:
                value = runtime.get(str(runtime_key))
        if value is None or value == "":
            continue
        if isinstance(spec, dict) and spec.get("odata_property"):
            key = str(spec["odata_property"])
        else:
            key = dmf_to_odata.get(header)
            if not key:
                continue
        payload[key] = coerce_odata_value(
            value, odata_property=key, sample_types=types
        )
    return payload
