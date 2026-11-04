"""Read-only evaluation of effective node inputs for distinct values and preflight.

External producers are explicit unknown boundaries. No execution dispatcher,
writer, source recalculation or intermediate persistence is used here.
"""
from app.core.manipola.executor import ManipolaExecutor
from app.core.projects.canvas_validation import _StaticCanvasService
from app.core.projects.schema_inference import SchemaInferenceService

from typing import Any

from app.core.projects.canvas_models import CanvasState
from app.core.manipola.plugin_executor import get_executor
from app.core.manipola.transform_ordering import incoming_edge_sort_key_for_nodes
from app.shared.execution.node_fields import node_field
from dataclasses import dataclass

from psycopg import Error as PsycopgError
from sqlalchemy.exc import SQLAlchemyError

from app.core.artifacts import SourceLoader, SourceRef
from app.core.artifacts.types import ArtifactResolutionError, SourceLoadingError
from app.core.dataframes.tabular import DATAFRAME_PARSE_ERRORS, filter_visible_columns, rename_duplicate_columns
from app.core.resources.exceptions import MemoryLimitExceededError


FrameResults = dict[str, tuple[Any, bool, str | None]]


# Mappatore unico degli errori della preparazione in memoria (distinct e
# preflight). Tabella dichiarativa, in ordine: la prima famiglia che
# corrisponde decide stato HTTP e motivo. Nessun ``except Exception``: ogni
# famiglia elencata diventa una risposta 4xx con ``reason`` esplicito.
@dataclass(frozen=True)
class PreparationFailure:
    status_code: int
    reason: str
    message: str


PREPARATION_ERROR_FAMILIES: tuple[tuple[tuple[type[BaseException], ...], int, str, str], ...] = (
    ((SourceLoadingError, ArtifactResolutionError), 400, "source_unavailable", "Sorgente non disponibile"),
    ((OSError, UnicodeError, MemoryLimitExceededError, SQLAlchemyError, PsycopgError, RuntimeError) + DATAFRAME_PARSE_ERRORS,
     422, "data_unreadable", "Dati dell'ingresso non leggibili"),
    ((ValueError, KeyError, TypeError, IndexError, AttributeError, ArithmeticError, LookupError),
     422, "invalid_configuration", "Configurazione non valida"),
)
PREPARATION_ERRORS: tuple[type[BaseException], ...] = tuple(
    family for families, *_ in PREPARATION_ERROR_FAMILIES for family in families)


def classify_preparation_error(error: BaseException) -> PreparationFailure:
    for families, status_code, reason, title in PREPARATION_ERROR_FAMILIES:
        if isinstance(error, families):
            detail = str(error).strip() or type(error).__name__
            return PreparationFailure(status_code, reason, f"{title}: {detail}")
    raise TypeError(f"Eccezione fuori dalle famiglie della preparazione: {type(error).__name__}") from error


class CanvasDataPreparation:
    def __init__(self, canvas: CanvasState, group_id: str, *, source_limit: int = 10000) -> None:
        self.canvas = canvas
        self.group_id = group_id
        self.source_limit = source_limit
        self.nodes = {node.id: node for node in canvas.nodes}
        self.cache: FrameResults = {}
        self.active: set[str] = set()

    def input_ids(self, node_id: str) -> list[str]:
        edges = [edge for edge in self.canvas.edges if edge.target == node_id]
        if self.nodes[node_id].type in {"transform", "multi_join"}:
            edges.sort(key=lambda edge: incoming_edge_sort_key_for_nodes(edge, self.nodes))
        return [edge.source for edge in edges]

    def input(self, node_id: str) -> tuple[Any, bool, str | None]:
        ids = self.input_ids(node_id)
        return self.output(ids[0]) if ids else (None, False, "Nessun ingresso")

    def output(self, node_id: str) -> tuple[Any, bool, str | None]:
        if node_id in self.cache:
            return self.cache[node_id]
        if node_id in self.active:
            raise ValueError("Il canvas contiene un ciclo")
        self.active.add(node_id)
        try:
            result = self._output(node_id)
            self.cache[node_id] = result
            return result
        finally:
            self.active.remove(node_id)

    def _output(self, node_id: str) -> tuple[Any, bool, str | None]:
        node = self.nodes[node_id]
        if node.type == "project_input":
            return None, False, "Input file runtime: dati non disponibili nel canvas"
        if node.type in {"staticValues", "file_source", "project_source"}:
            data = {key: node_field(node, key) for key in ("fields", "file_source_id", "source_project_id")}
            ref = SourceRef.from_node_data(node.type, data)
            frame, reason = SourceLoader().load_in_memory(ref, expected_group_id=self.group_id)
            if frame is None:
                return None, False, reason
            frame = filter_visible_columns(frame, node_field(node, "columns") or [])
            if node.type == "project_source":
                frame, _ = rename_duplicate_columns(frame)
            sampled = len(frame) > self.source_limit
            return frame.head(self.source_limit), sampled, None
        if node.type in {"transform", "multi_join"}:
            incoming = [self.output(sid) for sid in self.input_ids(node_id)]
            unavailable = next((value for value in incoming if value[0] is None), None)
            if unavailable is not None:
                return unavailable
            frames = [value[0] for value in incoming]
            if not frames:
                return None, False, "Nessun ingresso"
            if node.type == "multi_join":
                frame = ManipolaExecutor(project_service=None)._esegui_multi_join(node, frames)
            else:
                inference = SchemaInferenceService(_StaticCanvasService(self.canvas))
                columns = [col for sid in self.input_ids(node_id) for col in inference.infer_node_schema_typed("", sid).columns]
                prepared_node = node.model_copy(update={"data": {**(node.data or {}), "inputColumns": columns}})
                frame = get_executor().execute(node_field(node, "transform_op"), frames, prepared_node)
            return frame, any(value[1] for value in incoming), None
        if node.type == "result":
            return self.input(node_id)
        return None, False, f"Ingresso dipendente da {node.type}: non conoscibile senza esecuzione esterna"
