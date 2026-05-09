"""Portable metadata CRUD helpers for ``FileSourceService``.

This module owns DB row -> FileSource mapping and metadata-only CRUD
orchestration. Side effects stay explicit through injected collaborators so
``FileSourceService`` can remain the public facade.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional


DbGetFileSource = Callable[..., Optional[dict[str, Any]]]
DbDeleteFileSource = Callable[[str], Any]
DbListFileSources = Callable[[Optional[str]], list[dict[str, Any]]]
DbUpdateFileSourceMetadata = Callable[[str, dict[str, Any]], None]
DbSaveFileSource = Callable[..., Any]
GetFileSource = Callable[[str], Optional[Any]]
LoadDataFrame = Callable[..., tuple[Any, Optional[Any]]]
ColumnBuilderFromDataFrame = Callable[[Any, Optional[Any]], list[Any]]
GetBlobFromDb = Callable[..., Optional[bytes]]
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
    db_list_file_sources: DbListFileSources,
    get_file_source_by_id: GetFileSource,
    file_source_list_model: Any = None,
    use_app_models: bool,
    logger: Any,
) -> Any:
    """List file sources, preserving ``group_id=None`` as unfiltered."""
    try:
        rows = db_list_file_sources(group_id)
        files = []
        for row in rows:
            fs = get_file_source_by_id(row["id"])
            if fs:
                files.append(fs)
        if use_app_models and file_source_list_model is not None:
            return file_source_list_model(files=files)
        return files
    except Exception as e:
        logger.error(f"[GESTITO] Errore list_file_sources: {e}")
        if use_app_models and file_source_list_model is not None:
            return file_source_list_model(files=[])
        return []


def update_file_source(
    file_id: str,
    data: Any,
    *,
    get_file_source_by_id: GetFileSource,
    load_dataframe: LoadDataFrame,
    column_builder_from_dataframe: ColumnBuilderFromDataFrame,
    get_blob_from_db: GetBlobFromDb,
    time_now: TimeNow,
    db_update_file_source_metadata: DbUpdateFileSourceMetadata,
    db_get_file_source: DbGetFileSource,
    db_save_file_source: DbSaveFileSource,
    default_group: str,
    use_app_models: bool,
    logger: Any,
) -> Optional[Any]:
    """Update FileSource metadata, including the legacy full-row fallback."""
    file_source = get_file_source_by_id(file_id)
    if not file_source:
        return None

    update_data = (
        data.model_dump(exclude_unset=True)
        if hasattr(data, "model_dump")
        else data
    )
    for field_name, value in update_data.items():
        setattr(file_source, field_name, value)

    if "layer" in update_data or "sheet" in update_data:
        try:
            df, gdf = load_dataframe(
                file_id, expected_group_id=file_source.group_id,
            )
            raw_cols = column_builder_from_dataframe(df, gdf)
            file_source.columns = [
                {"name": c.name, "type": c.type}
                if not isinstance(c, dict)
                else c
                for c in raw_cols
            ]
            file_source.row_count = len(df)
        except Exception as e:
            logger.warning(f"Errore aggiornamento colonne: {e}")

    file_source.updated_at = time_now()
    metadata = file_source.model_dump(mode="json") if use_app_models else {}
    try:
        db_update_file_source_metadata(file_id, metadata)
    except Exception:
        existing_row = db_get_file_source(file_id, include_data=False) or {}
        db_save_file_source(
            file_id=file_id,
            group_id=existing_row.get("group_id") or default_group,
            name=file_source.name,
            original_filename=file_source.original_filename,
            fmt=file_source.format,
            data=get_blob_from_db(
                file_id, expected_group_id=file_source.group_id,
            ) or b"",
            metadata=metadata,
        )
    return file_source
