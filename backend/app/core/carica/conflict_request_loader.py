"""Request-to-dataframe loading for CARICA conflict checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import HTTPException

from app.core.artifacts import SourceLoadingError
from app.core.dataframes.tabular import (
    dataframe_from_records,
    dataframe_from_rows,
    select_and_rename_columns,
)
from app.core.projects.upstream_identity import source_identity_need
from app.shared.contracts.payloads import JsonObject
from app.shared.execution.runtime_identity import RuntimeIdentityError, route_upstream_source


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ConflictInput:
    dataframe: Any = None
    early_response: Optional[JsonObject] = None


def load_conflict_input(
    request: Any,
    current_user: Any,
    *,
    get_connection: Callable[[str], JsonObject],
    create_engine: Callable[[JsonObject], Any],
    project_visible_checker: Callable[[str, Any], None],
    source_loader_cls: Callable[[], Any],
    source_ref_cls: Callable[..., Any],
    latest_project_artifact_ref_cls: Callable[..., Any],
    logger: Any,
) -> ConflictInput:
    """Build the dataframe used by ConflictChecker, or an early no-data response."""
    if request.sample_values:
        return ConflictInput(dataframe=dataframe_from_records(request.sample_values))

    if request.source_connection_id and request.source_table:
        return ConflictInput(
            dataframe=_load_source_table_conflict_dataframe(
                request,
                get_connection=get_connection,
                create_engine=create_engine,
                logger=logger,
            )
        )

    if request.project_id:
        return _load_project_conflict_dataframe(
            request,
            current_user,
            project_visible_checker=project_visible_checker,
            source_loader_cls=source_loader_cls,
            source_ref_cls=source_ref_cls,
            latest_project_artifact_ref_cls=latest_project_artifact_ref_cls,
            logger=logger,
        )

    return ConflictInput(
        early_response={
            "has_conflicts": False,
            "total_conflicts": 0,
            "internal_duplicates": 0,
            "db_conflicts": 0,
            "details": [],
            "message": "Nessun dato da verificare. Esegui prima la pipeline.",
        }
    )


def _load_source_table_conflict_dataframe(
    request: Any,
    *,
    get_connection: Callable[[str], JsonObject],
    create_engine: Callable[[JsonObject], Any],
    logger: Any,
) -> Any:
    src_conn = get_connection(request.source_connection_id)
    src_engine = create_engine(src_conn)
    src_schema = request.source_schema or "public"
    src_table = request.source_table
    src_cols = request.source_match_columns or request.match_columns
    for ident in [src_schema, src_table] + list(src_cols):
        if not _IDENTIFIER_RE.match(ident):
            raise HTTPException(400, f"Nome identificatore non valido: {ident}")

    from sqlalchemy import text
    from sqlalchemy.sql import quoted_name

    q_schema = quoted_name(src_schema, quote=True)
    q_table = quoted_name(src_table, quote=True)
    quoted_cols = ", ".join(quoted_name(col, quote=True) for col in src_cols)
    src_query = f"SELECT DISTINCT {quoted_cols} FROM {q_schema}.{q_table}"
    logger.info("[C07-SRC] Query sorgente diretta eseguita")
    with src_engine.connect() as src_conn_obj:
        result_proxy = src_conn_obj.execute(text(src_query))
        rows = result_proxy.fetchall()
        col_names = list(result_proxy.keys())
    df = dataframe_from_rows(rows, col_names)
    if request.source_match_columns and request.source_match_columns != request.match_columns:
        rename_map = dict(zip(request.source_match_columns, request.match_columns))
        df = select_and_rename_columns(df, request.source_match_columns, rename_map=rename_map)
    logger.info(f"[C07-SRC] DataFrame sorgente: {len(df)} righe distinte")
    return df


def _load_project_conflict_dataframe(
    request: Any,
    current_user: Any,
    *,
    project_visible_checker: Callable[[str, Any], None],
    source_loader_cls: Callable[[], Any],
    source_ref_cls: Callable[..., Any],
    latest_project_artifact_ref_cls: Callable[..., Any],
    logger: Any,
) -> ConflictInput:
    project_visible_checker(request.project_id, current_user)
    # La porta unica (runtime_identity U1/U6): un progetto con «Utente che
    # esegue» (anche a monte) non ha un risultato salvato filtrato: nessun
    # controllo sul risultato di prima del filtro. Il suo Carica si blocca
    # alla stessa porta, nel worker, prima di leggere o ricalcolare
    # (``workers/tasks/carica_task.execute_carica_task``).
    try:
        route_upstream_source(
            source_project_id=str(request.project_id),
            need=source_identity_need(str(request.project_id), group_id=current_user.group_id),
            isolated_upstreams=None,
        )
    except RuntimeIdentityError as exc:
        return ConflictInput(early_response={
            "has_conflicts": False, "total_conflicts": 0, "internal_duplicates": 0,
            "db_conflicts": 0, "details": [], "message": exc.message,
        })

    try:
        df = source_loader_cls().load(
            source_ref_cls(
                project=latest_project_artifact_ref_cls(project_id=request.project_id)
            ),
            expected_group_id=current_user.group_id,
        )
    except SourceLoadingError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if df is None:
        return ConflictInput(
            early_response={
                "has_conflicts": False,
                "total_conflicts": 0,
                "internal_duplicates": 0,
                "db_conflicts": 0,
                "details": [],
                "message": "Nessun output disponibile. Esegui prima la pipeline.",
            }
        )

    logger.info(
        f"[C07] match_columns={request.match_columns} "
        f"df_match_columns={request.df_match_columns}"
    )
    df_cols = request.df_match_columns or request.match_columns
    available_match = [col for col in df_cols if col in df.columns]
    if not available_match:
        return ConflictInput(dataframe=dataframe_from_rows([], request.match_columns))

    col_rename = {}
    if request.df_match_columns:
        for db_col, df_col in zip(request.match_columns, request.df_match_columns):
            if df_col != db_col and df_col in df.columns:
                col_rename[df_col] = db_col
    df = select_and_rename_columns(df, available_match, rename_map=col_rename)
    return ConflictInput(dataframe=df)
