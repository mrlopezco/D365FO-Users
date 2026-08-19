"""CLI entrypoint: import F&O users, workers, links, and security roles via OData."""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from app.cli.prompts import choose_environment
from app.d365.environments import get_environment_by_name, load_environments
from app.importer.orchestrator import run as odata_run
from app.importer.orchestrator import run_connection_test
from app.logging_setup import configure_logging, debug_tracebacks_enabled


def _report_error(message: str, exc: BaseException | None = None) -> None:
    print(f"Error: {message}", file=sys.stderr)
    if exc is not None and debug_tracebacks_enabled():
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import Dynamics 365 F&O users via OData: create employees (EmployeesV2), "
            "system users (SystemUsers), link user to worker (PersonUsers), and assign "
            "security roles from an input users workbook."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "input" / "users.xlsx",
        help="Path to the input users Excel file (default: input/users.xlsx)",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=ROOT / "config",
        help="Directory containing entity YAML configs (default: config)",
    )
    parser.add_argument(
        "--environment",
        metavar="NAME",
        default=None,
        help="D365 environment name from config/d365_environments.yaml",
    )
    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Verify token and GET /data only, then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build payloads and print feedback without POST",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after the first failed POST",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full F&O error JSON on failed POST",
    )
    parser.add_argument(
        "--skip-person-link",
        action="store_true",
        help="Do not POST PersonUsers (user to person link)",
    )
    parser.add_argument(
        "--skip-security",
        action="store_true",
        help="Do not assign security roles or organization scope",
    )
    parser.add_argument(
        "--skip-security-orgs",
        action="store_true",
        help="Assign roles only; skip SecurityUserRoleOrganizations",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Proceed with import without confirmation after preflight",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip duplicate checks against the environment",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Append log output to this file (stdout logging remains)",
    )
    return parser.parse_args(argv)


def _setup_log_file(path: Path) -> None:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()
    if args.log_file is not None:
        _setup_log_file(args.log_file.resolve())

    input_path = args.input.resolve()
    config_dir = args.config_dir.resolve()

    env_config_path = config_dir / "d365_environments.yaml"
    require_secrets = not args.dry_run
    try:
        environments = load_environments(env_config_path, require_secrets=require_secrets)
    except (FileNotFoundError, ValueError) as exc:
        _report_error(str(exc), exc)
        return 1

    env_name = args.environment
    if env_name is None:
        try:
            env = choose_environment(environments)
        except (ValueError, EOFError, KeyboardInterrupt) as exc:
            _report_error(str(exc), exc)
            return 1
    else:
        try:
            env = get_environment_by_name(environments, env_name)
        except ValueError as exc:
            _report_error(str(exc), exc)
            return 1

    if args.test_connection:
        try:
            run_connection_test(env)
        except RuntimeError as exc:
            _report_error(str(exc), exc)
            return 1
        return 0

    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1
    if not config_dir.is_dir():
        print(f"Error: config directory not found: {config_dir}", file=sys.stderr)
        return 1

    try:
        result = odata_run(
            input_path=input_path,
            config_dir=config_dir,
            env=env,
            dry_run=args.dry_run,
            stop_on_error=args.stop_on_error,
            verbose=args.verbose,
            link_users=not args.skip_person_link,
            assume_yes=args.yes,
            skip_preflight=args.skip_preflight,
            import_security=not args.skip_security,
            assign_security_orgs=not args.skip_security_orgs,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _report_error(str(exc), exc)
        return 1

    if result.cancelled:
        return 0
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
