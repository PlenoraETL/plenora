"""Portable metadata CRUD helpers for ``FileSourceService``.

This module owns DB row -> FileSource mapping and metadata-only CRUD
orchestration. Side effects stay explicit through injected collaborators so
``FileSourceService`` can remain the public facade.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable, Optional


DbGetFileSource = Callable[..., Optional[dict[str, Any]]]
DbDeleteFileSource = Callable[[str], Any]
DbListFileSources = Callable[..., list[dict[str, Any]]]
DbUpdateFileSourceMetadata = Callable[[str, dict[str, Any]], None]
GetFileSource = Callable[[str], Optional[Any]]
LoadDataFrame = Callable[..., tuple[Any, Optional[Any]]]
ColumnBuilderFromDataFrame = Callable[[Any, Optional[Any]], list[Any]]
TimeNow = Callable[[], Any]


def file_source_from_row(
    row: dict[str, Any],
    *,
    file_source_model: Any,
    use_app_models: bool,
) -> Any:
    """Build a FileSource model from a DB row.

    ``group_id`` and ``created_by`` are canonical DB columns, so they override
    any stale values embedded inside the serialized metadata blob.
    """
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    metadata["group_id"] = row.get("group_id", "")
    metadata["created_by"] = row.get("created_by")
    if use_app_models:
        return file_source_model.model_validate(metadata)
    return file_source_model.from_json(json.dumps(metadata))


def get_file_source(
    file_id: str,
    *,
    db_get_file_source: DbGetFileSource,
    file_source_model: Any,
    use_app_models: bool,
    logger: Any,
) -> Optional[Any]:
    """Fetch and map one file source from the domain DB."""
    try:
        row = db_get_file_source(file_id, include_data=False)
        if not row:
            return None
        return file_source_from_row(
            row,
            file_source_model=file_source_model,
            use_app_models=use_app_models,
        )
    except Exception as e:
        logger.error(f"[GESTITO] Errore get_file_source {file_id}: {e}")
        return None


def delete_file_source(
    file_id: str,
    *,
    db_get_file_source: DbGetFileSource,
    db_delete_file_source: DbDeleteFileSource,
    logger: Any,
) -> bool:
    """Delete one file source if it exists."""
    try:
        if not db_get_file_source(file_id):
            return False
        db_delete_file_source(file_id)
        logger.info(f"FileSource eliminato: {file_id}")
        return True
    except Exception as e:
        logger.error(f"[GESTITO] Errore delete_file_source {file_id}: {e}")
        return False


def list_file_sources(
    group_id: Optional[str] = None,
    *,
    resource_ids: set[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    after: tuple[object, str] | None = None,
    db_list_file_sources: DbListFileSources,
    get_file_source_by_id: GetFileSource,
    file_source_list_model: Any = None,
    use_app_models: bool,
    logger: Any,
) -> Any:
    """List file sources, preserving ``group_id=None`` as unfiltered.

    Regole della pagina (conferma UI A-K10):
      - ``next_cursor`` viene dalle righe del DB, non dai file restituiti:
        una riga senza metadati leggibili e' saltata (con il log) ma non
        accorcia la pagina, quindi il client non scambia una pagina corta
        per l'ultima e non tronca la lista;
      - un errore del DB PROPAGA (la route risponde 500 e il client lo
        mostra): mai ``files=[]`` come se non ci fossero file.
    """
    kwargs: dict[str, object] = {"resource_ids": resource_ids, "limit": limit, "offset": offset}
    if after is not None:
        kwargs["after"] = after
    rows = db_list_file_sources(group_id, **kwargs)
    files = []
    for row in rows:
        fs = get_file_source_by_id(row["id"])
        if fs:
            files.append(fs)
        else:
            logger.warning(f"[GESTITO] list_file_sources: file {row['id']} senza metadati leggibili, saltato")
    next_cursor = None
    if limit is not None and rows and len(rows) >= limit:
        last = rows[-1]
        next_cursor = encode_file_cursor(last.get("created_at"), str(last["id"]))
    if use_app_models and file_source_list_model is not None:
        out = file_source_list_model(files=files)
        out.next_cursor = next_cursor
        return out
    return files


def encode_file_cursor(created_at: object, file_id: str) -> str:
    """Cursore opaco della lista dei file: (created_at, id) dell'ultima riga."""
    stamp = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or "")
    raw = json.dumps([stamp, file_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_file_cursor(cursor: str) -> tuple[str, str]:
    """Il contrario di encode_file_cursor; ``ValueError`` se non valido."""
    try:
        stamp, file_id = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, TypeError) as exc:  # base64, UTF-8 e JSON non validi sono ValueError
        raise ValueError("cursore non valido") from exc
    if not isinstance(stamp, str) or not isinstance(file_id, str) or not stamp or not file_id:
        raise ValueError("cursore non valido")
    return stamp, file_id


def update_file_source(
    file_id: str,
    data: Any,
    *,
    get_file_source_by_id: GetFileSource,
    load_dataframe: LoadDataFrame,
    column_builder_from_dataframe: ColumnBuilderFromDataFrame,
    time_now: TimeNow,
    db_update_file_source_metadata: DbUpdateFileSourceMetadata,
    use_app_models: bool,
    logger: Any,
) -> Optional[Any]:
    """Update FileSource metadata senza toccare il payload object storage."""
    file_source = get_file_source_by_id(file_id)
    if not file_source:
        return None

    update_data = (
        data.model_dump(exclude_unset=True)
        if hasattr(data, "model_dump")
        else data
    )
    if "layer" in update_data or "sheet" in update_data:
        target_layer = update_data.get("layer", file_source.layer)
        target_sheet = update_data.get("sheet", file_source.sheet)
        if target_layer and target_sheet:
            raise ValueError("Specificare un solo valore tra layer e sheet")
        df, gdf = load_dataframe(
            file_id,
            layer=target_layer,
            sheet=target_sheet,
            expected_group_id=file_source.group_id,
        )
        raw_cols = column_builder_from_dataframe(df, gdf)
        refreshed_columns = [
            {"name": c.name, "type": c.type}
            if not isinstance(c, dict)
            else c
            for c in raw_cols
        ]
        refreshed_row_count = len(df)

    for field_name, value in update_data.items():
        setattr(file_source, field_name, value)

    if "layer" in update_data or "sheet" in update_data:
        file_source.columns = refreshed_columns
        file_source.row_count = refreshed_row_count

    file_source.updated_at = time_now()
    metadata = file_source.model_dump(mode="json") if use_app_models else {}
    # Fail loudly. Il vecchio fallback scaricava il BLOB completo e lo
    # riscriveva come BYTEA: oltre al picco RAM, poteva spostare dati da S3 a
    # Postgres durante un semplice update dei metadati.
    db_update_file_source_metadata(file_id, metadata)
    return file_source
