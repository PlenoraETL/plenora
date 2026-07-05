"""CARICA-owned ArcGIS write orchestration boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from app.core.connections.arcgis_writer_boundary import (
    ArcGISColumnMappingsInput,
    ArcGISFeatureWriter,
    ArcGISLayerSpec,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

CaricaArcGISTaskResult = Mapping[str, object]


@dataclass(frozen=True)
class ArcGISFeatureWriteResult:
    """Typed subset returned by the existing ArcGIS feature writer contract."""

    success: bool = False
    feature_count: int = 0
    count: int = 0
    message: str = ""
    service_url: str = ""
    layer_url: str = ""
    item_id: str = ""
    errors: tuple[object, ...] = ()
    include_errors: bool = False

    @classmethod
    def from_mapping(
        cls,
        result: Mapping[str, object],
    ) -> "ArcGISFeatureWriteResult":
        errors = result.get("errors")
        return cls(
            success=result.get("success") is True,
            feature_count=_int_result(result, "feature_count"),
            count=_int_result(result, "count"),
            message=_string_result(result, "message"),
            service_url=_string_result(result, "service_url"),
            layer_url=_string_result(result, "layer_url"),
            item_id=_string_result(result, "item_id"),
            errors=tuple(errors) if isinstance(errors, list) else (),
            include_errors="errors" in result,
        )

    @property
    def rows_written(self) -> int:
        return self.feature_count or self.count


@dataclass(frozen=True)
class CaricaArcGISWriteRequest:
    """Explicit CARICA request for ArcGIS feature writes."""

    project_id: str
    connection_id: str
    operation: str
    geometry_column: str
    source_srid: Optional[int]
    batch_size: Optional[int]
    column_mappings: ArcGISColumnMappingsInput
    layer_url: str = ""
    match_column: str = ""
    service_name: str = ""
    layer_name: str = "Layer 0"
    folder_id: str = ""


@dataclass(frozen=True)
class CaricaArcGISMultiLayerRequest:
    """CARICA request for an N-layer ArcGIS Feature Service write.

    Domain-neutral: descrive solo `dove` (servizio nuovo o esistente) e `come`
    (operation/srid/folder/renderer). I layer veri sono `Sequence[ArcGISLayerSpec]`
    passati a parte. Qualsiasi modulo (IMDF, export, ...) puo' usarlo: la policy
    di quali layer/ordine/match-key/renderer e' del chiamante (per IMDF arriva
    dal descrittore del bundle).
    """

    project_id: str
    connection_id: str
    operation: str  # create_service | append | upsert | update
    source_srid: Optional[int] = None
    service_name: str = ""  # create_service: nome del nuovo Feature Service
    service_url: str = ""  # append/upsert/update: URL del servizio esistente
    folder_id: str = ""
    # Renderer per-layer keyed by LAYER NAME (non URL): gli URL per-layer si
    # conoscono solo DOPO il write, quindi vengono risolti nome->URL dal
    # risultato. Best-effort: un errore non fa fallire la pubblicazione.
    renderers: Optional[Mapping[str, Mapping[str, object]]] = None


@dataclass(frozen=True)
class CaricaArcGISWriteOutcome:
    """Domain outcome converted to a task result at the worker edge."""

    status: str
    success: bool
    rows_written: int
    rows_inserted: int
    rows_updated: int
    total_rows: int
    message: str
    project_id: str = ""
    columns_count: int = 0
    service_url: str = ""
    layer_url: str = ""
    item_id: str = ""
    errors: tuple[object, ...] = ()
    include_errors: bool = False
    error: str = ""
    error_message: str = ""

    def to_task_result(self) -> CaricaArcGISTaskResult:
        """Serialize the outcome for the task/API result shape."""
        if not self.success:
            return {
                "status": self.status,
                "success": False,
                "error": self.error,
                "message": self.message,
                "error_message": self.error_message or self.message,
                "rows_written": 0,
                "rows_inserted": 0,
                "rows_updated": 0,
                "total_rows": self.total_rows,
            }

        arcgis_result = {
            key: value
            for key, value in (
                ("service_url", self.service_url),
                ("layer_url", self.layer_url),
                ("item_id", self.item_id),
                ("errors", list(self.errors) if self.include_errors else None),
            )
            if value
        }
        if self.include_errors and "errors" not in arcgis_result:
            arcgis_result["errors"] = []
        return {
            "status": self.status,
            "success": True,
            "project_id": self.project_id,
            "rows_written": self.rows_written,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "rows_count": self.rows_written,
            "total_rows": self.total_rows,
            "columns_count": self.columns_count,
            "message": self.message,
            "service_url": self.service_url,
            "layer_url": self.layer_url,
            "item_id": self.item_id,
            "arcgis_result": arcgis_result,
        }


def create_default_arcgis_feature_writer() -> ArcGISFeatureWriter:
    """Build the generic ArcGIS feature writer used by CARICA."""
    from app.core.features.features_service import FeaturesService

    return FeaturesService()


def execute_carica_arcgis_write(
    *,
    df: Any,
    request: CaricaArcGISWriteRequest,
    feature_writer: ArcGISFeatureWriter,
) -> CaricaArcGISWriteOutcome:
    """Validate and execute a CARICA ArcGIS write against the provider boundary."""
    total_rows = len(df)
    columns_count = len(df.columns)

    if not request.connection_id:
        return _validation_error("Nessuna connessione ArcGIS configurata", total_rows)

    try:
        if request.operation == "create_service":
            raw_result = _create_service(
                df=df,
                request=request,
                feature_writer=feature_writer,
            )
        else:
            raw_result = _write_existing_layer(
                df=df,
                request=request,
                feature_writer=feature_writer,
            )
    except ValueError as exc:
        return _validation_error(str(exc), total_rows)
    except Exception as exc:
        logger.error("[GESTITO] Errore scrittura ArcGIS CARICA: %s", exc, exc_info=True)
        return CaricaArcGISWriteOutcome(
            status="write_error",
            success=False,
            rows_written=0,
            rows_inserted=0,
            rows_updated=0,
            total_rows=total_rows,
            message="Errore scrittura ArcGIS (consulta log)",
            error="carica_arcgis_write_failed",
            error_message="Errore scrittura ArcGIS (consulta log)",
        )

    rows = raw_result.rows_written
    rows_inserted, rows_updated = _arcgis_write_counts(request.operation, rows)
    return CaricaArcGISWriteOutcome(
        status="completed",
        success=True,
        project_id=request.project_id,
        rows_written=rows,
        rows_inserted=rows_inserted,
        rows_updated=rows_updated,
        total_rows=total_rows,
        columns_count=columns_count,
        message=raw_result.message or f"Scrittura ArcGIS completata: {rows} features",
        service_url=raw_result.service_url,
        layer_url=raw_result.layer_url,
        item_id=raw_result.item_id,
        errors=raw_result.errors,
        include_errors=raw_result.include_errors,
    )


def execute_carica_arcgis_multilayer_write(
    *,
    layers: Sequence[ArcGISLayerSpec],
    request: CaricaArcGISMultiLayerRequest,
    feature_writer: ArcGISFeatureWriter,
) -> CaricaArcGISWriteOutcome:
    """Validate and execute an N-layer CARICA ArcGIS write.

    `create_service` crea un Feature Service con tutti i layer; gli altri mode
    (`append`/`upsert`/`update`) scrivono su un servizio esistente matchando per
    nome layer. I renderer (opzionali) sono applicati best-effort dopo la
    scrittura: un loro errore NON fa fallire la pubblicazione (sono cosmetici).
    """
    total_rows = sum(len(spec.df) for spec in layers)

    if not request.connection_id:
        return _validation_error("Nessuna connessione ArcGIS configurata", total_rows)
    if not layers:
        return _validation_error("Nessun layer da pubblicare", total_rows)

    try:
        if request.operation == "create_service":
            if not request.service_name:
                raise ValueError("Nome servizio ArcGIS non specificato")
            raw_mapping = feature_writer.create_service_with_layers(
                connection_id=request.connection_id,
                service_name=request.service_name,
                layers=layers,
                source_srid=request.source_srid,
                folder_id=request.folder_id,
            )
        else:
            if not request.service_url:
                raise ValueError("URL servizio ArcGIS non specificato")
            raw_mapping = feature_writer.write_layers_to_existing_service(
                connection_id=request.connection_id,
                service_url=request.service_url,
                layers=layers,
                mode=request.operation,
                source_srid=request.source_srid,
            )
        raw_result = ArcGISFeatureWriteResult.from_mapping(raw_mapping)
    except ValueError as exc:
        return _validation_error(str(exc), total_rows)
    except Exception as exc:
        logger.error(
            "[GESTITO] Errore scrittura ArcGIS multi-layer CARICA: %s", exc, exc_info=True
        )
        return CaricaArcGISWriteOutcome(
            status="write_error",
            success=False,
            rows_written=0,
            rows_inserted=0,
            rows_updated=0,
            total_rows=total_rows,
            message="Errore scrittura ArcGIS (consulta log)",
            error="carica_arcgis_write_failed",
            error_message="Errore scrittura ArcGIS (consulta log)",
        )

    if not raw_result.success:
        message = "; ".join(str(e) for e in raw_result.errors) or (
            "Scrittura ArcGIS multi-layer fallita"
        )
        return CaricaArcGISWriteOutcome(
            status="write_error",
            success=False,
            rows_written=0,
            rows_inserted=0,
            rows_updated=0,
            total_rows=total_rows,
            message="Errore scrittura ArcGIS (consulta log)",
            error="carica_arcgis_write_failed",
            error_message=message,
            errors=raw_result.errors,
            include_errors=True,
        )

    service_url = raw_result.service_url
    if request.renderers:
        url_renderers = _resolve_layer_renderers(raw_mapping, request.renderers)
        if url_renderers:
            try:
                feature_writer.apply_layer_renderers(
                    connection_id=request.connection_id,
                    renderers=url_renderers,
                )
            except Exception as exc:
                # Best-effort: i renderer sono cosmetici, non fanno fallire il write.
                logger.warning("[GESTITO] Applicazione renderer ArcGIS fallita: %s", exc)

    rows = raw_result.rows_written
    rows_inserted, rows_updated = _arcgis_write_counts(request.operation, rows)
    return CaricaArcGISWriteOutcome(
        status="completed",
        success=True,
        project_id=request.project_id,
        rows_written=rows,
        rows_inserted=rows_inserted,
        rows_updated=rows_updated,
        total_rows=total_rows,
        columns_count=0,
        message=raw_result.message or f"Pubblicazione ArcGIS completata: {rows} features",
        service_url=service_url,
        layer_url=raw_result.layer_url,
        item_id=raw_result.item_id,
        errors=raw_result.errors,
        include_errors=raw_result.include_errors,
    )


def _create_service(
    *,
    df: Any,
    request: CaricaArcGISWriteRequest,
    feature_writer: ArcGISFeatureWriter,
) -> ArcGISFeatureWriteResult:
    if not request.service_name:
        raise ValueError("Nome servizio ArcGIS non specificato")

    return ArcGISFeatureWriteResult.from_mapping(
        feature_writer.create_service_from_df(
            connection_id=request.connection_id,
            service_name=request.service_name,
            layer_name=request.layer_name,
            df=df,
            geometry_column=request.geometry_column,
            source_srid=request.source_srid,
            folder_id=request.folder_id,
            column_mappings=request.column_mappings,
        ),
    )


def _write_existing_layer(
    *,
    df: Any,
    request: CaricaArcGISWriteRequest,
    feature_writer: ArcGISFeatureWriter,
) -> ArcGISFeatureWriteResult:
    if not request.layer_url:
        raise ValueError("URL layer ArcGIS non specificato")

    return ArcGISFeatureWriteResult.from_mapping(
        feature_writer.write_features_from_df(
            connection_id=request.connection_id,
            layer_url=request.layer_url,
            operation=request.operation,
            df=df,
            geometry_column=request.geometry_column,
            source_srid=request.source_srid,
            batch_size=request.batch_size,
            match_column=request.match_column,
            column_mappings=request.column_mappings,
        ),
    )


def _resolve_layer_renderers(
    raw_mapping: Mapping[str, object],
    renderers_by_name: Mapping[str, Mapping[str, object]],
) -> Mapping[str, Mapping[str, object]]:
    """Risolve i renderer keyed-by-name in keyed-by-URL usando i layer scritti.

    Il risultato del write espone una lista ``layers`` con ``{name, url}`` per
    ogni layer (gli URL sono noti solo dopo la scrittura). Per ogni layer con un
    renderer dichiarato e un URL valido, produce ``{url: drawing_info}``.
    """
    written = raw_mapping.get("layers")
    if not isinstance(written, (list, tuple)):
        return {}
    resolved: "dict[str, Mapping[str, object]]" = {}
    for entry in written:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        url = entry.get("url")
        if isinstance(name, str) and isinstance(url, str) and url and name in renderers_by_name:
            resolved[url] = renderers_by_name[name]
    return resolved


def _validation_error(message: str, total_rows: int) -> CaricaArcGISWriteOutcome:
    return CaricaArcGISWriteOutcome(
        status="validation_error",
        success=False,
        rows_written=0,
        rows_inserted=0,
        rows_updated=0,
        total_rows=total_rows,
        message=message,
        error_message=message,
    )


def _string_result(result: Mapping[str, object], key: str) -> str:
    value = result.get(key)
    return value if isinstance(value, str) else ""


def _int_result(result: Mapping[str, object], key: str) -> int:
    value = result.get(key)
    return value if isinstance(value, int) else 0


def _arcgis_write_counts(operation: str, rows_written: int) -> tuple[int, int]:
    if operation == "update":
        return 0, rows_written
    if operation == "upsert":
        return 0, rows_written
    if operation == "delete":
        return 0, 0
    return rows_written, 0
