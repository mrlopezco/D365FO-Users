"""CLI entrypoint: generate F&O DMF import Excel files or OData import."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from app.cli_prompts import choose_environment, choose_mode
from app.d365.environments import get_environment_by_name, load_environments
from app.generate import run as generate_run
from app.odata_import import run as odata_run
from app.odata_import import run_connection_test


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Dynamics 365 F&O DMF import Excel files or import users "
            "via OData (Employee V2, SystemUsers, PersonUsers link, and security "
            "role assignments) from an input users workbook."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("file", "odata"),
        default=None,
        help="file: generate Excel only; odata: POST to F&O (skips mode menu)",
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
        "--output-dir",
        type=Path,
        default=ROOT / "output",
        help="Root output directory; a timestamped subfolder is created inside it",
    )
    parser.add_argument(
        "--environment",
        metavar="NAME",
        default=None,
        help="D365 environment name from config/d365_environments.yaml (OData mode)",
    )
    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="OData mode: verify token and GET /data only, then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="OData mode: build payloads and print feedback without POST",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="OData mode: stop after the first failed POST",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="OData mode: print full F&O error JSON on failed POST",
    )
    parser.add_argument(
        "--skip-person-link",
        action="store_true",
        help="OData mode: do not POST PersonUsers (user to person link)",
    )
    parser.add_argument(
        "--skip-security",
        action="store_true",
        help="OData mode: do not assign security roles or organization scope",
    )
    parser.add_argument(
        "--skip-security-orgs",
        action="store_true",
        help="OData mode: assign roles only; skip SecurityUserRoleOrganizations",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="OData mode: proceed with import without confirmation after preflight",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="OData mode: skip duplicate checks against the environment",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    mode = args.mode
    if mode is None:
        mode = choose_mode()

    input_path = args.input.resolve()
    config_dir = args.config_dir.resolve()

    if mode == "file":
        output_root = args.output_dir.resolve()
        if not input_path.exists():
            print(f"Error: input file not found: {input_path}", file=sys.stderr)
            return 1
        if not config_dir.is_dir():
            print(f"Error: config directory not found: {config_dir}", file=sys.stderr)
            return 1
        try:
            output_dir = generate_run(input_path, config_dir, output_root)
        except Exception as exc:  # noqa: BLE001 - surface clean CLI errors
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        print(f"Generated DMF import files in: {output_dir}")
        for path in sorted(output_dir.glob("*.xlsx")):
            print(f"  - {path.name}")
        print(
            "Import order in F&O: 1) Employee V2, 2) User information, "
            "3) Person users (if used), 4) Security user role association, "
            "5) SystemSecurityUserRoleOrganizationEntity (if org columns filled)."
        )
        return 0

    # OData mode
    env_config_path = config_dir / "d365_environments.yaml"
    require_secrets = not args.dry_run
    try:
        environments = load_environments(env_config_path, require_secrets=require_secrets)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    env_name = args.environment
    if env_name is None:
        try:
            env = choose_environment(environments)
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        try:
            env = get_environment_by_name(environments, env_name)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if args.test_connection:
        try:
            run_connection_test(env)
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}", file=sys.stderr)
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
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.cancelled:
        return 0
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
