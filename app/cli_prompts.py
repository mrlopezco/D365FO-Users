"""Compatibility re-exports (prefer app.cli.prompts)."""

from app.cli.prompts import choose_environment, confirm_proceed_after_preflight

__all__ = ["choose_environment", "confirm_proceed_after_preflight"]
