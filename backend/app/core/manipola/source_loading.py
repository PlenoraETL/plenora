"""
Manipola source-loading helpers — separation slice.

"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.utils.dataframe import filter_visible_columns
from app.utils.logger import get_logger


__all__ = ["load_file_sources"]


_DEFAULT_LOGGER = get_logger(__name__)


def load_file_sources(
    canvas,
    dataframes: Dict[str, Any],
    *,
    group_id: Optional[str],
    logger=None,
    metadata_map: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
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
            continue

        ref = SourceRef.from_node_data(
            'file_source',
            {'file_source_id': fid},
        )

        node_columns = _nf(node, 'columns', [])
        node_columns_normalized = []
        for c in (node_columns or []):
            if isinstance(c, dict):
                node_columns_normalized.append(c)
            else:
                node_columns_normalized.append({
                    "name": getattr(c, "name", ""),
                    "alias": getattr(c, "alias", None),
                    "type": getattr(c, "type", ""),
                    "visible": getattr(c, "visible", True),
                })
        _meta_input = [
            {"name": c.get("alias") or c.get("name", ""), "type": c.get("type", "")}
            for c in node_columns_normalized
            if c.get("alias") or c.get("name")
        ]

        try:
            loaded = loader.load_with_metadata(
                ref,
                expected_group_id=group_id,
                columns_metadata=_meta_input,
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
