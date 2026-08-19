"""Interactive terminal prompts for CLI mode and environment selection."""

from __future__ import annotations

import sys

from app.d365.environments import D365Environment


def choose_mode() -> str:
    """Return 'file' or 'odata'."""
    print()
    print("Select mode:")
    print("  1) Generate DMF Excel files (local output)")
    print("  2) Import into Dynamics 365 F&O via OData")
    while True:
        choice = input("Enter choice [1-2]: ").strip()
        if choice in ("1", "file"):
            return "file"
        if choice in ("2", "odata"):
            return "odata"
        print("Invalid choice. Enter 1 or 2.", file=sys.stderr)


def choose_environment(environments: list[D365Environment]) -> D365Environment:
    if not environments:
        raise ValueError("No environments configured")

    print()
    print("Select D365 F&O environment:")
    for i, env in enumerate(environments, start=1):
        print(f"  {i}) {env.name} — {env.environment_url}")
    names = {env.name.lower(): env for env in environments}

    while True:
        choice = input(f"Enter choice [1-{len(environments)}] or environment name: ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(environments):
                return environments[idx - 1]
        elif choice.lower() in names:
            return names[choice.lower()]
        print("Invalid choice. Try again.", file=sys.stderr)


def confirm_proceed_after_preflight(*, has_employee_duplicates: bool) -> bool:
    """Ask user to continue OData import after preflight reported issues."""
    print()
    if has_employee_duplicates:
        print(
            "Warning: Existing employees were found. Continuing may create duplicate "
            "worker records in F&O."
        )
    while True:
        choice = input("Proceed with OData import? [y/N]: ").strip().lower()
        if choice in ("y", "yes"):
            return True
        if choice in ("n", "no", ""):
            return False
        print("Enter y or n.", file=sys.stderr)
