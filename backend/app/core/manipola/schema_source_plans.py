"""Adattatore dei metadati sorgente verso i confini runtime di Manipola."""

from app.core.artifacts.source_loader import SourceLoader
from app.core.dataframes.tabular import make_unique_column_names
from app.core.manipola.canvas_schema_runtime import build_columns_with_types
from app.core.manipola.types import ColumnMetadataRows, JsonRows


def static_columns(fields: JsonRows) -> ColumnMetadataRows:
    """Il loader statico elabora solo configurazione, senza I/O esterno."""
    return build_columns_with_types(SourceLoader()._load_static(tuple(fields)), {})


def unique_source_names(names: list[str]) -> list[str]:
    """Usa la stessa deduplicazione della sorgente di progetto."""
    return make_unique_column_names(names)[0]
