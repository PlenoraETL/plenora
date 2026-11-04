"""Server-owned schemas for validation and execution of an in-memory canvas."""

from types import SimpleNamespace

from app.core.projects.canvas_models import CanvasState
from app.core.projects.types import JsonObject, SchemaMap


def infer_canvas_columns(canvas: CanvasState | JsonObject, *, project_id: str = "", project_type: str | None = None,
                         api_configs: dict[str, dict] | None = None) -> SchemaMap:
    # Lazy import: inference also dispatches Arricchisci node handlers.
    from app.core.projects.schema_inference import SchemaInferenceService

    if isinstance(canvas, dict):
        canvas = CanvasState.model_validate(canvas)
    service = SimpleNamespace(
        get_project_canvas=lambda _: canvas,
        get_project_metadata=lambda _: SimpleNamespace(project_type=project_type),
    )
    return SchemaInferenceService(service, api_configs=api_configs).infer_all_schemas(project_id, canvas)
