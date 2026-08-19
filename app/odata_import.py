"""Push user records to D365 F&O via OData POST and link users to workers."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.d365.environments import D365Environment
from app.d365.fo_client import (
    acquire_fo_access_token,
    entity_collection_url,
    extract_fo_error_message,
    extract_party_number_from_row,
    fetch_entity_rows,
    format_fo_error_response,
    odata_escape_string_literal,
    odata_post_json,
    test_connection,
)
from app.d365.odata_mapping import (
    build_dmf_to_odata_map,
    fetch_entity_schema,
    normalize_property_key,
    row_to_odata_payload,
)
from app.excel_io import read_users
from app.cli_prompts import confirm_proceed_after_preflight
from app.entity_rows import ODATA_IMPORT_FILES, build_entity_rows, load_entity_config
from app.preflight_plan import build_preflight_plan, print_preflight_plan
from app.security_import import import_security_assignments

_USER_ENTITY_CONFIGS = frozenset(
    {
        "employee_v2.yaml",
        "user_information.yaml",
    }
)


@dataclass
class ImportResult:
    succeeded: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)


def _user_key(user: dict[str, str]) -> str:
    return user.get("UserId", "").strip()


def _user_label(user: dict[str, str]) -> str:
    for key in ("Email", "UserId", "Alias"):
        val = user.get(key, "").strip()
        if val:
            return val
    return user.get("FirstName", "user")


def _resolve_company(
    env: D365Environment,
    config: dict[str, Any],
) -> str | None:
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


def _error_text(status: int, body: Any) -> str:
    return (extract_fo_error_message(body) or f"HTTP {status}").lower()


def _is_duplicate_user_error(status: int, body: Any) -> bool:
    text = _error_text(status, body)
    return "already exists" in text or "domain and alias" in text


def _is_duplicate_link_error(status: int, body: Any) -> bool:
    text = _error_text(status, body)
    return (
        "already exists" in text
        or "duplicate" in text
        or "already linked" in text
        or "overlaps" in text
    )


def _person_link_exists(
    *,
    env: D365Environment,
    access_token: str,
    company: str | None,
    user_id: str,
) -> bool:
    if not user_id:
        return False
    escaped = odata_escape_string_literal(user_id)
    rows = fetch_entity_rows(
        env.environment_url,
        "PersonUsers",
        access_token=access_token,
        odata_filter=f"UserId eq '{escaped}'",
        top=1,
        company=company,
        cross_company=True,
    )
    return bool(rows)


def _lookup_party_number_by_email(
    *,
    env: D365Environment,
    access_token: str,
    email: str,
    company: str | None,
) -> str | None:
    if not email.strip():
        return None
    escaped = odata_escape_string_literal(email.strip())
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
        return None
    return extract_party_number_from_row(rows[0])


def _register_existing_employee_party(
    user: dict[str, str],
    party_numbers: dict[str, str],
    *,
    env: D365Environment,
    access_token: str,
    company: str | None,
) -> str | None:
    user_id = _user_key(user)
    if user_id and user_id in party_numbers:
        return party_numbers[user_id]
    email = user.get("Email", "").strip()
    if not email or not access_token:
        return None
    party = _lookup_party_number_by_email(
        env=env,
        access_token=access_token,
        email=email,
        company=company,
    )
    if party and user_id:
        party_numbers[user_id] = party
    return party


def _ensure_party_numbers(
    users: list[dict[str, str]],
    party_numbers: dict[str, str],
    *,
    env: D365Environment,
    access_token: str,
    company: str | None,
) -> None:
    for user in users:
        key = _user_key(user)
        if not key or key in party_numbers:
            continue
        email = user.get("Email", "").strip()
        party = _lookup_party_number_by_email(
            env=env,
            access_token=access_token,
            email=email,
            company=company,
        )
        if party:
            party_numbers[key] = party


def import_entity_rows(
    *,
    env: D365Environment,
    access_token: str,
    config: dict[str, Any],
    users: list[dict[str, str]],
    dry_run: bool,
    stop_on_error: bool,
    result: ImportResult,
    party_numbers: dict[str, str],
    verbose: bool = False,
) -> None:
    entity_name = str(config["odata_entity"])
    display = config.get("entity") or entity_name
    headers, rows = build_entity_rows(config, users)
    columns: dict[str, Any] = config["columns"]
    company = _resolve_company(env, config)
    url = entity_collection_url(env.environment_url, entity_name)
    total = len(users)
    entity_norm = normalize_property_key(entity_name)

    dmf_to_odata: dict[str, str] = {}
    sample_types: dict[str, type] = {}
    if access_token:
        schema = fetch_entity_schema(
            environment_url=env.environment_url,
            entity_name=entity_name,
            access_token=access_token,
            company=company,
        )
        dmf_to_odata = build_dmf_to_odata_map(headers, schema.property_names)
        sample_types = schema.sample_types

    send_all = bool(config.get("odata_send_all_defaults"))
    if company and verbose:
        print(f"  Company header: {company.upper()}")

    print(f"Importing {display} ({entity_name}) — {total} user(s)")
    if not send_all and verbose:
        print("  OData payload: sourced columns + odata_on_create only")

    for index, (user, row_values) in enumerate(zip(users, rows, strict=True), start=1):
        label = _user_label(user)
        payload = row_to_odata_payload(
            headers,
            row_values,
            columns,
            dmf_to_odata,
            sample_types,
            send_all_defaults=send_all,
        )

        if dry_run:
            if entity_norm == normalize_property_key("EmployeesV2") and access_token:
                existing_party = _register_existing_employee_party(
                    user,
                    party_numbers,
                    env=env,
                    access_token=access_token,
                    company=company,
                )
                if existing_party:
                    print(
                        f"  [{index}/{total}] {label} — employee already in F&O "
                        f"(dry-run skip, PartyNumber {existing_party})"
                    )
                    result.succeeded += 1
                    continue
            if index == 1:
                preview = json.dumps(payload, indent=2, default=str)
                print(f"  [dry-run] sample payload for {label}:")
                for line in preview.splitlines():
                    print(f"    {line}")
            print(f"  [{index}/{total}] {label} — dry-run (skipped POST)")
            result.succeeded += 1
            continue

        if entity_norm == normalize_property_key("EmployeesV2") and access_token:
            existing_party = _register_existing_employee_party(
                user,
                party_numbers,
                env=env,
                access_token=access_token,
                company=company,
            )
            if existing_party:
                print(
                    f"  [{index}/{total}] {label} — employee already in F&O "
                    f"(skipped create, using PartyNumber {existing_party})"
                )
                result.succeeded += 1
                continue

        status, body = odata_post_json(
            url,
            access_token=access_token,
            body=payload,
            company=company,
        )
        if 200 <= status < 300:
            print(f"  [{index}/{total}] {label} — created (HTTP {status})")
            result.succeeded += 1
            if entity_norm == normalize_property_key("EmployeesV2") and isinstance(
                body, dict
            ):
                party = extract_party_number_from_row(body)
                user_id = _user_key(user)
                if party and user_id:
                    party_numbers[user_id] = party
            continue

        if entity_norm == normalize_property_key("SystemUsers") and _is_duplicate_user_error(
            status, body
        ):
            print(f"  [{index}/{total}] {label} — already exists (skipped create)")
            result.succeeded += 1
            continue

        if entity_norm == normalize_property_key("EmployeesV2"):
            email = user.get("Email", "").strip()
            party = _lookup_party_number_by_email(
                env=env,
                access_token=access_token,
                email=email,
                company=company,
            )
            user_id = _user_key(user)
            if party and user_id:
                party_numbers[user_id] = party
                short = extract_fo_error_message(body) or f"HTTP {status}"
                print(
                    f"  [{index}/{total}] {label} — employee not created ({short}); "
                    f"using existing PartyNumber {party}"
                )
                result.succeeded += 1
                continue

        detail = format_fo_error_response(status, body, verbose=verbose)
        short = extract_fo_error_message(body) or f"HTTP {status}"
        err_line = f"{display} [{index}/{total}] {label}: {short}"
        result.failed += 1
        result.errors.append(err_line)
        print(f"  [{index}/{total}] {label} — failed: {short}", file=sys.stderr)
        for line in detail.splitlines():
            print(f"      {line}", file=sys.stderr)
        if stop_on_error:
            raise RuntimeError(err_line)


def import_person_user_links(
    *,
    env: D365Environment,
    access_token: str,
    config: dict[str, Any],
    users: list[dict[str, str]],
    party_numbers: dict[str, str],
    dry_run: bool,
    stop_on_error: bool,
    result: ImportResult,
    verbose: bool = False,
) -> None:
    entity_name = str(config["odata_entity"])
    display = config.get("entity") or entity_name
    headers, rows = build_entity_rows(config, users)
    columns: dict[str, Any] = config["columns"]
    company = _resolve_company(env, config)
    url = entity_collection_url(env.environment_url, entity_name)
    total = len(users)

    schema = fetch_entity_schema(
        environment_url=env.environment_url,
        entity_name=entity_name,
        access_token=access_token,
        company=company,
    )
    dmf_to_odata = build_dmf_to_odata_map(headers, schema.property_names)
    sample_types = schema.sample_types
    send_all = bool(config.get("odata_send_all_defaults"))

    print(f"Linking user to person ({entity_name}) — {total} user(s)")

    for index, (user, row_values) in enumerate(zip(users, rows, strict=True), start=1):
        label = _user_label(user)
        user_id = _user_key(user)
        party = party_numbers.get(user_id)
        if not party:
            if dry_run:
                party = "000000000"
                runtime = {"party_number": party}
                payload = row_to_odata_payload(
                    headers,
                    row_values,
                    columns,
                    dmf_to_odata,
                    sample_types,
                    send_all_defaults=send_all,
                    runtime_values=runtime,
                )
                preview = json.dumps(payload, indent=2, default=str)
                print(f"  [dry-run] sample PersonUsers payload for {label} (PartyNumber placeholder):")
                for line in preview.splitlines():
                    print(f"    {line}")
                print(f"  [{index}/{total}] {label} — dry-run (skipped POST)")
                result.succeeded += 1
                continue
            print(
                f"  [{index}/{total}] {label} — skipped link (no PartyNumber for UserId {user_id!r})",
                file=sys.stderr,
            )
            result.failed += 1
            result.errors.append(f"{display} [{index}/{total}] {label}: missing PartyNumber")
            if stop_on_error:
                raise RuntimeError(f"Missing PartyNumber for {label}")
            continue

        runtime = {"party_number": party}
        payload = row_to_odata_payload(
            headers,
            row_values,
            columns,
            dmf_to_odata,
            sample_types,
            send_all_defaults=send_all,
            runtime_values=runtime,
        )

        if dry_run:
            if index == 1:
                preview = json.dumps(payload, indent=2, default=str)
                print(f"  [dry-run] sample PersonUsers payload for {label}:")
                for line in preview.splitlines():
                    print(f"    {line}")
            print(f"  [{index}/{total}] {label} — dry-run (skipped POST)")
            result.succeeded += 1
            continue

        if access_token and _person_link_exists(
            env=env,
            access_token=access_token,
            company=company,
            user_id=user_id,
        ):
            print(
                f"  [{index}/{total}] {label} — user/person link already exists (skipped)"
            )
            result.succeeded += 1
            continue

        status, body = odata_post_json(
            url,
            access_token=access_token,
            body=payload,
            company=company,
        )
        if 200 <= status < 300:
            print(
                f"  [{index}/{total}] {label} — linked UserId {user_id} to PartyNumber {party} "
                f"(HTTP {status})"
            )
            result.succeeded += 1
            continue

        if _is_duplicate_link_error(status, body):
            print(
                f"  [{index}/{total}] {label} — user/person link already exists (skipped)"
            )
            result.succeeded += 1
            continue

        detail = format_fo_error_response(status, body, verbose=verbose)
        short = extract_fo_error_message(body) or f"HTTP {status}"
        err_line = f"{display} [{index}/{total}] {label}: {short}"
        result.failed += 1
        result.errors.append(err_line)
        print(f"  [{index}/{total}] {label} — link failed: {short}", file=sys.stderr)
        for line in detail.splitlines():
            print(f"      {line}", file=sys.stderr)
        if stop_on_error:
            raise RuntimeError(err_line)


def run(
    *,
    input_path: Path,
    config_dir: Path,
    env: D365Environment,
    dry_run: bool = False,
    stop_on_error: bool = False,
    skip_connection_test: bool = False,
    verbose: bool = False,
    link_users: bool = True,
    assume_yes: bool = False,
    skip_preflight: bool = False,
    import_security: bool = True,
    assign_security_orgs: bool = True,
) -> ImportResult:
    users = read_users(input_path)
    result = ImportResult()
    party_numbers: dict[str, str] = {}

    print(f"Environment: {env.name} ({env.environment_url})")

    access_token = ""
    if dry_run:
        print("Dry-run mode: no OData POST requests will be sent.")

    if env.client_secret or not dry_run:
        label = "Connecting (dry-run, for property discovery)..." if dry_run else "Connecting..."
        print(label, end=" ", flush=True)
        access_token = acquire_fo_access_token(
            tenant_id=env.tenant_id,
            client_id=env.client_id,
            client_secret=env.client_secret,
            environment_url=env.environment_url,
        )
        if not dry_run and not skip_connection_test:
            test_connection(env.environment_url, access_token)
        print("OK")
    elif dry_run:
        print(
            "Warning: no client_secret — OData JSON keys cannot be mapped to PascalCase.",
            file=sys.stderr,
        )

    if access_token and not skip_preflight:
        print("Checking environment and building import plan...", flush=True)
        employee_cfg = load_entity_config(
            config_dir / "employee_v2.yaml", require_odata=True
        )
        company = _resolve_company(env, employee_cfg)
        try:
            plan = build_preflight_plan(
                users,
                env=env,
                access_token=access_token,
                company=company,
                config_dir=config_dir,
                link_users=link_users,
                import_security=import_security,
                assign_security_orgs=assign_security_orgs,
            )
        except ValueError as exc:
            print(f"Preflight failed: {exc}", file=sys.stderr)
            result.cancelled = True
            return result
        print_preflight_plan(plan)
        plan.apply_existing_party_numbers(party_numbers)
        if not assume_yes:
            if not confirm_proceed_after_preflight(
                create_count=plan.create_count,
                skip_count=plan.skip_count,
            ):
                print("Import cancelled.")
                result.cancelled = True
                return result

    person_config_path = config_dir / "person_users.yaml"
    entity_files = [
        f
        for f in ODATA_IMPORT_FILES
        if f in _USER_ENTITY_CONFIGS
        or (f == "person_users.yaml" and link_users and person_config_path.exists())
    ]

    for filename in entity_files:
        if filename == "person_users.yaml":
            continue
        cfg_path = config_dir / filename
        if not cfg_path.exists():
            raise FileNotFoundError(f"Missing config file: {cfg_path}")
        config = load_entity_config(cfg_path, require_odata=True)
        import_entity_rows(
            env=env,
            access_token=access_token,
            config=config,
            users=users,
            dry_run=dry_run,
            stop_on_error=stop_on_error,
            result=result,
            party_numbers=party_numbers,
            verbose=verbose,
        )

    if link_users and person_config_path.exists():
        if access_token and not dry_run:
            company = _resolve_company(
                env,
                load_entity_config(config_dir / "employee_v2.yaml", require_odata=True),
            )
            _ensure_party_numbers(
                users,
                party_numbers,
                env=env,
                access_token=access_token,
                company=company,
            )
        if dry_run and not party_numbers:
            print(
                "  [dry-run] PersonUsers link would run after employee PartyNumber is resolved."
            )
        config = load_entity_config(person_config_path, require_odata=True)
        import_person_user_links(
            env=env,
            access_token=access_token,
            config=config,
            users=users,
            party_numbers=party_numbers,
            dry_run=dry_run,
            stop_on_error=stop_on_error,
            result=result,
            verbose=verbose,
        )
    elif link_users:
        print(
            "Warning: config/person_users.yaml not found; skipping user–person link.",
            file=sys.stderr,
        )

    if import_security:
        result.succeeded, result.failed = import_security_assignments(
            env=env,
            access_token=access_token,
            config_dir=config_dir,
            users=users,
            dry_run=dry_run,
            stop_on_error=stop_on_error,
            verbose=verbose,
            assign_orgs=assign_security_orgs,
            result_succeeded=result.succeeded,
            result_failed=result.failed,
            result_errors=result.errors,
        )

    print(f"Summary: {result.succeeded} succeeded, {result.failed} failed")
    return result


def run_connection_test(env: D365Environment) -> None:
    print(f"Environment: {env.name} ({env.environment_url})")
    print("Connecting...", end=" ", flush=True)
    token = acquire_fo_access_token(
        tenant_id=env.tenant_id,
        client_id=env.client_id,
        client_secret=env.client_secret,
        environment_url=env.environment_url,
    )
    test_connection(env.environment_url, token)
    print("OK")
    print("Connection test passed.")
