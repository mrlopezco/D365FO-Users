"""Compatibility re-exports (prefer app.config.entity)."""

from app.config.entity import (
    ODATA_IMPORT_FILES,
    build_entity_rows,
    enrich_user,
    load_entity_config,
    resolve_row,
)

__all__ = [
    "ODATA_IMPORT_FILES",
    "build_entity_rows",
    "enrich_user",
    "load_entity_config",
    "resolve_row",
]
