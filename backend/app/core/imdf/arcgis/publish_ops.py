"""
Cluster PUBLISH_OPS — operazioni di scrittura layer-level su Feature

Service ArcGIS (create+populate / append / upsert / update).
"""

from typing import Dict, List

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISServiceAdminProviderProtocol
from app.utils.logger import get_logger

from .sql_helpers import _quote_sql_value, _chunked, _query_oid_by_key

logger = get_logger("app.core.imdf.arcgis_uploader")


def _add_layer_to_service(
    client: ArcGISServiceAdminProviderProtocol,
    conn: ArcGISConnectionInfo,
    service_url: str,
    gdf: object,
    layer_name: str,
    layer_id: int,
    srid: int = 4326,
) -> Dict[str, object]:
    """Aggiunge un layer a un Feature Service esistente.

    BE-IMDF-006B: implementation promoted to the provider
    `add_layer_to_service` method. This module-level symbol remains as a
    thin `KEEP_COMPAT` wrapper to:

    1. preservare il dict shape `{success, feature_count, errors}`
       atteso dai caller IMDF (`upload_imdf_layers`);
    2. mantenere temporaneamente i consumer legacy fuori dal dominio IMDF.

    Guardrail: keep this as delegation plus legacy result conversion only.
    """
    return client.add_layer_to_service(
        conn, service_url, gdf, layer_name, layer_id, srid,
    ).to_compat_dict()


# APPEND / UPSERT / UPDATE su FS esistente


def _publish_mode_existing(
    client, conn, service_url: str, item_id: str, folder_id: str,
    layers: Dict, layers_present: List[str],
    mode: str, match_columns: Dict[str, str], target_srid: int,
    result: Dict, emit, step: int, total_steps: int,
) -> Dict:
    """Esegue la pubblicazione in modalita' `append` / `upsert` / `update`
    su un FS gia' esistente. Ritorna il `result` (mutato) compilato.

    Per ogni layer IMDF presente:
      - append: `add_features` soltanto (no controllo duplicati)
      - upsert: per ogni business-key chiama `delete_features` con
                WHERE IN (...) e poi `add_features` delle nuove feature
      - update: `update_features` con OBJECTID risolto tramite match column.
                Le feature del bundle senza match → skipped (niente insert).
    """
    import pandas as _pd

    # 1. Lista i layer del FS esistente (per risolvere name → url+id
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
        layer_id  = existing["id"]

        emit("layer", step, total_steps,
             f"{mode} '{name}': preparazione ({len(gdf)} feat)...")

        errors_this_layer: List[str] = []
        deleted_count = 0
        updated_count = 0
        added_count   = 0
        skipped_count = 0

        # ── UPDATE: STRICT — updateFeatures per match column → OBJECTID
        # Pre-condizioni bloccanti
        # 1. match_col esiste nel bundle
        # 2. nessun NULL in match_col
        # 3. nessun duplicato in match_col
        # 4. TUTTI i valori del bundle hanno un OBJECTID sul FS (no skip
        # Se una fallisce → errore, nessuna chiamata a updateFeatures per il layer
        if mode == "update":
            match_col = match_columns.get(name)
            strict_fail = False
            if not match_col or match_col not in gdf.columns:
                msg = f"update '{name}': match column '{match_col}' assente nel GDB"
                logger.error(msg)
                errors_this_layer.append(msg)
                strict_fail = True
            else:
                null_count = int(gdf[match_col].isna().sum())
                if null_count > 0:
                    msg = (f"update '{name}': {null_count} feature con '{match_col}' NULL — "
                           f"update strict richiede chiavi valorizzate")
                    logger.error(msg)
                    errors_this_layer.append(msg)
                    strict_fail = True

                dup_count = int(gdf[match_col].duplicated(keep=False).sum()
                                - gdf[match_col].isna().sum())
                if dup_count > 0:
                    msg = (f"update '{name}': {dup_count} feature con '{match_col}' "
                           f"duplicato — update strict non ammette chiavi duplicate")
                    logger.error(msg)
                    errors_this_layer.append(msg)
                    strict_fail = True

            if not strict_fail:
                values = gdf[match_col].dropna().unique().tolist()
                oid_by_key: Dict[str, int] = {}
                if values:
                    oid_by_key, q_errors = _query_oid_by_key(
                        client, conn, layer_url, match_col, values
                    )
                    if q_errors:
                        errors_this_layer.extend(q_errors)
                        strict_fail = True

                if not strict_fail:
                    # Strict: ogni valore bundle deve matchare un OBJECTID
                    unmatched = [v for v in values if str(v) not in oid_by_key]
                    if unmatched:
                        preview = ", ".join(str(x) for x in unmatched[:5])
                        extra = f" (+{len(unmatched)-5} altri)" if len(unmatched) > 5 else ""
                        msg = (
                            f"update '{name}': {len(unmatched)} valori di "
                            f"'{match_col}' senza match sul FS [{preview}{extra}] — "
                            f"update strict richiede che ogni feature esista gia'"
                        )
                        logger.error(msg)
                        errors_this_layer.append(msg)
                        strict_fail = True

                if not strict_fail:
                    # Filtra il gdf alle sole righe con match_col non-null
                    # E presente in oid_by_key. `keys_in_order`, `update_feats`
                    # e la costruzione Esri features devono partire dallo
                    # STESSO sottoinsieme di righe altrimenti l'index i
                    # tra la lista features e i keys si disallinea e si
                    # iniettano OBJECTID sulle feature sbagliate (data loss
                    gdf_valid = gdf[gdf[match_col].apply(
                        lambda v: v is not None and not _pd.isna(v) and str(v) in oid_by_key
                    )]
                    missing_rows = int(len(gdf) - len(gdf_valid))
                    if missing_rows > 0:
                        msg = (f"update '{name}': {missing_rows} righe con match_col NULL "
                               f"o non risolvibile — inconsistenza con strict check")
                        logger.error(msg)
                        errors_this_layer.append(msg)
                        strict_fail = True

                    if not strict_fail and len(gdf_valid) > 0:
                        emit("layer", step, total_steps,
                             f"update '{name}': updateFeatures {len(gdf_valid)} features...")
                        try:
                            update_feats = client.dataframe_to_esri_features(
                                gdf_valid, "geometry", target_srid, operation="append"
                            )
                            keys_in_order = [str(v) for v in gdf_valid[match_col].tolist()]
                            missing_oid = 0
                            for i, feat in enumerate(update_feats):
                                if i >= len(keys_in_order):
                                    break
                                oid = oid_by_key.get(keys_in_order[i])
                                if oid is None:
                                    missing_oid += 1
                                    continue
                                feat.setdefault("attributes", {})["OBJECTID"] = oid
                            if missing_oid > 0:
                                msg = f"update '{name}': {missing_oid} feature senza OBJECTID post-resolve"
                                logger.error(msg)
                                errors_this_layer.append(msg)
                            else:
                                upd_res = client.update_features(conn, layer_url, update_feats)
                                updated_count = int(upd_res.get("updated_count", 0) or 0)
                                if upd_res.get("errors"):
                                    errors_this_layer.extend(upd_res["errors"])
                        except Exception as e:
                            logger.error(f"update_features '{name}': {e}", exc_info=True)
                            errors_this_layer.append(f"update_features: {e}")

            if strict_fail:
                # In strict mode non facciamo nulla su ArcGIS per questo layer
                skipped_count = int(len(gdf))

        # ── UPSERT: delete features con match_col nelle nuove key ────
        elif mode == "upsert":
            match_col = match_columns.get(name)
            if not match_col:
                msg = f"Upsert '{name}': nessuna match column (skip)"
                logger.warning(msg)
                errors_this_layer.append(msg)
            elif match_col not in gdf.columns:
                msg = f"Upsert '{name}': colonna '{match_col}' assente nel GDB (skip)"
                logger.warning(msg)
                errors_this_layer.append(msg)
            else:
                values = gdf[match_col].dropna().unique().tolist()
                if values:
                    for batch in _chunked(values, 200):
                        where = f"{match_col} IN ({','.join(_quote_sql_value(v) for v in batch)})"
                        try:
                            del_res = client.delete_features(conn, layer_url, where=where)
                            deleted_count += int(del_res.get("deleted_count", 0) or 0)
                            if not del_res.get("success"):
                                errors_this_layer.extend(del_res.get("errors", []) or [])
                        except Exception as e:
                            msg = f"deleteFeatures '{name}': {e}"
                            logger.error(msg, exc_info=True)
                            errors_this_layer.append(msg)

        # ── ADD features (append o upsert — NON per update) ──────────
        if mode in ("append", "upsert"):
            emit("layer", step, total_steps,
                 f"{mode} '{name}': upload {len(gdf)} features...")
            try:
                features = client.dataframe_to_esri_features(
                    gdf, "geometry", target_srid, operation="append"
                )
                add_res = client.add_features(conn, layer_url, features)
                added_count = int(add_res.get("added_count", 0) or 0)
                if add_res.get("errors"):
                    errors_this_layer.extend(add_res["errors"])
            except Exception as e:
                logger.error(f"add_features '{name}': {e}", exc_info=True)
                errors_this_layer.append(f"add_features: {e}")

        # feature_count nel result significa "feature processate con successo"
        # added per append/upsert, updated per update). Il frontend lo mostra
        # come "feat." con eventuali colonne extra deleted/skipped
        primary_count = updated_count if mode == "update" else added_count

        result["layers"].append({
            "id": layer_id,
            "name": name,
            "url": layer_url,
            "feature_count": primary_count,
            "deleted_count": deleted_count,
            "updated_count": updated_count,
            "added_count":   added_count,
            "skipped_count": skipped_count,
            "errors": errors_this_layer,
        })
        logger.info(
            f"Layer {layer_id} ({name}) {mode}: "
            f"added={added_count} updated={updated_count} "
            f"deleted={deleted_count} skipped={skipped_count}"
        )
        step += 1

    # ── Esito finale ────────────────────────────────────────────────
    # Success = nessun errore E almeno un layer ha avuto attivita' REALE
    # sul FS (add/update/delete). `skipped_count` NON conta come attivita'
    # se tutti i layer sono skipped, l'operazione non ha prodotto modifiche
    # e success=False (il frontend mostra "Errori presenti"). Questo evita
    # il falso positivo "✓ Pubblicato" quando in update strict tutti i
    # pre-check falliscono e ogni layer viene skippato integralmente
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
