"""CARICA-owned ArcGIS write orchestration boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional

from app.core.features.arcgis_writer_boundary import (
    ArcGISColumnMappingsInput,
    ArcGISFeatureWriter,
)
from app.utils.logger import get_logger

if TYPE_CHECKING:
    import pandas as pd


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
class CaricaArcGISWriteOutcome:
    """Domain outcome converted to a Celery result only at the worker edge."""

    status: str
    success: bool
    rows_written: int
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
        """Serialize the outcome for the existing Celery/API result shape."""
        if not self.success:
            return {
                "status": self.status,
                "success": False,
                "error": self.error,
                "message": self.message,
                "error_message": self.error_message or self.message,
                "rows_written": 0,
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
    from app.utils.dataframe import save_dataframe

    return FeaturesService(save_function=save_dataframe)


def execute_carica_arcgis_write(
    *,
    df: "pd.DataFrame",
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
            total_rows=total_rows,
            message="Errore scrittura ArcGIS (consulta log)",
            error="carica_arcgis_write_failed",
            error_message="Errore scrittura ArcGIS (consulta log)",
        )

    rows = raw_result.rows_written
    return CaricaArcGISWriteOutcome(
        status="completed",
        success=True,
        project_id=request.project_id,
        rows_written=rows,
        total_rows=total_rows,
        columns_count=columns_count,
        message=raw_result.message or f"Scrittura ArcGIS completata: {rows} features",
        service_url=raw_result.service_url,
        layer_url=raw_result.layer_url,
        item_id=raw_result.item_id,
        errors=raw_result.errors,
        include_errors=raw_result.include_errors,
    )


def _create_service(
    *,
    df: "pd.DataFrame",
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
    df: "pd.DataFrame",
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


def _validation_error(message: str, total_rows: int) -> CaricaArcGISWriteOutcome:
    return CaricaArcGISWriteOutcome(
        status="validation_error",
        success=False,
        rows_written=0,
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
