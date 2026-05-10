"""Project-source runtime loading for ManipolaExecutor."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import threading
import uuid as _uuid

from app.core.intermediate.run_lifecycle import (
    close_run as close_intermediate_run,
    create_run as create_intermediate_run,
    get_default_runs_retention,
)
from app.core.projects.execution_context import get_upstream_run_context
from app.core.projects.lineage import ProjectLineageCycleError
from app.shared.execution.executor_protocol import PipelineDispatcherProtocol
from app.core.files.dataframe_io import find_latest_output_file
from app.core.artifacts import (
    ArtifactResolver,
    ArtifactResolutionError,
    LatestProjectArtifactRef,
    MaterializedArtifact,
)
from app.utils.logger import close_run, get_logger, open_run

from .source_loading import load_project_source_artifact


logger = get_logger(__name__)


# Runtime cross-project cycle guard. The chain is thread-local because worker
# processes/threads can execute unrelated project graphs concurrently.
_manipola_execution_chain = threading.local()


def _get_execution_chain() -> List[str]:
    if not hasattr(_manipola_execution_chain, "projects"):
        _manipola_execution_chain.projects = []
    return _manipola_execution_chain.projects


def _push_to_execution_chain(project_id: str) -> None:
    _get_execution_chain().append(project_id)


def _pop_from_execution_chain(project_id: str) -> None:
    chain = _get_execution_chain()
    if chain and chain[-1] == project_id:
        chain.pop()


def _assert_no_lineage_cycle_on_chain(source_project_id: str) -> None:
    """Raise when recursive upstream execution would close a project cycle."""
    chain = _get_execution_chain()
    if source_project_id in chain:
        cycle_chain = chain[chain.index(source_project_id):] + [source_project_id]
        raise ProjectLineageCycleError(cycle_chain)


def _node_field(node: Any, key: str, default: Any = None) -> Any:
    field_value = getattr(node, key, None)
    if field_value is not None and field_value != [] and field_value != {}:
        return field_value
    data = getattr(node, "data", None)
    if data and isinstance(data, dict):
        return data.get(key, default)
    return default


def _cross_tenant_error(source_project_id: str) -> Dict[str, Any]:
    return {
        "success": False,
        "message": (
            f"Accesso negato: il progetto sorgente "
            f"'{source_project_id}' appartiene a un gruppo diverso."
        ),
    }


def _open_upstream_run(
    source_project_id: str,
    log,
) -> tuple[Optional[str], Optional[int]]:
    upstream_run_id = str(_uuid.uuid4())
    try:
        upstream_context = get_upstream_run_context(
            source_project_id,
            default_retention_n=get_default_runs_retention(),
        )
        upstream_group_id = upstream_context.group_id
        open_run(
            run_id=upstream_run_id,
            project_id=source_project_id,
            group_id=upstream_group_id,
            module=upstream_context.project_type,
            triggered_by="cascade",
        )
        create_intermediate_run(
            run_id=upstream_run_id,
            canvas_id=source_project_id,
            group_id=upstream_group_id,
        )
        return upstream_run_id, upstream_context.retention_n
    except Exception as exc:
        log.warning(
            f"Apertura run upstream {source_project_id} fallita "
            f"(non bloccante): {exc}"
        )
        return None, None


def _close_upstream_run(upstream_run_id: Optional[str], upstream, retention_n, log) -> None:
    if not upstream_run_id:
        return
    try:
        status = "success" if upstream.success else "failure"
        close_run(
            run_id=upstream_run_id,
            status=status,
            rows_out=upstream.rows_count or 0,
            duration_ms=0,
            steps=[],
            error_message=upstream.message if not upstream.success else None,
        )
        close_intermediate_run(
            run_id=upstream_run_id,
            input_hash="",
            output_hash="",
            total_exec_time=0.0,
            success=upstream.success,
            retention_n=retention_n,
        )
    except Exception as exc:
        source_project_id = getattr(upstream, "project_id", "<unknown>")
        log.warning(
            f"Chiusura run upstream {source_project_id} fallita "
            f"(non bloccante): {exc}"
        )


def _load_materialized_project_source(
    materialized,
    node: Any,
    source_project_id: str,
    group_id: str,
    dataframes: Dict[str, Any],
) -> None:
    node_columns = _node_field(node, "columns", [])
    with materialized as m:
        loaded = load_project_source_artifact(
            m.path,
            node_columns,
            source_project_id=source_project_id,
            group_id=group_id,
        )
    dataframes[node.id] = loaded.data


def load_project_sources(
    canvas: Any,
    dataframes: Dict[str, Any],
    *,
    force_recalculate: bool,
    pipeline_dispatcher: Optional[PipelineDispatcherProtocol],
    results_base_dir: Path,
    run_id: Optional[str] = None,
    group_id: Optional[str] = None,
    logger=None,
) -> Optional[Dict[str, Any]]:
    """Load ``project_source`` nodes, recomputing upstream projects as needed."""
    del run_id  # The upstream recompute path opens its own cascade run id.
    log = logger or globals()["logger"]
    source_nodes = [n for n in canvas.nodes if n.type == "project_source"]

    executing_group_id = group_id
    if not executing_group_id:
        log.error(
            "[SECURITY] manipola._carica_sorgenti_progetto: "
            "group_id esplicito mancante (ctx-strict), fail-closed"
        )
        return {
            "success": False,
            "message": "Contesto di sicurezza non determinabile (group_id mancante)",
        }

    for node in source_nodes:
        source_project_id = _node_field(node, "source_project_id")
        if not source_project_id:
            log.warning(f"Nodo {node.id}: source_project_id mancante")
            continue

        if not force_recalculate:
            try:
                materialized = ArtifactResolver().materialize_latest(
                    LatestProjectArtifactRef(project_id=source_project_id),
                    expected_group_id=executing_group_id,
                )
            except ArtifactResolutionError as sec_err:
                log.error(
                    f"[SECURITY] Cross-tenant access blocked: "
                    f"progetto sorgente {source_project_id} non risolvibile "
                    f"per group={executing_group_id}: {sec_err}",
                    op="execute",
                )
                return _cross_tenant_error(source_project_id)

            if materialized is None:
                output_dir = results_base_dir / source_project_id
                fs_file = find_latest_output_file(output_dir)
                if fs_file:
                    log.warning(
                        f"[COMPAT-RESIDUE] manipola._carica_sorgenti_progetto: "
                        f"filesystem fallback scattato per "
                        f"source_project={source_project_id} file={fs_file} - "
                        f"progetto upstream senza blob DB, pre-migrazione"
                    )
                    materialized = MaterializedArtifact(
                        path=Path(fs_file),
                        is_temporary=False,
                    )

            if materialized is not None:
                try:
                    _load_materialized_project_source(
                        materialized,
                        node,
                        source_project_id,
                        executing_group_id,
                        dataframes,
                    )
                    continue
                except Exception as exc:
                    log.warning(f"Errore cache: {exc}, procedo con ricalcolo")

        if not pipeline_dispatcher:
            return {"success": False, "message": "Pipeline dispatcher non configurato"}

        _assert_no_lineage_cycle_on_chain(source_project_id)

        upstream_run_id, upstream_retention_n = _open_upstream_run(
            source_project_id,
            log,
        )

        upstream = pipeline_dispatcher(
            source_project_id,
            force_recalculate=True,
            run_id=upstream_run_id,
        )
        _close_upstream_run(upstream_run_id, upstream, upstream_retention_n, log)

        if not upstream.success:
            return {
                "success": False,
                "message": (
                    f"Errore caricamento sorgente upstream "
                    f"'{source_project_id}': "
                    f"{upstream.message or 'errore non specificato'}"
                ),
            }

        materialized = None
        raw_output_path = upstream.output_path or ""
        if raw_output_path and Path(raw_output_path).exists():
            materialized = MaterializedArtifact(
                path=Path(raw_output_path),
                is_temporary=False,
            )
        if materialized is None:
            try:
                materialized = ArtifactResolver().materialize_latest(
                    LatestProjectArtifactRef(project_id=source_project_id),
                    expected_group_id=executing_group_id,
                )
            except ArtifactResolutionError as sec_err:
                log.error(
                    f"[SECURITY] Cross-tenant access blocked post-recompute: "
                    f"{source_project_id} per group={executing_group_id}: {sec_err}",
                    op="execute",
                )
                return _cross_tenant_error(source_project_id)

        if materialized is None or materialized.path is None:
            return {
                "success": False,
                "message": f"Risultato upstream non trovato per: {source_project_id}",
            }

        try:
            _load_materialized_project_source(
                materialized,
                node,
                source_project_id,
                executing_group_id,
                dataframes,
            )
        except Exception as exc:
            return {
                "success": False,
                "message": f"Errore progetto {source_project_id}: {exc}",
            }

    return None
