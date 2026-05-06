"""Manipola source-loading helpers — extraction slice.

BE-GO-READY-TRANSFORMATION-ENGINE-006-MANIPOLA-SOURCE-LOADING-SLICE.

This module owns the **file-source** loading chain previously
inlined in ``ManipolaExecutor._carica_sorgenti_file``. The
extraction is behaviour-preserving:

  - same ``SourceLoader.load`` call shape;
  - same fail-closed dict shape (``{"success": False,
    "message": ...}``) with byte-for-byte the same operator
    strings;
  - same CRS upgrade via ``_applica_crs_da_colonne``;
  - same node-iteration pattern with ``_nf`` extraction.

The only behaviour the function is NOT given access to is the
legacy singleton ``self._current_group_id``: post
``TRANSFORMATION-ENGINE-005`` the file chain is ``ctx-strict``
and the caller MUST pass an explicit ``group_id``. A stale
singleton from a previous run cannot reach a visibility check
through this helper (the executor delegator no longer consults
it for this chain — see
``app/core/manipola/executor.py::ManipolaExecutor._carica_sorgenti_file``).

Boundary
--------
Pure function module: no class, no global state, no side effect
beyond mutating the caller-owned ``dataframes`` dict and emitting
log lines through the optional ``logger`` argument. Imports are
explicit; nothing reaches into ``ManipolaExecutor`` at module-
load time (the small cross-chain helpers ``_nf`` and
``_applica_crs_da_colonne`` are imported lazily inside the
function body to avoid an executor ↔ source_loading import
cycle).

Out of scope (per ``TE-006`` brief):

  - the project-source chain (``_carica_sorgenti_progetto``)
    and the static-values chain (``_carica_sorgenti_statiche``)
    stay in ``executor.py`` for now; their extraction is
    deferred to follow-up streams.
  - join / groupby / aggregation logic (``_applica_trasformazioni``,
    plugin executor) — explicitly out of this slice.

Public surface
--------------
::

    load_file_sources(
        canvas,
        dataframes,
        *,
        group_id: Optional[str],
        logger=None,
    ) -> Optional[Dict[str, Any]]

Returns ``None`` on success (the caller-owned ``dataframes``
dict has been populated in place); returns the canonical
fail-closed dict on tenant-context error or per-node loader
failure.
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

    Behaviour-preserving extraction of the body previously
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

    # Lazy imports to avoid an executor ↔ source_loading cycle:
    # ``executor.py`` imports this module at top-level for the
    # delegator; reaching back into it here happens only at call
    # time, after ``executor.py`` is fully initialized.
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
    # singleton fallback (TE-005 contract); see module docstring.
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

        # Normalise columns from the canvas node before the load call so
        # alias-resolved names can be forwarded to load_with_metadata as
        # columns_metadata.  ResourceMetadata construction is delegated to
        # SourceLoader.load_with_metadata (BE-GO-READY-ARCH-058-W1 API);
        # the metadata travels out in LoadedResource.metadata rather than
        # being reconstructed post-load from node_columns.
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
            # format ("Accesso negato: il file sorgente '...'"),
            # propagated as-is in the error dict.
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"Errore file '{fid}': {e}"}

        data = loaded.data
        meta = loaded.metadata  # ResourceMetadata built by SourceLoader; None if no columns

        data = filter_visible_columns(data, node_columns_normalized)
        # Preserve CRS: SourceLoader returns a GeoDataFrame when
        # ``file_source_service.load_dataframe`` produced one,
        # but for files where the CRS is reconstructed from the
        # node metadata (geometry:subtype:SRID in the column
        # definitions) the DataFrame -> GeoDataFrame upgrade
        # still has to run here. Mirrors the project_source
        # branch. Idempotent: no-op if ``data`` is already a
        # GeoDataFrame.
        data = _applica_crs_da_colonne(data, node_columns, meta=meta)
        if metadata_map is not None:
            metadata_map[node.id] = meta
        dataframes[node.id] = data

    return None
