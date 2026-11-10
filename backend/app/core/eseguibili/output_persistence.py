"""Output persistence owned by the versioned executable domain.

Executable identifiers (``exec_*``) do not belong to ``projects``.  This
adapter implements the same persistence port used by QueryExecutor, but writes
to the executable object namespace without creating rows in ``results``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.dataframes import export_writer as export_writer_module
from app.core.dataframes import frame_io as frame_io_module
from app.core.dataframes.geodata import detect_geometry_column
from app.core.eseguibili import dashboard_data as executable_dashboard_data
from app.core.eseguibili import storage as executable_storage
from app.utils.logger import get_logger
from app.shared.execution.query_results import (
    PersistedQueryResult,
    StreamedQueryResult,
)


@dataclass(frozen=True)
class ExecutableDataFrameResult:
    output_filename: str
    storage_key: str
    output_columns: Optional[list[str]] = None


logger = get_logger(__name__)


class ExecutableOutputPersistence:
    """Persistence adapter bound to one executable task and tenant.

    ``dashboard_copy`` (deciso dal server nel contratto,
    ``shared.execution.dashboard_copy``): oltre all'output scrive la copia
    delle prime righe per la dashboard Grafana (``eseguibili.dashboard_data``).
    E' il punto unico dei due produttori: DataFrame
    (``persist_dataframe_output``) e query in streaming
    (``persist_streaming_query_to_storage``, «tee» sul cursore). La copia si
    scrive solo dopo l'output riuscito; un suo errore non fa MAI fallire
    l'esecuzione: lo garantisce in un punto solo il raccoglitore
    (``DashboardRowsCollector.add_rows``/``add_dataframe``/``save``), qui
    nessun ``try`` dedicato.
    """

    def __init__(
        self,
        *,
        task_id: str,
        group_id: str,
        stream_query_to_writer: Optional[Callable[..., tuple[int, list[str]]]] = None,
        dashboard_copy: bool = False,
    ) -> None:
        if not task_id:
            raise ValueError("task_id output eseguibile mancante")
        if not group_id:
            raise ValueError("group_id output eseguibile mancante")
        self.task_id = task_id
        self.group_id = group_id
        self._stream_query_to_writer = stream_query_to_writer
        self.dashboard_copy = bool(dashboard_copy)

    def _dashboard_collector(self) -> Optional[executable_dashboard_data.DashboardRowsCollector]:
        return executable_dashboard_data.DashboardRowsCollector() if self.dashboard_copy else None

    def _write_dashboard_copy(
        self, collector: Optional[executable_dashboard_data.DashboardRowsCollector],
    ) -> None:
        if collector is not None:
            collector.save(self.task_id, group_id=self.group_id)

    def _writer(self, filename: str, output_format: str) -> Any:
        """Scrittore del risultato: lo spazio dell'attivita' nello storage."""
        del output_format
        return executable_storage.ExecutableOutputWriter(
            self.task_id,
            filename,
            group_id=self.group_id,
        )

    def persist_streaming_query_to_storage(
        self,
        *,
        project_id: str,
        engine_factory: Any,
        connection: Any,
        sql: str,
        params: Optional[Any],
        output_format: str,
        output_filename: str,
        group_id: Optional[str] = None,
    ) -> StreamedQueryResult:
        del project_id, group_id
        writer = self._writer(output_filename, output_format)
        collector = self._dashboard_collector()
        sink_kwargs = {"row_sink": collector.add_rows} if collector is not None else {}
        try:
            if self._stream_query_to_writer is None:
                raise RuntimeError("Streaming query writer non configurato")
            rows_count, columns = self._stream_query_to_writer(
                engine_factory=engine_factory,
                connection=connection,
                sql=sql,
                params=params,
                output_writer=writer,
                output_format=output_format,
                **sink_kwargs,
            )
            storage_key = writer.finalize()
        except Exception:
            writer.abort()
            raise
        self._write_dashboard_copy(collector)
        return StreamedQueryResult(
            result_id=None,
            rows_count=rows_count,
            columns=columns,
            output_path=Path(output_filename),
            storage_key=storage_key,
        )

    def persist_dataframe_result(
        self,
        *,
        project_id: str,
        df: Any,
        output_format: str,
        filename_stem: str,
        rows_count: int,
        group_id: Optional[str] = None,
        geom_column: Optional[str] = None,
    ) -> PersistedQueryResult:
        del project_id, rows_count, group_id
        saved = self.persist_dataframe_output(
            df=df,
            output_format=output_format,
            filename_stem=filename_stem,
            geom_column=geom_column,
        )
        return PersistedQueryResult(
            result_id=None,
            output_path=Path(saved.output_filename),
            storage_key=saved.storage_key,
        )

    def persist_dataframe_output(
        self,
        *,
        df: Any,
        output_format: str,
        filename_stem: str,
        geom_column: Optional[str] = None,
        crs: str = "EPSG:4326",
        column_types: Optional[dict[str, str]] = None,
    ) -> ExecutableDataFrameResult:
        fmt = export_writer_module.normalize_export_format(output_format)
        supported = {"csv", "json", "geojson", "xlsx", "parquet", "gpkg", "shp"}
        if fmt not in supported:
            raise ValueError(f"Formato output non supportato: {output_format}")

        resolved_geom = geom_column
        if fmt in {"geojson", "gpkg", "shp"} and not resolved_geom:
            resolved_geom = detect_geometry_column(df)
        filename = f"{filename_stem}.zip" if fmt == "shp" else f"{filename_stem}.{fmt}"

        writer = self._writer(filename, fmt)
        try:
            if fmt in {"csv", "json", "parquet"}:
                for chunk in export_writer_module.dataframe_to_export_chunks(df, fmt):
                    writer.write(chunk)
            elif fmt == "xlsx":
                export_writer_module.write_dataframe_to_xlsx_stream(
                    df,
                    writer,
                    column_types=column_types,
                )
            else:
                writer.write(
                    frame_io_module.save_dataframe_to_bytes(
                        df,
                        fmt,
                        geom_column=resolved_geom,
                        crs=crs,
                        column_types=column_types,
                    )
                )
            storage_key = writer.finalize()
        except Exception:
            writer.abort()
            raise

        collector = self._dashboard_collector()
        if collector is not None:
            collector.add_dataframe(df, skip_columns=[resolved_geom] if resolved_geom else ())
        self._write_dashboard_copy(collector)

        output_columns = None
        if fmt in {"geojson", "gpkg", "shp"}:
            try:
                from app.core.dataframes.geospatial_io import (
                    exported_column_names_for_format,
                )

                output_columns = exported_column_names_for_format(
                    df.columns,
                    fmt,
                    geom_column=resolved_geom,
                )
            except Exception:
                output_columns = None
        return ExecutableDataFrameResult(
            output_filename=filename,
            storage_key=storage_key,
            output_columns=output_columns,
        )


class _MemoryWriter(io.BytesIO):
    """Scrittore in memoria (interfaccia di ``ExecutableOutputWriter``:
    ``write``, ``finalize``, ``abort``; piu' quella di un file binario)."""

    def __init__(self, owner: "MemoryOutputPersistence", filename: str, output_format: str) -> None:
        super().__init__()
        self._owner = owner
        self._filename = filename
        self._format = output_format

    def write(self, chunk: Any) -> int:
        return super().write(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)

    def finalize(self) -> str:
        self._owner.captured = (self._format, self.getvalue())
        return f"memory://{self._filename}"

    def abort(self) -> None:
        self._owner.captured = None


class MemoryOutputPersistence(ExecutableOutputPersistence):
    """Ricalcolo isolato di un progetto a monte («Utente che esegue» negli
    Estrai a monte, U3): il risultato resta in memoria, solo per
    l'esecuzione che lo ha chiesto. Nessun oggetto nello storage, nessuna
    riga ``results``. ``captured`` = ``(formato, byte)`` dopo la scrittura."""

    def __init__(
        self,
        *,
        group_id: str,
        stream_query_to_writer: Optional[Callable[..., tuple[int, list[str]]]] = None,
    ) -> None:
        super().__init__(task_id="memoria", group_id=group_id, stream_query_to_writer=stream_query_to_writer)
        self.captured: Optional[tuple[str, bytes]] = None

    def _writer(self, filename: str, output_format: str) -> Any:
        return _MemoryWriter(self, filename, export_writer_module.normalize_export_format(output_format))


__all__ = ["ExecutableDataFrameResult", "ExecutableOutputPersistence", "MemoryOutputPersistence"]
