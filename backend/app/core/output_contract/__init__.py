"""Public output contract boundary for project-to-project data exchange."""

from .models import OutputContract, OutputColumn
from .service import (
    build_output_contract,
    column_types_from_contract,
    ensure_dataframe_matches_contract,
    node_columns_from_contract,
    persist_output_contract,
    result_columns_from_plan,
)
from .canvas_sync import sync_canvas_with_output_contracts

__all__ = [
    "OutputColumn",
    "OutputContract",
    "build_output_contract",
    "column_types_from_contract",
    "ensure_dataframe_matches_contract",
    "node_columns_from_contract",
    "persist_output_contract",
    "result_columns_from_plan",
    "sync_canvas_with_output_contracts",
]
