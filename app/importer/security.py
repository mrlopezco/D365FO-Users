"""Security role and organization assignment via OData."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

from app.config.entity import load_entity_config, resolve_row
from app.d365.environments import D365Environment
from app.d365.fo_client import (
    entity_collection_url,
    extract_fo_error_message,
    fetch_entity_rows,
    format_fo_error_response,
    odata_escape_string_literal,
    odata_post_json,
)
from app.d365.odata_mapping import (
    build_dmf_to_odata_map,
    fetch_entity_schema,
    normalize_property_key,
    row_to_odata_payload,
)
from app.security import (
    OrgAssignmentRow,
    RoleAssignmentRow,
    assignment_row_to_user_dict,
    collect_role_names_from_users,
    expand_org_assignments,
    expand_role_assignments,
)
from app.util.helpers import resolve_company

logger = logging.getLogger(__name__)


@dataclass
class SecurityRoleCatalog:
    """Maps role display name (case-insensitive) to SecurityRoleIdentifier."""

    by_normalized_name: dict[str, tuple[str, str]] = field(default_factory=dict)

    def resolve(self, role_name: str) -> tuple[str, str]:
        key = normalize_property_key(role_name)
        if key not in self.by_normalized_name:
            raise ValueError(f"Unknown security role name: {role_name!r}")
        return self.by_normalized_name[key]

    @classmethod
    def fetch(
        cls,
        *,
        environment_url: str,
        access_token: str,
        company: str | None,
        required_names: list[str],
    ) -> SecurityRoleCatalog:
        rows = fetch_entity_rows(
            environment_url,
            "SecurityRoles",
            access_token=access_token,
            top=5000,
            company=company,
            cross_company=True,
        )
        catalog = cls()
        for row in rows:
            name = row.get("SecurityRoleName")
            ident = row.get("SecurityRoleIdentifier")
            if name is None or ident is None:
                continue
            name_str = str(name).strip()
            ident_str = str(ident).strip()
            if not name_str or not ident_str:
                continue
            catalog.by_normalized_name[normalize_property_key(name_str)] = (
                name_str,
                ident_str,
            )

        missing = [
            name
            for name in required_names
            if normalize_property_key(name) not in catalog.by_normalized_name
        ]
        if missing:
            raise ValueError(
                "Unknown security role name(s): "
                + ", ".join(repr(n) for n in missing)
            )
        return catalog


@dataclass
class ExistingSecurityAssignments:
    role_keys: set[tuple[str, str]] = field(default_factory=set)
    org_keys: set[tuple[str, str, str, str]] = field(default_factory=set)

    @classmethod
    def load(
        cls,
        *,
        environment_url: str,
        access_token: str,
        company: str | None,
        user_ids: list[str],
    ) -> ExistingSecurityAssignments:
        out = cls()
        for user_id in user_ids:
            if not user_id:
                continue
            escaped = odata_escape_string_literal(user_id)
            role_rows = fetch_entity_rows(
                environment_url,
                "SecurityUserRoleAssociations",
                access_token=access_token,
                odata_filter=f"UserId eq '{escaped}'",
                top=500,
                company=company,
                cross_company=True,
            )
            for row in role_rows:
                ident = row.get("SecurityRoleIdentifier")
                if ident:
                    out.role_keys.add((user_id, str(ident).strip()))

            org_rows = fetch_entity_rows(
                environment_url,
                "SecurityUserRoleOrganizations",
                access_token=access_token,
                odata_filter=f"UserId eq '{escaped}'",
                top=500,
                company=company,
                cross_company=True,
            )
            for row in org_rows:
                ident = row.get("SecurityRoleIdentifier")
                org_id = row.get("OrganizationId")
                hierarchy = row.get("HierarchyType")
                if ident and org_id is not None and hierarchy is not None:
                    out.org_keys.add(
                        (
                            user_id,
                            str(ident).strip(),
                            str(org_id).strip(),
                            str(hierarchy).strip(),
                        )
                    )
        return out


def _is_duplicate_security_error(status: int, body: Any) -> bool:
    text = (extract_fo_error_message(body) or f"HTTP {status}").lower()
    return "already exists" in text or "duplicate" in text


def _import_assignment_batch(
    *,
    env: D365Environment,
    access_token: str,
    config: dict[str, Any],
    assignments: list[RoleAssignmentRow | OrgAssignmentRow],
    catalog: SecurityRoleCatalog,
    existing: ExistingSecurityAssignments | None,
    dry_run: bool,
    stop_on_error: bool,
    verbose: bool,
    result_succeeded: list[int],
    result_failed: list[int],
    result_errors: list[str],
    skip_existing: bool = True,
) -> None:
    if not assignments:
        return

    entity_name = str(config["odata_entity"])
    display = config.get("entity") or entity_name
    columns: dict[str, Any] = config["columns"]
    headers = list(columns.keys())
    company = resolve_company(env, config)
    url = entity_collection_url(env.environment_url, entity_name)
    total = len(assignments)

    schema = fetch_entity_schema(
        environment_url=env.environment_url,
        entity_name=entity_name,
        access_token=access_token,
        company=company,
    )
    dmf_to_odata = build_dmf_to_odata_map(headers, schema.property_names)
    sample_types = schema.sample_types
    send_all = bool(config.get("odata_send_all_defaults"))

    is_org = entity_name == "SecurityUserRoleOrganizations"
    print(f"Importing {display} ({entity_name}) — {total} assignment row(s)")

    for index, assignment in enumerate(assignments, start=1):
        canonical_name, role_identifier = catalog.resolve(assignment.role_name)
        user_id = assignment.user_id
        label = assignment.user_label

        if is_org:
            assert isinstance(assignment, OrgAssignmentRow)
            org_key = (
                user_id,
                role_identifier,
                assignment.organization_id,
                assignment.hierarchy_type,
            )
            if skip_existing and existing and org_key in existing.org_keys:
                print(
                    f"  [{index}/{total}] {label} — org scope for "
                    f"{assignment.role_name!r} already exists (skipped)"
                )
                result_succeeded[0] += 1
                continue
        else:
            role_key = (user_id, role_identifier)
            if skip_existing and existing and role_key in existing.role_keys:
                print(
                    f"  [{index}/{total}] {label} — role "
                    f"{assignment.role_name!r} already assigned (skipped)"
                )
                result_succeeded[0] += 1
                continue

        row_dict = assignment_row_to_user_dict(assignment)
        row_dict["SecurityRoleName"] = canonical_name
        row_values = resolve_row(columns, row_dict)
        runtime = {"security_role_identifier": role_identifier}
        payload = row_to_odata_payload(
            headers,
            row_values,
            columns,
            dmf_to_odata,
            sample_types,
            send_all_defaults=send_all,
            runtime_values=runtime,
        )

        detail_role = assignment.role_name
        if is_org and isinstance(assignment, OrgAssignmentRow):
            detail_role = (
                f"{assignment.role_name} @ {assignment.organization_id} "
                f"({assignment.hierarchy_type})"
            )

        if dry_run:
            if index == 1:
                preview = json.dumps(payload, indent=2, default=str)
                print(f"  [dry-run] sample payload for {label} / {detail_role}:")
                for line in preview.splitlines():
                    print(f"    {line}")
            print(f"  [{index}/{total}] {label} — {detail_role} — dry-run (skipped POST)")
            result_succeeded[0] += 1
            continue

        status, body = odata_post_json(
            url,
            access_token=access_token,
            body=payload,
            company=company,
        )
        if 200 <= status < 300:
            print(
                f"  [{index}/{total}] {label} — assigned {detail_role} "
                f"(HTTP {status})"
            )
            result_succeeded[0] += 1
            if existing:
                if is_org and isinstance(assignment, OrgAssignmentRow):
                    existing.org_keys.add(org_key)
                else:
                    existing.role_keys.add((user_id, role_identifier))
            continue

        if _is_duplicate_security_error(status, body):
            print(
                f"  [{index}/{total}] {label} — {detail_role} already exists (skipped)"
            )
            result_succeeded[0] += 1
            continue

        detail = format_fo_error_response(status, body, verbose=verbose)
        short = extract_fo_error_message(body) or f"HTTP {status}"
        err_line = f"{display} [{index}/{total}] {label}: {short}"
        result_failed[0] += 1
        result_errors.append(err_line)
        print(f"  [{index}/{total}] {label} — failed: {short}", file=sys.stderr)
        for line in detail.splitlines():
            print(f"      {line}", file=sys.stderr)
        if stop_on_error:
            raise RuntimeError(err_line)


def import_security_assignments(
    *,
    env: D365Environment,
    access_token: str,
    config_dir: Any,
    users: list[dict[str, str]],
    dry_run: bool,
    stop_on_error: bool,
    verbose: bool,
    assign_orgs: bool,
    result_succeeded: int,
    result_failed: int,
    result_errors: list[str],
) -> tuple[int, int]:
    role_names = collect_role_names_from_users(users)
    if not role_names:
        return result_succeeded, result_failed

    role_assignments = expand_role_assignments(users)
    org_assignments = expand_org_assignments(users) if assign_orgs else []

    employee_cfg = load_entity_config(config_dir / "employee_v2.yaml", require_odata=True)
    company = resolve_company(env, employee_cfg)

    catalog = SecurityRoleCatalog.fetch(
        environment_url=env.environment_url,
        access_token=access_token,
        company=company,
        required_names=role_names,
    )

    user_ids = sorted({u.get("UserId", "").strip() for u in users if u.get("UserId")})
    existing: ExistingSecurityAssignments | None = None
    if access_token:
        existing = ExistingSecurityAssignments.load(
            environment_url=env.environment_url,
            access_token=access_token,
            company=company,
            user_ids=user_ids,
        )

    succeeded = [result_succeeded]
    failed = [result_failed]

    role_cfg = load_entity_config(
        config_dir / "security_user_role_association.yaml", require_odata=True
    )
    _import_assignment_batch(
        env=env,
        access_token=access_token,
        config=role_cfg,
        assignments=role_assignments,
        catalog=catalog,
        existing=existing,
        dry_run=dry_run,
        stop_on_error=stop_on_error,
        verbose=verbose,
        result_succeeded=succeeded,
        result_failed=failed,
        result_errors=result_errors,
    )

    if assign_orgs and org_assignments:
        org_cfg = load_entity_config(
            config_dir / "security_user_role_organization.yaml", require_odata=True
        )
        _import_assignment_batch(
            env=env,
            access_token=access_token,
            config=org_cfg,
            assignments=org_assignments,
            catalog=catalog,
            existing=existing,
            dry_run=dry_run,
            stop_on_error=stop_on_error,
            verbose=verbose,
            result_succeeded=succeeded,
            result_failed=failed,
            result_errors=result_errors,
        )

    return succeeded[0], failed[0]
