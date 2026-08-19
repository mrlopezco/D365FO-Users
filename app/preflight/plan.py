"""Build and print OData import preflight: planned creates vs skips."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.d365.environments import D365Environment
from app.importer.security import ExistingSecurityAssignments, SecurityRoleCatalog
from app.preflight.checks import (
    PreflightReport,
    UserPreflightRow,
    run_preflight,
)
from app.security import (
    collect_role_names_from_users,
    expand_org_assignments,
    expand_role_assignments,
)

ActionKind = Literal["create", "skip", "n/a"]


@dataclass
class UserOnboardingPlanRow:
    user_id: str
    label: str
    employee: ActionKind
    system_user: ActionKind
    person_link: ActionKind


@dataclass
class SecurityPlanRow:
    user_id: str
    label: str
    kind: Literal["role", "org"]
    detail: str
    action: ActionKind


@dataclass
class PreflightPlan:
    onboarding: list[UserOnboardingPlanRow] = field(default_factory=list)
    security: list[SecurityPlanRow] = field(default_factory=list)
    legacy: PreflightReport = field(default_factory=PreflightReport)

    @property
    def create_count(self) -> int:
        n = 0
        for row in self.onboarding:
            n += sum(1 for a in (row.employee, row.system_user, row.person_link) if a == "create")
        n += sum(1 for row in self.security if row.action == "create")
        return n

    @property
    def skip_count(self) -> int:
        n = 0
        for row in self.onboarding:
            n += sum(1 for a in (row.employee, row.system_user, row.person_link) if a == "skip")
        n += sum(1 for row in self.security if row.action == "skip")
        return n

    def apply_existing_party_numbers(self, party_numbers: dict[str, str]) -> None:
        self.legacy.apply_existing_party_numbers(party_numbers)


def _onboarding_from_preflight_row(
    row: UserPreflightRow,
    *,
    link_users: bool,
) -> UserOnboardingPlanRow:
    employee: ActionKind = "skip" if row.has_employee else "create"
    system_user: ActionKind = "skip" if row.has_system_user else "create"
    if not link_users:
        person_link: ActionKind = "n/a"
    elif row.person_link_note:
        person_link = "skip"
    else:
        person_link = "create"
    return UserOnboardingPlanRow(
        user_id=row.user_id,
        label=row.label,
        employee=employee,
        system_user=system_user,
        person_link=person_link,
    )


def build_preflight_plan(
    users: list[dict[str, str]],
    *,
    env: D365Environment,
    access_token: str,
    company: str | None,
    config_dir: Path,
    link_users: bool = True,
    import_security: bool = True,
    assign_security_orgs: bool = True,
) -> PreflightPlan:
    legacy = run_preflight(
        users,
        env=env,
        access_token=access_token,
        company=company,
        check_person_links=link_users,
    )

    plan = PreflightPlan(legacy=legacy)
    for row in legacy.rows:
        plan.onboarding.append(
            _onboarding_from_preflight_row(row, link_users=link_users)
        )

    if not import_security:
        return plan

    role_names = collect_role_names_from_users(users)
    if not role_names:
        return plan

    catalog = SecurityRoleCatalog.fetch(
        environment_url=env.environment_url,
        access_token=access_token,
        company=company,
        required_names=role_names,
    )

    user_ids = sorted({u.get("UserId", "").strip() for u in users if u.get("UserId")})
    existing = ExistingSecurityAssignments.load(
        environment_url=env.environment_url,
        access_token=access_token,
        company=company,
        user_ids=user_ids,
    )

    for assignment in expand_role_assignments(users):
        canonical_name, role_identifier = catalog.resolve(assignment.role_name)
        role_key = (assignment.user_id, role_identifier)
        action: ActionKind = "skip" if role_key in existing.role_keys else "create"
        plan.security.append(
            SecurityPlanRow(
                user_id=assignment.user_id,
                label=assignment.user_label,
                kind="role",
                detail=canonical_name,
                action=action,
            )
        )

    if assign_security_orgs:
        for assignment in expand_org_assignments(users):
            _, role_identifier = catalog.resolve(assignment.role_name)
            org_key = (
                assignment.user_id,
                role_identifier,
                assignment.organization_id,
                assignment.hierarchy_type,
            )
            action = "skip" if org_key in existing.org_keys else "create"
            plan.security.append(
                SecurityPlanRow(
                    user_id=assignment.user_id,
                    label=assignment.user_label,
                    kind="org",
                    detail=(
                        f"{assignment.role_name} @ {assignment.organization_id} "
                        f"({assignment.hierarchy_type})"
                    ),
                    action=action,
                )
            )

    return plan


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1] + "…"


def _print_table(headers: list[str], rows: list[list[str]], *, col_max: list[int]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = min(max(widths[i], len(cell)), col_max[i])

    def fmt_cells(cells: list[str]) -> str:
        parts: list[str] = []
        for i, cell in enumerate(cells):
            shown = _truncate(cell, widths[i])
            parts.append(shown.ljust(widths[i]))
        return "  ".join(parts)

    print(fmt_cells(headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt_cells(row))


def _action_label(action: ActionKind) -> str:
    if action == "create":
        return "CREATE"
    if action == "skip":
        return "skip"
    return "—"


def print_preflight_plan(plan: PreflightPlan) -> None:
    total_users = len(plan.onboarding)
    print()
    print("Preflight — planned OData changes (GET existing data in environment)")
    print(f"  Input users: {total_users}")

    ob_create = sum(
        1
        for r in plan.onboarding
        for a in (r.employee, r.system_user, r.person_link)
        if a == "create"
    )
    ob_skip = sum(
        1
        for r in plan.onboarding
        for a in (r.employee, r.system_user, r.person_link)
        if a == "skip"
    )
    sec_create = sum(1 for r in plan.security if r.action == "create")
    sec_skip = sum(1 for r in plan.security if r.action == "skip")

    print(
        f"  Onboarding: {ob_create} step(s) to create, {ob_skip} to skip (already present)"
    )
    if plan.security or sec_create or sec_skip:
        print(
            f"  Security:   {sec_create} assignment(s) to create, "
            f"{sec_skip} to skip (already assigned)"
        )
    print(
        f"  Total:      {plan.create_count} create, {plan.skip_count} skip"
    )
    print()

    if plan.onboarding:
        print("User onboarding")
        ob_rows = [
            [
                r.user_id or r.label,
                _action_label(r.employee),
                _action_label(r.system_user),
                _action_label(r.person_link),
            ]
            for r in plan.onboarding
        ]
        _print_table(
            ["UserId", "Employee", "System user", "Person link"],
            ob_rows,
            col_max=[24, 10, 12, 12],
        )
        print()

    if plan.security:
        print("Security assignments")
        sec_rows = [
            [
                r.user_id or r.label,
                r.kind,
                r.detail,
                _action_label(r.action),
            ]
            for r in plan.security
        ]
        _print_table(
            ["UserId", "Type", "Role / organization scope", "Action"],
            sec_rows,
            col_max=[20, 6, 48, 10],
        )
        print()
