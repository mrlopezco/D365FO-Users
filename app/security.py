"""Parse security columns from input users and expand role/org assignment rows."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any

from app.d365.odata_mapping import normalize_property_key

OPTIONAL_SECURITY_COLUMNS = (
    "SecurityRoles",
    "SecurityLegalEntityIds",
    "SecurityLegalEntities",
)


def parse_csv_list(value: str | None) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _user_label(user: dict[str, str]) -> str:
    for key in ("Email", "UserId", "Alias"):
        val = user.get(key, "").strip()
        if val:
            return val
    return user.get("FirstName", "user")


def validate_user_security_columns(user: dict[str, str], *, row_num: int | None = None) -> None:
    """Require org columns to be both set or both empty."""
    le = user.get("SecurityLegalEntityIds", "").strip()
    hier = user.get("SecurityLegalEntities", "").strip()
    if bool(le) != bool(hier):
        prefix = f"Row {row_num}: " if row_num else ""
        raise ValueError(
            f"{prefix}{_user_label(user)!r}: SecurityLegalEntityIds and "
            "SecurityLegalEntities must both be filled or both be empty."
        )


def validate_users_security(users: list[dict[str, str]]) -> None:
    for index, user in enumerate(users, start=2):
        validate_user_security_columns(user, row_num=index)


@dataclass(frozen=True)
class RoleAssignmentRow:
    user_id: str
    user_label: str
    role_name: str


@dataclass(frozen=True)
class OrgAssignmentRow:
    user_id: str
    user_label: str
    role_name: str
    organization_id: str
    hierarchy_type: str


def expand_role_assignments(users: list[dict[str, str]]) -> list[RoleAssignmentRow]:
    out: list[RoleAssignmentRow] = []
    for user in users:
        user_id = user.get("UserId", "").strip()
        if not user_id:
            continue
        label = _user_label(user)
        for role_name in parse_csv_list(user.get("SecurityRoles")):
            out.append(
                RoleAssignmentRow(
                    user_id=user_id,
                    user_label=label,
                    role_name=role_name,
                )
            )
    return out


def expand_org_assignments(users: list[dict[str, str]]) -> list[OrgAssignmentRow]:
    out: list[OrgAssignmentRow] = []
    for user in users:
        user_id = user.get("UserId", "").strip()
        if not user_id:
            continue
        label = _user_label(user)
        roles = parse_csv_list(user.get("SecurityRoles"))
        legal_entities = parse_csv_list(user.get("SecurityLegalEntityIds"))
        hierarchies = parse_csv_list(user.get("SecurityLegalEntities"))
        if not roles or not legal_entities or not hierarchies:
            continue
        for role_name, organization_id, hierarchy_type in product(
            roles, legal_entities, hierarchies
        ):
            out.append(
                OrgAssignmentRow(
                    user_id=user_id,
                    user_label=label,
                    role_name=role_name,
                    organization_id=organization_id,
                    hierarchy_type=hierarchy_type,
                )
            )
    return out


def assignment_row_to_user_dict(row: RoleAssignmentRow | OrgAssignmentRow) -> dict[str, str]:
    """Map assignment row to a dict compatible with YAML column `source` keys."""
    data: dict[str, str] = {
        "UserId": row.user_id,
        "SecurityRoleName": row.role_name,
    }
    if isinstance(row, OrgAssignmentRow):
        data["SecurityOrganizationId"] = row.organization_id
        data["SecurityHierarchyType"] = row.hierarchy_type
    return data


def collect_role_names_from_users(users: list[dict[str, str]]) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for user in users:
        for name in parse_csv_list(user.get("SecurityRoles")):
            key = normalize_property_key(name)
            if key not in seen:
                seen.add(key)
                names.append(name)
    return names
