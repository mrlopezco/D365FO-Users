"""Interactive terminal prompts for environment selection and import confirmation."""

from __future__ import annotations

import sys

from app.d365.environments import D365Environment


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


def confirm_proceed_after_preflight(*, create_count: int, skip_count: int) -> bool:
    """Ask user to continue OData import after preflight plan is shown."""
    print()
    print(
        f"Summary: {create_count} operation(s) will POST to F&O; "
        f"{skip_count} will be skipped (no change)."
    )
    if create_count == 0:
        print("Nothing new to import — continuing will only run skip/no-op steps.")
    while True:
        choice = input("Proceed with OData import? [y/N]: ").strip().lower()
        if choice in ("y", "yes"):
            return True
        if choice in ("n", "no", ""):
            return False
        print("Enter y or n.", file=sys.stderr)
