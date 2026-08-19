"""OData import orchestration: auth, preflight, entity and security phases."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.cli.prompts import confirm_proceed_after_preflight
from app.config.entity import ODATA_IMPORT_FILES, load_entity_config
from app.d365.environments import D365Environment
from app.d365.fo_client import acquire_fo_access_token, test_connection
from app.importer.entities import (
    _USER_ENTITY_CONFIGS,
    _ensure_party_numbers,
    import_entity_rows,
    import_person_user_links,
)
from app.importer.security import import_security_assignments
from app.importer.types import ImportResult
from app.io.excel import read_users
from app.preflight.plan import build_preflight_plan, print_preflight_plan
from app.util.helpers import resolve_company

logger = logging.getLogger(__name__)


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

    logger.info("Environment: %s (%s)", env.name, env.environment_url)

    access_token = ""
    if dry_run:
        logger.info("Dry-run mode: no OData POST requests will be sent.")

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
        company = resolve_company(env, employee_cfg)
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
                logger.info("Import cancelled.")
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
            company = resolve_company(
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
            logger.info(
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

    logger.info("Summary: %s succeeded, %s failed", result.succeeded, result.failed)
    return result


def run_connection_test(env: D365Environment) -> None:
    logger.info("Environment: %s (%s)", env.name, env.environment_url)
    print("Connecting...", end=" ", flush=True)
    token = acquire_fo_access_token(
        tenant_id=env.tenant_id,
        client_id=env.client_id,
        client_secret=env.client_secret,
        environment_url=env.environment_url,
    )
    test_connection(env.environment_url, token)
    print("OK")
    logger.info("Connection test passed.")
