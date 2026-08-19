"""Import run result counters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImportResult:
    succeeded: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)
