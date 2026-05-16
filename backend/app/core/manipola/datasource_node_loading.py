"""MANIPOLA loader for datasource nodes used as file sources."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.core.auth.visibility import is_resource_visible
from app.core.datasources.service import datasource_service
from app.core.dataframes.frame_io import (
    filter_visible_columns,
    load_dataframe_from_file,
)
from app.core.manipola.types import FrameMap, JsonObject
from app.core.results.temp_paths import (
    cleanup_path_only_adapter,
    write_path_only_adapter_bytes,
)
from app.utils.logger import get_logger


__all__ = ["load_datasource_nodes"]


_DEFAULT_LOGGER = get_logger(__name__)


_FORMAT_SUFFIX = {
    "csv": ".csv",
    "xlsx": ".xlsx",
    "xls": ".xls",
    "json": ".json",
    "geojson": ".geojson",
    "gpkg": ".gpkg",
    "shp": ".shp",
    "gdb": ".gdb",
    "parquet": ".parquet",
}


def _nf(node, key, default=None):
    """Read a node attribute from the top-level field, falling back to ``data``."""
    val = getattr(node, key, None)
    if val is not None:
        return val
    data = getattr(node, "data", None) or {}
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def _suffix_for(node, file_path: str) -> str:
    """Pick a tempfile suffix that ``load_dataframe_from_file`` recognises."""
    fmt = (_nf(node, "format") or "").strip().lower()
    if fmt and fmt in _FORMAT_SUFFIX:
        return _FORMAT_SUFFIX[fmt]
    suffix = Path(file_path).suffix.lower() if file_path else ""
    return suffix


def load_datasource_nodes(
    canvas,
    dataframes: FrameMap,
    *,
    group_id: Optional[str],
    logger=None,
) -> Optional[JsonObject]:
    """Load source-mode datasource nodes into ``dataframes``."""
    log = logger or _DEFAULT_LOGGER

    nodes = [
        n
        for n in canvas.nodes
        if getattr(n, "type", None) == "datasource_node"
        and (_nf(n, "mode") or "source") == "source"
    ]
    if not nodes:
        return None

    if not group_id:
        log.error(
            "[SECURITY] manipola.load_datasource_nodes: explicit "
            "group_id required (no singleton fallback), fail-closed"
        )
        return {
            "success": False,
            "message": "Contesto di sicurezza non determinabile",
        }

    for node in nodes:
        ds_id = _nf(node, "datasource_id")
        if not ds_id:
            return {
                "success": False,
                "message": (
                    f"Nodo '{getattr(node, 'id', '?')}' senza datasource_id"
                ),
            }

        file_path = _nf(node, "file_path")
        if not file_path:
            return {
                "success": False,
                "message": (
                    f"Nodo '{getattr(node, 'id', '?')}' senza file_path "
                    "(modalita' source richiede un file remoto specifico)"
                ),
            }

        # Visibility: owned-by-group OR shared via datasource_shares.
        # Mirrors the HTTP `assert_resource_visible` policy used by
        # `GET /datasources/{id}/files/download` so a canvas cannot
        # exfiltrate a datasource the project's group cannot read.
        try:
            visible = is_resource_visible(
                "datasources",
                ds_id,
                group_id,
                is_superadmin=False,
            )
        except Exception as e:
            log.error(
                f"[GESTITO] visibility check fallito su datasource {ds_id}: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            return {
                "success": False,
                "message": "Errore controllo visibilita' datasource",
            }
        if not visible:
            return {
                "success": False,
                "message": (
                    f"Accesso negato: datasource '{ds_id}' non visibile "
                    "al gruppo del progetto"
                ),
            }

        # Read remote bytes via the canonical service. The backend
        # connect/disconnect lifecycle and credential decryption live
        # there — no FTP/SFTP/SharePoint logic leaks into this loader.
        try:
            content = datasource_service.read_file(ds_id, file_path)
        except Exception as e:
            log.error(
                f"[GESTITO] datasource_node read_file fallito: ds={ds_id} "
                f"path={file_path!r} {type(e).__name__}: {e}",
                exc_info=True,
            )
            return {
                "success": False,
                "message": (
                    f"Errore lettura file remoto '{file_path}': {e}"
                ),
            }

        # Materialize at the path-only adapter boundary so
        # `load_dataframe_from_file` can pick the parser by extension.
        suffix = _suffix_for(node, file_path)
        try:
            tmp_path = write_path_only_adapter_bytes(content, suffix=suffix)
            parse_options = _nf(node, "parse_options") or {}
            try:
                df = load_dataframe_from_file(
                    tmp_path,
                    sheet=parse_options.get("sheet"),
                    layer=parse_options.get("layer"),
                )
            finally:
                if not cleanup_path_only_adapter(tmp_path):
                    log.warning(
                        f"Cleanup path-only adapter {tmp_path} fallito"
                    )
        except Exception as e:
            log.error(
                f"[GESTITO] datasource_node parse fallito: ds={ds_id} "
                f"path={file_path!r} suffix={suffix!r} "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            return {
                "success": False,
                "message": f"Errore parsing file '{file_path}': {e}",
            }

        # Match `load_file_sources`: the node-level columns list
        # drives column visibility / ordering downstream.
        node_columns = _nf(node, "columns", []) or []
        node_columns_normalized = []
        for c in node_columns:
            if isinstance(c, dict):
                node_columns_normalized.append(c)
            else:
                node_columns_normalized.append(
                    {
                        "name": getattr(c, "name", ""),
                        "alias": getattr(c, "alias", None),
                        "type": getattr(c, "type", ""),
                        "visible": getattr(c, "visible", True),
                    }
                )
        df = filter_visible_columns(df, node_columns_normalized)

        dataframes[node.id] = df
        log.info(
            f"datasource_node caricato: node_id={node.id} ds={ds_id} "
            f"path={file_path!r} rows={len(df)} cols={len(df.columns)}"
        )

    return None
