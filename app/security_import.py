"""Compatibility re-exports (prefer app.importer.security)."""

from app.importer.security import (
    ExistingSecurityAssignments,
    SecurityRoleCatalog,
    import_security_assignments,
)

__all__ = [
    "ExistingSecurityAssignments",
    "SecurityRoleCatalog",
    "import_security_assignments",
]
