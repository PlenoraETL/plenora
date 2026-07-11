"""Project-source runtime loading for ManipolaExecutor."""

from __future__ import annotations

from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any, List, Optional
import uuid as _uuid

from app.core.intermediate.run_lifecycle import (
    close_run as close_intermediate_run,
    create_run as create_intermediate_run,
    get_default_runs_retention,
)
from app.core.projects.execution_context import get_upstream_run_context
from app.core.projects.lineage import ProjectLineageCycleError
from app.core.projects.source_freshness import get_project_source_freshness
from app.core.resources.memory_guard import cleanup_worker_memory
from app.shared.execution.executor_protocol import PipelineDispatcherProtocol
from app.core.artifacts import (
    ArtifactResolver,
    ArtifactResolutionError,
    LatestProjectArtifactRef,
    MaterializedArtifact,
    RunArtifactRef,
)
from app.utils.logger import close_run, get_logger, open_run

from .source_loading import load_project_source_artifact
from .types import FrameMap, JsonObject


logger = get_logger(__name__)


# Runtime cross-project cycle guard. ContextVar keeps unrelated async/task
# contexts isolated without tying the state to a physical thread.
_manipola_execution_chain: ContextVar[tuple[str, ...]] = ContextVar(
    "manipola_execution_chain",
    default=(),
)


def _get_execution_chain() -> List[str]:
    return list(_manipola_execution_chain.get())


def _push_to_execution_chain(project_id: str) -> None:
    chain = _manipola_execution_chain.get()
    _manipola_execution_chain.set((*chain, project_id))


def _pop_from_execution_chain(project_id: str) -> None:
    chain = _manipola_execution_chain.get()
    if chain and chain[-1] == project_id:
        _manipola_execution_chain.set(chain[:-1])


def _materialize_latest_project_source(
    source_project_id: str,
    executing_group_id: str,
) -> MaterializedArtifact:
    resolver = ArtifactResolver()
    ref = LatestProjectArtifactRef(project_id=source_project_id)
    try:
        return resolver.materialize_latest(
            ref,
            expected_group_id=executing_group_id,
            mode="bytes",
        )
    except TypeError as exc:
        if "mode" not in str(exc):
            raise
        return resolver.materialize_latest(
            ref,
            expected_group_id=executing_group_id,
        )


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


def _cross_tenant_error(source_project_id: str) -> JsonObject:
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
    dataframes: FrameMap,
) -> None:
    node_columns = _node_field(node, "columns", [])
    from app.core.projects.service import ProjectService
    from app.core.output_contract import node_columns_from_contract

    output_contract = ProjectService().get_project_output_contract(source_project_id)
    if not output_contract:
        raise ValueError(
            f"output_contract mancante per progetto sorgente {source_project_id}"
        )
    node_columns = node_columns_from_contract(
        output_contract,
        previous_node_columns=node_columns,
    )
    with materialized as m:
        loaded = load_project_source_artifact(
            node_columns,
            source_project_id=source_project_id,
            group_id=group_id,
            format_hint=m.format,
            data=m.data,
            path=m.path,
        )
    dataframes[node.id] = loaded.data


def load_project_sources(
    canvas: Any,
    dataframes: FrameMap,
    *,
    force_recalculate: bool,
    pipeline_dispatcher: Optional[PipelineDispatcherProtocol],
    run_id: Optional[str] = None,
    group_id: Optional[str] = None,
    logger=None,
) -> Optional[JsonObject]:
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

        freshness = get_project_source_freshness(
            source_project_id,
            expected_group_id=executing_group_id,
        )
        if freshness and freshness.stale and not force_recalculate:
            return {
                "success": False,
                "message": freshness.message,
            }

        if not force_recalculate:
            try:
                # mode="bytes": `_load_materialized_project_source` accetta
                # data direttamente, niente tempfile bounce.
                materialized = _materialize_latest_project_source(
                    source_project_id,
                    executing_group_id,
                )
            except ArtifactResolutionError as sec_err:
                log.error(
                    f"[SECURITY] Cross-tenant access blocked: "
                    f"progetto sorgente {source_project_id} non risolvibile "
                    f"per group={executing_group_id}: {sec_err}",
                    op="execute",
                )
                return _cross_tenant_error(source_project_id)

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
                except FileNotFoundError as exc:
                    # Cache miss semanticamente atteso: artifact non
                    # ancora prodotto / scaduto / cleanup. No warning,
                    # fallback canonico al ricalcolo via pipeline.
                    log.info(
                        "Cache miss artifact upstream (FileNotFound): "
                        "%s, ricalcolo via pipeline_dispatcher",
                        exc,
                    )
                except ArtifactResolutionError as exc:
                    # Errore tipizzato del resolver (cross-tenant violation,
                    # checksum mismatch, manifest corrotto). Visibile in log
                    # con livello WARNING perche' potenzialmente indica un
                    # problema di integrita' che andra' investigato anche
                    # se il ricalcolo nasconde l'effetto immediato.
                    log.warning(
                        "ArtifactResolutionError su upstream cache: %s, "
                        "fallback a ricalcolo via pipeline_dispatcher",
                        exc,
                    )
                except Exception as exc:
                    # Eccezione non classificata: log ERROR + exc_info per
                    # postmortem. Il ricalcolo procede ma il bug e' visibile.
                    log.error(
                        "Errore non gestito su cache lookup upstream: %s: %s",
                        type(exc).__name__, exc,
                        exc_info=True,
                    )

        if not pipeline_dispatcher:
            return {"success": False, "message": "Pipeline dispatcher non configurato"}

        _assert_no_lineage_cycle_on_chain(source_project_id)

        upstream_run_id, upstream_retention_n = _open_upstream_run(
            source_project_id,
            log,
        )

        # try/finally garantisce che _close_upstream_run venga chiamata anche
        # se pipeline_dispatcher solleva eccezione.
        upstream: Any = None
        try:
            upstream = pipeline_dispatcher(
                source_project_id,
                force_recalculate=True,
                run_id=upstream_run_id,
            )
        finally:
            if upstream is None:
                # pipeline_dispatcher ha sollevato; sentinel di failure
                # cosi' _close_upstream_run logga 'failure' invece di
                # accedere ad attributi su None.
                upstream = SimpleNamespace(
                    success=False,
                    message="Eccezione non gestita in pipeline_dispatcher",
                    rows_count=0,
                    output_path=None,
                )
            _close_upstream_run(
                upstream_run_id, upstream, upstream_retention_n, log,
            )

        # L'upstream puo' aver eseguito query/streaming pesanti nello stesso
        # processo. Il risultato e' ormai persistito: libera le sue arena
        # prima di materializzarlo nel DataFrame del progetto MANIPOLA.
        cleanup_stats = cleanup_worker_memory()
        log.info(
            "[UPSTREAM-PRE-LOAD-TRIM] project=%s rss=%s -> %s bytes "
            "gc=%s trim=%s",
            source_project_id,
            cleanup_stats.get("before_rss"),
            cleanup_stats.get("after_rss"),
            cleanup_stats.get("collected"),
            cleanup_stats.get("malloc_trim"),
        )

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
        run_ref = None
        if hasattr(upstream, "to_run_artifact_ref"):
            try:
                run_ref = upstream.to_run_artifact_ref()
            except ValueError:
                run_ref = None

        if run_ref is None:
            raw_output_path = upstream.output_path or ""
            if raw_output_path:
                run_ref = RunArtifactRef(
                    project_id=source_project_id,
                    output_path=raw_output_path,
                )

        if run_ref is not None:
            try:
                materialized = ArtifactResolver().materialize_run(
                    run_ref,
                    mode="bytes",
                )
            except ArtifactResolutionError as resolve_err:
                log.warning(
                    f"artifact upstream rifiutato o non disponibile: "
                    f"{resolve_err}; fallback a materialize_latest.",
                    op="execute",
                )
                materialized = None
        if materialized is None:
            try:
                materialized = _materialize_latest_project_source(
                    source_project_id,
                    executing_group_id,
                )
            except ArtifactResolutionError as sec_err:
                log.error(
                    f"[SECURITY] Cross-tenant access blocked post-recompute: "
                    f"{source_project_id} per group={executing_group_id}: {sec_err}",
                    op="execute",
                )
                return _cross_tenant_error(source_project_id)

        if materialized is None or (
            materialized.path is None and materialized.data is None
        ):
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
