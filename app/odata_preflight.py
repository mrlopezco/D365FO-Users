"""Compatibility re-exports (prefer app.preflight.checks)."""

from app.preflight.checks import (
    PreflightReport,
    UserPreflightRow,
    run_preflight,
)

__all__ = ["PreflightReport", "UserPreflightRow", "run_preflight"]
