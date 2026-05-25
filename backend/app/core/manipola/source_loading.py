"""
Manipola source-loading helpers — separation slice.

"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from app.core.dataframes.frame_io import (
    filter_visible_columns,
    load_dataframe_from_artifact,
    rename_duplicate_columns,
)
from app.core.output_contract import ensure_dataframe_matches_contract
from app.core.manipola.types import ColumnMetadataRows, FrameMap, JsonObject, JsonRows
from app.utils.logger import get_logger
from app.shared.resources.metadata import LoadedResource, ResourceMetadata


__all__ = [
    "load_file_sources",
    "load_project_source_artifact",
    "normalize_node_columns",
]

logger = get_logger(__name__)


_DEFAULT_LOGGER = get_logger(__name__)


def normalize_node_columns(node_columns: Sequence[Any] | None) -> list[dict[str, Any]]:
    """Normalizza una lista di node columns in dict omogenei.

    Pre-fix: lo stesso for-loop `if isinstance(c, dict): append(c) else
    append({name/alias/type/visible})` era duplicato in
    `source_loading.py::load_file_sources` e
    `datasource_node_loading.py::_load_datasource_node`. Estratto qui
    come single source of truth: i caller passano la lista raw dal
    canvas e ottengono dict normalizzati da consegnare a
    `filter_visible_columns` / `_metadata_columns_from_node_columns`.
    """
    out: list[dict[str, Any]] = []
    for c in node_columns or []:
        if isinstance(c, dict):
            out.append(c)
        else:
            out.append({
                "name": getattr(c, "name", ""),
                "alias": getattr(c, "alias", None),
                "type": getattr(c, "type", ""),
                "visible": getattr(c, "visible", True),
            })
    return out


def _metadata_columns_from_node_columns(
    node_columns: Sequence[Any] | None,
) -> ColumnMetadataRows:
    columns: ColumnMetadataRows = []
    for col in node_columns or []:
        if isinstance(col, dict):
            name = col.get("alias") or col.get("name", "")
            col_type = col.get("type", "")
        else:
            name = getattr(col, "alias", None) or getattr(col, "name", "")
            col_type = getattr(col, "type", "")
        if name:
            columns.append({"name": str(name), "type": str(col_type or "")})
    return columns


def load_project_source_artifact(
    path: Path,
    node_columns: JsonRows | None,
    *,
    source_project_id: str,
    group_id: str,
    format_hint: str | None = None,
) -> LoadedResource:
    """Load one materialized ``project_source`` artifact with metadata.

    The returned ``LoadedResource`` keeps the DataFrame paired with the
    ``ResourceMetadata`` derived at the loader boundary. This preserves the
    legacy DataFrame/CRS behavior while avoiding a second metadata guess in
    ``_applica_crs_da_colonne``.

    Disk dependency is intentionally contained in this adapter because
    pandas/geopandas loaders still require path-like inputs for some formats.
    Callers pass resource references; this boundary owns the final
    materialization step.
    """
    from app.core.manipola.executor import _applica_crs_da_colonne

    df = load_dataframe_from_artifact(path=path, format_hint=format_hint)
    # Pre-fix (P2 etl-sources): `ensure_dataframe_matches_contract` con
    # `node_columns=[]` produce `visible_names=[]` → nessuna colonna
    # mancante → check passa indipendentemente dal contenuto del DF.
    # Subito dopo `filter_visible_columns(df, [])` ritorna un DF a 0
    # colonne, propagato silenziosamente alla pipeline. Diagnostica
    # zero. Post-fix: warning esplicito al boundary per attirare
    # l'attenzione nei log durante il debug di run "vuote".
    if not node_columns:
        _DEFAULT_LOGGER.warning(
            "load_project_source_artifact: node_columns vuoto per "
            "project_source %s — filter_visible_columns produrra' un "
            "DataFrame a 0 colonne. Probabile canvas senza colonne "
            "selezionate sul nodo.",
            source_project_id,
        )
    ensure_dataframe_matches_contract(
        dataframe_columns=df.columns,
        node_columns=node_columns or [],
        source_project_id=source_project_id,
        dataframe=df,
    )
    data = filter_visible_columns(df, node_columns)
    data, renamed_columns = rename_duplicate_columns(data)
    if renamed_columns:
        _DEFAULT_LOGGER.warning(
            "project_source con colonne duplicate normalizzate: "
            + ", ".join(f"{old}->{new}" for old, new in renamed_columns),
        )
    metadata = ResourceMetadata.from_columns_list(
        _metadata_columns_from_node_columns(node_columns),
        source_kind="project",
        row_count=len(data),
        group_id=group_id,
        project_id=source_project_id,
    )
    data = _applica_crs_da_colonne(data, node_columns, meta=metadata)
    return LoadedResource(data=data, metadata=metadata)


def load_file_sources(
    canvas,
    dataframes: FrameMap,
    *,
    group_id: Optional[str],
    logger=None,
    metadata_map: Optional[FrameMap] = None,
) -> Optional[JsonObject]:
    """Carica DataFrame dai nodi ``file_source`` via ``SourceLoader``.

    Behaviour-preserving separation of the body previously
    inlined in ``ManipolaExecutor._carica_sorgenti_file``.

    Args:
        canvas: the canvas whose ``file_source`` nodes are
            iterated. Read-only.
        dataframes: the caller-owned dict that receives the
            loaded DataFrame per node.id. **Mutated in place.**
        group_id: explicit tenant binding. MUST be a non-empty
            ``str``; ``None`` / empty triggers the canonical
            fail-closed dict. NO fallback to any legacy
            singleton — the TE-005 contract is enforced here.
        logger: optional logger; defaults to a module-level
            logger so test harnesses can inject a captured
            logger if needed.
        metadata_map: optional caller-owned dict that receives
            the ``ResourceMetadata`` produced for each file node
            (keyed by ``node.id``). Populated when provided;
            ignored when ``None``. Lets callers capture schema/
            CRS metadata at load time rather than reconstructing
            it later from legacy dicts.

    Returns:
        ``None`` on success (``dataframes`` populated in place).
        ``{"success": False, "message": "..."}`` on tenant
        context error, visibility block, or per-node loader
        failure (the operator-visible message strings are
        preserved verbatim from the pre-006 inline body).
    """
    log = logger or _DEFAULT_LOGGER

    file_nodes = [n for n in canvas.nodes if n.type == "file_source"]
    if not file_nodes:
        return None

    # Lazy imports to avoid an executor ↔ source_loading cycle
    # ``executor.py`` imports this module at top-level for the
    # delegator; reaching back into it here happens only at call
    # time, after ``executor.py`` is fully initialized
    from app.core.artifacts import (
        SourceLoader,
        SourceLoadingError,
        SourceRef,
    )
    from app.core.manipola.executor import (
        _applica_crs_da_colonne,
        _nf,
    )

    # Tenant binding ctx-strict: explicit ``group_id`` only. No
    # singleton fallback (TE-005 contract); see module docstring
    if not group_id:
        log.error(
            "[SECURITY] manipola.load_file_sources: explicit "
            "group_id required (post TRANSFORMATION-ENGINE-005, "
            "no singleton fallback), fail-closed"
        )
        return {
            "success": False,
            "message": "Contesto di sicurezza non determinabile",
        }

    loader = SourceLoader()

    for node in file_nodes:
        fid = _nf(node, 'file_source_id')
        if not fid:
            # Pre-fix: silent skip su file_source senza file_source_id.
            # Sintomo utente: canvas con node file_source non collegato
            # produce "Nessun dato sorgente" generico, senza indicazione
            # che il nodo era misconfigurato. Warn esplicito facilita
            # il triage operativo.
            node_id = _nf(node, 'id', '<sconosciuto>')
            # Pre-fix (P1 etl-sources): `logger.warning` invece di
            # `log.warning`. Il logger iniettato (`logger=` kwarg, alias
            # `log` nella funzione) viene ignorato per questo warning,
            # rompendo i test harness con logger catturante.
            # Post-fix: `log.warning` allineato a `log.error` del catch
            # sottostante.
            log.warning(
                "Nodo file_source skippato (file_source_id mancante): "
                "node_id=%s — il canvas referenzia un file non configurato",
                node_id,
            )
            continue

        ref = SourceRef.from_node_data(
            'file_source',
            {'file_source_id': fid},
        )

        node_columns_normalized = normalize_node_columns(_nf(node, 'columns', []))
        _meta_input = _metadata_columns_from_node_columns(node_columns_normalized)

        try:
            loaded = loader.load_with_metadata(
                ref,
                expected_group_id=group_id,
                resource_columns=_meta_input,
            )
        except SourceLoadingError as e:
            # Visibility block or materialization error: the
            # loader already produces a message in the expected
            # format ("Accesso negato: il file sorgente '...'"
            # propagated as-is in the error dict
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"Errore file '{fid}': {e}"}

        data = loaded.data
        meta = loaded.metadata  # ResourceMetadata built by SourceLoader; None if no columns

        data = filter_visible_columns(data, node_columns_normalized)
        # Preserve CRS: SourceLoader returns a GeoDataFrame when
        # ``file_source_service.load_dataframe`` produced one
        # but for files where the CRS is reconstructed from the
        # node metadata (geometry:subtype:SRID in the column
        # definitions) the DataFrame -> GeoDataFrame upgrade
        # still has to run here. Mirrors the project_source
        # branch. Idempotent: no-op if ``data`` is already a
        # GeoDataFrame
        data = _applica_crs_da_colonne(data, node_columns, meta=meta)
        if metadata_map is not None:
            metadata_map[node.id] = meta
        dataframes[node.id] = data

    return None
