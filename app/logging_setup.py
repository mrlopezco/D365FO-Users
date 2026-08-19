"""Configure application logging (stdout, operator-friendly)."""

from __future__ import annotations

import logging
import os
import sys


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def debug_tracebacks_enabled() -> bool:
    return os.environ.get("DEBUG", "").strip().lower() in ("1", "true", "yes")
