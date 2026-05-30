"""
IMDF ArcGIS publish policy — pubblica i layer IMDF su ArcGIS Online.

Thin policy IMDF sopra il writer ArcGIS condiviso (`ArcGISFeatureWriter`): decide
quali layer, in quale ordine, con quali match-key e renderer, e delega
l'orchestrazione multi-layer al writer generico (`create_service_with_layers` /
`write_layers_to_existing_service` / `apply_layer_renderers`) + ai boundary
`connections` per folder/cleanup. Nessun loop ArcGIS qui dentro.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Optional

from app.core.connections.arcgis_connection_resolver import resolve_arcgis_connection
import app.core.connections.arcgis_service_discovery as _service_discovery
import app.core.connections.arcgis_service_lifecycle as _service_lifecycle
from app.core.connections.arcgis_writer_boundary import (
    ArcGISFeatureWriter,
    ArcGISLayerSpec,
)
from app.shared.contracts.payloads import JsonObject, StringMap
from app.utils.logger import get_logger


logger = get_logger(__name__)

# Ordine canonico dei layer IMDF dentro il Feature Service (gerarchia top-down).
LAYER_ORDER = ["Localita", "OperaPrincipale", "Livello", "Locale", "Details"]

# Colonne business-key per layer IMDF (match per upsert/update). Policy IMDF;
# in prospettiva derivabili dall'`identity` del descrittore del bundle.
DEFAULT_MATCH_COLUMNS = {
    "Localita":        "SITE_ID",
    "OperaPrincipale": "FACILITY_ID",
    "Livello":         "LEVEL_ID",
    "Locale":          "UNIT_ID",
    "Details":         "DETAIL_ID",
}

PUBLISH_MODES = ("replace", "append", "upsert", "update")


def _empty_result(mode: str, error: str) -> JsonObject:
    return {
        "success": False, "mode": mode, "service_url": "", "service_item_id": "",
        "folder_id": "", "layers": [], "renderers_applied": 0, "errors": [error],
    }


def upload_imdf_layers(
    connection_id: str,
    layers: Mapping[str, Any],
    fs_name: str,
    *,
    feature_writer: ArcGISFeatureWriter,
    folder_name: Optional[str] = None,
    apply_renderers: bool = False,
    renderers_data: Optional[JsonObject] = None,
    target_srid: int = 4326,
    progress_callback=None,
    mode: str = "replace",
    match_columns: Optional[StringMap] = None,
) -> JsonObject:
    """Pubblica i layer IMDF su un Feature Service ArcGIS via il writer condiviso.

    Args:
        connection_id: connessione ArcGIS in plenora.arcgis_connections.
        layers: dict {layer_name: GeoDataFrame} (da bundle_geo).
        fs_name: nome del Feature Service.
        feature_writer: writer ArcGIS condiviso (passato dal worker).
        folder_name: folder ArcGIS (creata se manca; root se None).
        apply_renderers / renderers_data: stile per-layer ({nome: drawing_info}).
        target_srid: SRID dei dati (4326).
        mode: 'replace' (cleanup+create) | 'append' | 'upsert' | 'update' (su FS esistente).
        match_columns: override di DEFAULT_MATCH_COLUMNS (upsert/update).

    Returns:
        {success, mode, service_url, service_item_id, folder_id, layers[],
         renderers_applied, errors[]}.
    """
    if mode not in PUBLISH_MODES:
        return _empty_result(mode, f"mode non supportata: {mode}. Validi: {PUBLISH_MODES}")

    effective_match = {**DEFAULT_MATCH_COLUMNS, **(match_columns or {})}

    def _emit(stage, current, total, message=""):
        if progress_callback is None:
            return
        try:
            progress_callback(stage, current, total, message)
        except Exception as _cb_e:  # pragma: no cover - callback best-effort
            logger.warning(f"progress_callback error: {_cb_e}")

    result: Dict[str, Any] = {
        "success": False, "mode": mode, "service_url": "", "service_item_id": "",
        "folder_id": "", "layers": [], "renderers_applied": 0, "errors": [],
    }

    resolved = resolve_arcgis_connection(connection_id)
    if resolved is None:
        result["errors"].append(f"Connessione ArcGIS '{connection_id}' non trovata")
        return result
    conn, client = resolved
    if not conn.username:
        result["errors"].append("La connection ArcGIS non ha username (richiesto per createService)")
        return result

    layers_present = [n for n in LAYER_ORDER if n in layers and len(layers[n]) > 0]
    if not layers_present:
        result["errors"].append("Nessun layer IMDF popolato da caricare")
        return result
    missing = [n for n in LAYER_ORDER if n not in layers or len(layers[n]) == 0]
    if missing:
        logger.warning(f"Layer mancanti o vuoti, saltati: {missing}")

    # Spec domain-neutral per il writer condiviso (ordine LAYER_ORDER).
    specs = [
        ArcGISLayerSpec(
            layer_name=n, df=layers[n], geometry_column="geometry",
            match_column=effective_match.get(n, ""),
        )
        for n in layers_present
    ]

    _emit("folder", 0, 3, f"Preparazione folder '{folder_name or '(root)'}'...")
    folder_id = ""
    if folder_name:
        try:
            folder_id = _service_discovery.ensure_folder(client, conn, folder_name) or ""
            result["folder_id"] = folder_id
        except Exception as e:
            result["errors"].append(f"Errore ensure_folder: {e}")
            return result

    if mode in ("append", "upsert", "update"):
        _emit("find_service", 1, 3, f"Ricerca FS esistente '{fs_name}'...")
        existing_url, existing_item_id, _ = _service_discovery.find_existing_feature_service(
            client, conn, fs_name,
        )
        if not existing_url:
            result["errors"].append(
                f"Modalita' '{mode}' richiede un FS esistente '{fs_name}' non trovato. "
                f"Usa 'replace' per la prima pubblicazione."
            )
            return result
        result["service_url"] = existing_url
        result["service_item_id"] = existing_item_id or ""
        _emit("write", 2, 3, f"{mode} su FS esistente ({len(specs)} layer)...")
        write_res = feature_writer.write_layers_to_existing_service(
            connection_id, existing_url, specs, mode, source_srid=target_srid,
        )
    else:
        # replace: cleanup esistente + check zombie, poi create multi-layer.
        _emit("cleanup", 1, 3, f"Verifica FS esistente '{fs_name}'...")
        try:
            attempted, succeeded = _service_lifecycle.delete_existing_feature_service(
                client, conn, fs_name,
            )
            if succeeded > 0:
                logger.info(f"Feature Service '{fs_name}': {succeeded}/{attempted} item cancellati")
        except Exception as e:
            attempted, succeeded = 0, 0
            logger.warning(f"delete_existing_feature_service: {e}")
        if not (attempted > 0 and attempted == succeeded):
            available, _err = _service_discovery.is_service_name_available(client, conn, fs_name)
            if not available:
                cleaned, cleanup_msg = _service_lifecycle.try_cleanup_zombie_service(
                    client, conn, fs_name,
                )
                if not cleaned:
                    available2, _ = _service_discovery.is_service_name_available(client, conn, fs_name)
                    if not available2:
                        result["errors"].append(
                            f"Nome Feature Service '{fs_name}' gia' riservato "
                            f"nell'organizzazione ma non visibile all'utente "
                            f"'{conn.username}' (visti {attempted}, rimossi {succeeded}). "
                            f"Cleanup automatico fallito: {cleanup_msg}. "
                            f"Soluzione: usa un nome diverso o contatta l'admin."
                        )
                        return result
        _emit("create", 2, 3, f"Creazione FS '{fs_name}' ({len(specs)} layer)...")
        write_res = feature_writer.create_service_with_layers(
            connection_id, fs_name, specs, source_srid=target_srid, folder_id=folder_id,
        )
        if isinstance(write_res, Mapping):
            result["service_item_id"] = write_res.get("item_id", "") or ""

    # Esito layer dal writer.
    if isinstance(write_res, Mapping):
        result["service_url"] = write_res.get("service_url", "") or result["service_url"]
        result["layers"] = list(write_res.get("layers", []) or [])
        result["errors"].extend(write_res.get("errors", []) or [])

    # Renderers (best-effort: i fallimenti sono warning, non bloccano il publish).
    if apply_renderers and renderers_data and result.get("service_url"):
        _emit("renderers", 3, 3, "Applicazione renderer ArcGIS...")
        url_to_drawing = {
            lyr["url"]: renderers_data[lyr["name"]]
            for lyr in result["layers"]
            if lyr.get("url") and renderers_data.get(lyr.get("name"))
        }
        if url_to_drawing:
            rr = feature_writer.apply_layer_renderers(connection_id, url_to_drawing)
            if isinstance(rr, Mapping):
                result["renderers_applied"] = rr.get("applied", 0)
                for err in rr.get("errors", []) or []:
                    logger.warning(f"renderer non applicato: {err}")

    _emit("done", 3, 3, "Upload completato")
    layer_errors = any(l.get("errors") for l in result["layers"])
    writer_ok = bool(write_res.get("success")) if isinstance(write_res, Mapping) else False
    result["success"] = writer_ok and not layer_errors and not result["errors"]
    logger.info(
        f"upload_imdf_layers done: success={result['success']} mode={mode} "
        f"layers={len(result['layers'])} renderers_applied={result['renderers_applied']}"
    )
    return result
