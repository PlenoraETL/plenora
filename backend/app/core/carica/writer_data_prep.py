"""
Mixin DATA_PREP: DataFrame/value transformations for DatabaseWriter.

Keeps database loading behavior isolated from orchestration callers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.carica.models import ColumnMapping
from app.core.carica.writer_data_prep_helpers import (
    build_params,
    fix_mixed_type_columns,
    geometry_to_wkt,
    prepare_dataframe,
)
from app.utils.logger import get_logger

# Same logger name as `database_writer.py`: tests and runtime log consumers attach
# handlers to the canonical writer logger.
logger = get_logger("app.core.carica.writer")


class DataPrepMixin:
    """Mixin API for DatabaseWriter data preparation.

    The actual transformations live in writer_data_prep_helpers so they can be
    tested without constructing the full DatabaseWriter mixin chain.
    """

    def _prepare_dataframe(self, df: Any) -> Any:
        """Prepara il DataFrame per la scrittura."""
        return prepare_dataframe(df, logger)

    def _fix_mixed_type_columns(self, df: Any) -> Any:
        """Rileva colonne con tipi misti e le forza a str."""
        return fix_mixed_type_columns(df, logger)

    def _geometry_to_wkt(self, value: Any) -> Optional[str]:
        """Converte un valore geometria in WKT."""
        return geometry_to_wkt(value, logger)

    def _build_params(
        self,
        row: Any,
        columns: List[ColumnMapping],
        operation: str,
    ) -> Dict[str, Any]:
        """Costruisce i parametri per una singola riga."""
        return build_params(row, columns, operation, self.config.match_columns, logger)
