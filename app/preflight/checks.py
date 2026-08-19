"""Pre-import OData checks: detect existing users and employees before POST."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.d365.environments import D365Environment
from app.d365.fo_client import (
    extract_party_number_from_row,
    fetch_entity_rows,
    odata_escape_string_literal,
)
from app.d365.odata_mapping import normalize_property_key


def _row_field(row: dict, *logical_names: str) -> str | None:
    targets = {normalize_property_key(n) for n in logical_names}
    for key, val in row.items():
        if key.startswith("@"):
            continue
        if normalize_property_key(key) in targets and val not in (None, ""):
            return str(val).strip()
    return None


@dataclass
class UserPreflightRow:
    label: str
    user_id: str
    email: str
    alias: str
    system_user_matches: list[str] = field(default_factory=list)
    employee_matches: list[str] = field(default_factory=list)
    existing_party_number: str | None = None
    person_link_note: str | None = None

    @property
    def has_system_user(self) -> bool:
        return bool(self.system_user_matches)

    @property
    def has_employee(self) -> bool:
        return bool(self.employee_matches)

    @property
    def has_any_issue(self) -> bool:
        return self.has_system_user or self.has_employee or bool(self.person_link_note)


@dataclass
class PreflightReport:
    rows: list[UserPreflightRow] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return any(r.has_any_issue for r in self.rows)

    @property
    def employee_duplicate_count(self) -> int:
        return sum(1 for r in self.rows if r.has_employee)

    @property
    def system_user_duplicate_count(self) -> int:
        return sum(1 for r in self.rows if r.has_system_user)

    def apply_existing_party_numbers(self, party_numbers: dict[str, str]) -> None:
        for row in self.rows:
            if row.user_id and row.existing_party_number:
                party_numbers[row.user_id] = row.existing_party_number


def _find_system_user(
    *,
    env: D365Environment,
    access_token: str,
    company: str | None,
    user_id: str,
    email: str,
    alias: str,
) -> tuple[list[str], dict | None]:
    """Return human-readable match reasons and first matching row if any."""
    reasons: list[str] = []
    matched_row: dict | None = None

    checks: list[tuple[str, str, str]] = []
    if user_id:
        checks.append(("UserID", user_id, f"UserID {user_id!r}"))
    if email:
        checks.append(("Email", email, f"Email {email!r}"))
    if alias:
        checks.append(("Alias", alias, f"Alias {alias!r}"))

    seen: set[str] = set()
    for field_name, value, reason in checks:
        if reason in seen:
            continue
        escaped = odata_escape_string_literal(value)
        odata_filter = f"{field_name} eq '{escaped}'"
        rows = fetch_entity_rows(
            env.environment_url,
            "SystemUsers",
            access_token=access_token,
            odata_filter=odata_filter,
            top=1,
            company=company,
            cross_company=True,
        )
        if rows:
            seen.add(reason)
            reasons.append(reason)
            if matched_row is None:
                matched_row = rows[0]

    return reasons, matched_row


def _find_employee_by_email(
    *,
    env: D365Environment,
    access_token: str,
    company: str | None,
    email: str,
) -> tuple[list[str], dict | None]:
    if not email:
        return [], None
    escaped = odata_escape_string_literal(email)
    odata_filter = f"PrimaryContactEmail eq '{escaped}'"
    rows = fetch_entity_rows(
        env.environment_url,
        "EmployeesV2",
        access_token=access_token,
        odata_filter=odata_filter,
        top=1,
        company=company,
        cross_company=True,
    )
    if not rows:
        return [], None
    row = rows[0]
    personnel = _row_field(row, "PersonnelNumber") or "?"
    party = extract_party_number_from_row(row) or "?"
    name = _row_field(row, "Name") or "?"
    return [
        f"PrimaryContactEmail {email!r} → PersonnelNumber {personnel}, "
        f"PartyNumber {party}, Name {name!r}"
    ], row


def _find_person_link(
    *,
    env: D365Environment,
    access_token: str,
    company: str | None,
    user_id: str,
) -> str | None:
    if not user_id:
        return None
    escaped = odata_escape_string_literal(user_id)
    odata_filter = f"UserId eq '{escaped}'"
    rows = fetch_entity_rows(
        env.environment_url,
        "PersonUsers",
        access_token=access_token,
        odata_filter=odata_filter,
        top=1,
        company=company,
        cross_company=True,
    )
    if not rows:
        return None
    party = _row_field(rows[0], "PartyNumber") or "?"
    return f"PersonUsers link already exists for UserID {user_id!r} (PartyNumber {party})"


def run_preflight(
    users: list[dict[str, str]],
    *,
    env: D365Environment,
    access_token: str,
    company: str | None,
    check_person_links: bool = True,
) -> PreflightReport:
    report = PreflightReport()
    for user in users:
        user_id = user.get("UserId", "").strip()
        email = user.get("Email", "").strip()
        alias = user.get("Alias", "").strip()
        label = email or user_id or alias or user.get("FirstName", "user")

        sys_reasons, _ = _find_system_user(
            env=env,
            access_token=access_token,
            company=company,
            user_id=user_id,
            email=email,
            alias=alias,
        )
        emp_reasons, emp_row = _find_employee_by_email(
            env=env,
            access_token=access_token,
            company=company,
            email=email,
        )
        existing_party = None
        if emp_row:
            existing_party = extract_party_number_from_row(emp_row)
        link_note = None
        if check_person_links and user_id:
            link_note = _find_person_link(
                env=env,
                access_token=access_token,
                company=company,
                user_id=user_id,
            )

        report.rows.append(
            UserPreflightRow(
                label=label,
                user_id=user_id,
                email=email,
                alias=alias,
                system_user_matches=sys_reasons,
                employee_matches=emp_reasons,
                existing_party_number=existing_party,
                person_link_note=link_note,
            )
        )
    return report
