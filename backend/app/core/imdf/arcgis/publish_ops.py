"""
Cluster PUBLISH_OPS — operazioni di scrittura layer-level su Feature

Service ArcGIS (create+populate / append / upsert / update).
"""

from collections.abc import Mapping
from typing import Any, List

from app.core.connections.arcgis_publish_modes import publish_existing_layer_mode
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISServiceAdminProviderProtocol
from app.shared.contracts.payloads import JsonObject, ObjectMap, StringMap
from app.utils.logger import get_logger

logger = get_logger("app.core.imdf.arcgis_uploader")


def _add_layer_to_service(
    client: ArcGISServiceAdminProviderProtocol,
    conn: ArcGISConnectionInfo,
    service_url: str,
    gdf: object,
    layer_name: str,
    layer_id: int,
    srid: int = 4326,
) -> ObjectMap:
    """Aggiunge un layer a un Feature Service esistente.

    BE-IMDF-006B: implementation promoted to the provider
    `add_layer_to_service` method. This module-level symbol remains as a
    thin IMDF adapter to:

    1. preservare il dict shape `{success, feature_count, errors}`
       atteso dai caller IMDF (`upload_imdf_layers`);
    2. keep provider mechanics outside the IMDF publish flow.

    Guardrail: keep this as delegation plus IMDF result-shape conversion only.
    """
    return client.add_layer_to_service(
        conn, service_url, gdf, layer_name, layer_id, srid,
    ).to_compat_dict()


# APPEND / UPSERT / UPDATE su FS esistente


def _publish_mode_existing(
    client, conn, service_url: str, item_id: str, folder_id: str,
    layers: Mapping[str, Any], layers_present: List[str],
    mode: str, match_columns: StringMap, target_srid: int,
    result: JsonObject, emit, step: int, total_steps: int,
) -> JsonObject:
    """Esegue la pubblicazione in modalita' `append` / `upsert` / `update`
    su un FS gia' esistente. Ritorna il `result` (mutato) compilato.

    Per ogni layer IMDF presente:
      - append: `add_features` soltanto (no controllo duplicati)
      - upsert: per ogni business-key chiama `delete_features` con
                WHERE IN (...) e poi `add_features` delle nuove feature
      - update: `update_features` con OBJECTID risolto tramite match column.
                Le feature del bundle senza match → skipped (niente insert).
    """
    try:
        existing_layers = client.list_layers(conn, service_url) or []
    except Exception as e:
        result["errors"].append(f"list_layers: {e}")
        return result

    name_to_info = {}
    for lyr in existing_layers:
        if lyr.get("name"):
            name_to_info[lyr["name"]] = lyr

    for name in layers_present:
        gdf = layers[name]
        existing = name_to_info.get(name)
        if not existing:
            msg = f"Layer '{name}' non trovato nel FS esistente"
            logger.warning(msg)
            result["layers"].append({
                "id": None, "name": name, "url": "",
                "feature_count": 0, "errors": [msg],
            })
            result["errors"].append(msg)
            step += 1
            continue

        layer_url = existing["url"]
        layer_id = existing["id"]

        emit("layer", step, total_steps,
             f"{mode} '{name}': preparazione ({len(gdf)} feat)...")
        if mode == "update":
            emit("layer", step, total_steps,
                 f"update '{name}': updateFeatures {len(gdf)} features...")
        elif mode in ("append", "upsert"):
            emit("layer", step, total_steps,
                 f"{mode} '{name}': upload {len(gdf)} features...")

        mode_result = publish_existing_layer_mode(
            client=client,
            conn=conn,
            layer_url=layer_url,
            gdf=gdf,
            mode=mode,
            match_col=match_columns.get(name),
            target_srid=target_srid,
        )
        errors_this_layer = _format_publish_mode_errors(
            name,
            mode,
            mode_result.errors,
        )

        # feature_count nel result significa "feature processate con successo"
        # (added per append/upsert, updated per update). Il frontend lo mostra
        # come "feat." con eventuali colonne extra deleted/skipped.
        primary_count = (
            mode_result.updated_count if mode == "update" else mode_result.added_count
        )

        result["layers"].append({
            "id": layer_id,
            "name": name,
            "url": layer_url,
            "feature_count": primary_count,
            "deleted_count": mode_result.deleted_count,
            "updated_count": mode_result.updated_count,
            "added_count": mode_result.added_count,
            "skipped_count": mode_result.skipped_count,
            "errors": errors_this_layer,
        })
        logger.info(
            f"Layer {layer_id} ({name}) {mode}: "
            f"added={mode_result.added_count} updated={mode_result.updated_count} "
            f"deleted={mode_result.deleted_count} skipped={mode_result.skipped_count}"
        )
        step += 1

    # Success = nessun errore E almeno un layer ha avuto attivita' reale
    # sul FS (add/update/delete). `skipped_count` non conta come attivita'.
    real_activity = any(
        (l.get("added_count", 0) or 0)
        + (l.get("updated_count", 0) or 0)
        + (l.get("deleted_count", 0) or 0) > 0
        for l in result["layers"]
    )
    has_errors = any(l["errors"] for l in result["layers"]) or bool(result.get("errors"))
    result["success"] = real_activity and not has_errors

    total_primary = sum(l.get("feature_count", 0) or 0 for l in result["layers"])
    emit("done", total_steps, total_steps, f"{mode} completato")
    logger.info(
        f"upload_imdf_layers ({mode}) done: success={result['success']} "
        f"layers={len(result['layers'])} primary={total_primary}"
    )
    return result


def _format_publish_mode_errors(name: str, mode: str, errors: List[str]) -> List[str]:
    """Keep legacy IMDF layer/mode prefixes on generic publish-mode errors."""
    formatted = []
    for error in errors:
        if error.startswith("deleteFeatures:"):
            detail = error.removeprefix("deleteFeatures:").strip()
            formatted.append(f"deleteFeatures '{name}': {detail}")
        elif error.startswith(("add_features:", "update_features:")):
            formatted.append(error)
        elif mode == "upsert":
            formatted.append(f"Upsert '{name}': {error}")
        elif mode == "update":
            formatted.append(f"update '{name}': {error}")
        else:
            formatted.append(error)
    return formatted
