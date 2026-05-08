"""
IMDF ArcGIS uploader — caricamento dei 5 layer IMDF su ArcGIS Online.

Orchestrator end-to-end (Fase 1B):
"""

from __future__ import annotations

from typing import Dict, Optional

from app.core.imdf.arcgis import (
    publish_ops as _publish_ops,
    service_discovery as _service_discovery,
    service_lifecycle as _service_lifecycle,
)
from app.utils.logger import get_logger


logger = get_logger(__name__)

# Ordine canonico dei layer IMDF dentro il Feature Service
# NOTE: il layer 0 e' "Localita" (gerarchia top-down
LAYER_ORDER = ["Localita", "OperaPrincipale", "Livello", "Locale", "Details"]

# Colonne business-key per layer IMDF. Usate dalle modalita' `upsert` per
# identificare le features esistenti da sostituire. I nomi sono ricavati
# sperimentalmente dal GDB generato da imdf.plenora.it (pyogrio read_info
DEFAULT_MATCH_COLUMNS = {
    "Localita":       "SITE_ID",
    "OperaPrincipale":"FACILITY_ID",
    "Livello":        "LEVEL_ID",
    "Locale":         "UNIT_ID",
    "Details":        "DETAIL_ID",
}

# Modalita' supportate per la pubblicazione
PUBLISH_MODES = ("replace", "append", "upsert", "update")


def upload_imdf_layers(
    connection_id: str,
    layers: Dict,
    fs_name: str,
    folder_name: Optional[str] = None,
    apply_renderers: bool = False,
    renderers_data: Optional[Dict] = None,
    target_srid: int = 4326,
    progress_callback=None,
    mode: str = "replace",
    match_columns: Optional[Dict[str, str]] = None,
) -> Dict:
    """Pubblica i 5 layer IMDF su un Feature Service ArcGIS.

    Args:
        connection_id: id della connessione ArcGIS in plenora.arcgis_connections
        layers: dict {layer_name: GeoDataFrame} prodotto da loader.load_imdf_layers
        fs_name: nome del Feature Service
        folder_name: nome della folder ArcGIS dove crearlo (creata se manca).
            Se None il FS finisce nella root content dell'utente.
        apply_renderers: se True, applica gli stili del renderers.json.
            Ignorato nelle modalita' `append` e `upsert` (il FS esistente
            conserva i suoi renderer originali).
        renderers_data: contenuto parsato di renderers.json (chiave per layer)
        target_srid: SRID dei dati (default 4326). Deve coincidere col CRS
            dei GeoDataFrame in input (loader li riproietta a 4326).
        mode: modalita' di pubblicazione
            - 'replace': cancella eventuale FS esistente e ricrea da zero (default)
            - 'append': aggiunge features ai layer del FS esistente
            - 'upsert': per ogni layer cancella le features con match_col nei
                         valori in ingresso, poi inserisce i nuovi. Equivale a
                         un "replace per business key".
        match_columns: override dei default DEFAULT_MATCH_COLUMNS (solo upsert)

    Returns:
        dict con esito completo:
            {
                "success": bool,
                "service_url": str,
                "service_item_id": str,
                "folder_id": str,
                "layers": [
                    {"id": int, "name": str, "url": str, "feature_count": int,
                     "errors": [...]},
                    ...
                ],
                "renderers_applied": int,
                "errors": [str, ...],
            }
    """
    from app.core.connections.arcgis_connection_resolver import resolve_arcgis_connection

    if mode not in PUBLISH_MODES:
        return {
            "success": False, "service_url": "", "service_item_id": "",
            "folder_id": "", "layers": [], "renderers_applied": 0,
            "errors": [f"mode non supportata: {mode}. Valori validi: {PUBLISH_MODES}"],
        }

    effective_match_cols = {**DEFAULT_MATCH_COLUMNS, **(match_columns or {})}

    def _emit(stage, current, total, message=""):
        """Wrapper sicuro per il progress_callback — non fa mai throw."""
        if progress_callback is None:
            return
        try:
            progress_callback(stage, current, total, message)
        except Exception as _cb_e:
            logger.warning(f"progress_callback error: {_cb_e}")

    result: Dict = {
        "success": False,
        "mode": mode,
        "service_url": "",
        "service_item_id": "",
        "folder_id": "",
        "layers": [],
        "renderers_applied": 0,
        "errors": [],
    }

    # ── 1. Carica connection ──────────────────────────────────────────
    _resolved = resolve_arcgis_connection(connection_id)
    if _resolved is None:
        result["errors"].append(f"Connessione ArcGIS '{connection_id}' non trovata")
        return result
    conn, client = _resolved
    if not conn.username:
        result["errors"].append("La connection ArcGIS non ha username (richiesto per createService)")
        return result

    # Total steps for progress: folder + delete + N layers + (renderers?
    layers_present_preview = [n for n in LAYER_ORDER if n in layers and len(layers[n]) > 0]
    total_steps = 1 + 1 + len(layers_present_preview) + (1 if apply_renderers and renderers_data else 0)
    step = 0

    # ── 2. Folder: trova o crea ───────────────────────────────────────
    _emit("folder", step, total_steps, f"Preparazione folder '{folder_name or '(root)'}'...")
    folder_id = ""
    if folder_name:
        try:
            folder_id = (
                _service_discovery._ensure_folder(client, conn, folder_name) or ""
            )
            result["folder_id"] = folder_id
            logger.info(f"Folder '{folder_name}' -> id={folder_id or '(root)'}")
        except Exception as e:
            result["errors"].append(f"Errore ensure_folder: {e}")
            return result
    step += 1

    # ── 3. Dispatch per modalita' ─────────────────────────────────────
    # replace → cancella FS esistente e procede col path create_service
    # append/upsert → trova FS esistente e delega a funzione dedicata
    # ritornando qui con il risultato gia' pronto
    if mode in ("append", "upsert"):
        _emit("find_service", step, total_steps,
              f"Ricerca FS esistente '{fs_name}'...")
        existing_url, existing_item_id, _existing_folder = (
            _service_discovery._find_existing_service(client, conn, fs_name, folder_id)
        )
        if not existing_url:
            result["errors"].append(
                f"Modalita' '{mode}' richiede un FS esistente '{fs_name}' "
                f"(nella folder '{folder_name or 'root'}') che non e' stato trovato. "
                f"Usa modalita' 'replace' per la prima pubblicazione."
            )
            return result
        result["service_url"] = existing_url
        result["service_item_id"] = existing_item_id
        step += 1

        return _publish_ops._publish_mode_existing(
            client=client, conn=conn, service_url=existing_url,
            item_id=existing_item_id, folder_id=folder_id,
            layers=layers, layers_present=layers_present_preview,
            mode=mode, match_columns=effective_match_cols,
            target_srid=target_srid,
            result=result, emit=_emit, step=step, total_steps=total_steps,
        )

    # ── Modalita' replace: cancella eventuale FS esistente con stesso nome ──
    _emit("cleanup", step, total_steps, f"Verifica FS esistente '{fs_name}'...")
    del_attempted, del_succeeded = 0, 0
    try:
        del_attempted, del_succeeded = _service_lifecycle._delete_existing_service(
            client, conn, fs_name, folder_id
        )
        if del_succeeded > 0:
            logger.info(
                f"Feature Service '{fs_name}': {del_succeeded}/{del_attempted} item cancellati"
            )
    except Exception as e:
        # Non blocchiamo: il check anti-zombie sotto dara' un messaggio chiaro
        logger.warning(f"delete_existing_service: {e}")
    step += 1

    # Check anti-zombie: lo saltiamo SOLO se il delete e' stato totale
    # attempted == succeeded > 0), cioe' abbiamo effettivamente rimosso
    # tutti gli item trovati. In caso di
    # nessun item trovato (attempted=0) → check necessario per zombie
    # partial delete (attempted>succeeded) → check necessario, il nome
    # potrebbe essere ancora riservato sul hosted server
    full_delete = del_attempted > 0 and del_attempted == del_succeeded
    if not full_delete:
        available, _err = _service_discovery._is_service_name_available(
            client, conn, fs_name
        )
        if not available:
            # Tentativo automatico di cleanup zombie: l'item esiste sul
            # hosted server ma non e' nell'enumerazione utente. Proviamo a
            # recuperare il serviceItemId via admin describe e cancellarlo
            # direttamente. Funziona se l'utente e' il proprietario
            # dell'item (caso tipico di cancellazioni parziali
            logger.info(
                f"replace '{fs_name}': nome riservato non visibile, tentativo "
                f"cleanup zombie via hosted admin fallback"
            )
            cleaned, cleanup_msg = _service_lifecycle._try_cleanup_zombie_service(
                client, conn, fs_name
            )
            if cleaned:
                _emit("cleanup", step, total_steps, f"Zombie '{fs_name}' rimosso, procedo...")
                logger.info(f"zombie cleanup success: {cleanup_msg}")
            else:
                # Re-check disponibilita' per sicurezza (il cleanup puo'
                # avere effetti anche se la risposta non era chiara
                available2, _ = _service_discovery._is_service_name_available(
                    client, conn, fs_name
                )
                if not available2:
                    result["errors"].append(
                        f"Nome Feature Service '{fs_name}' gia' riservato nell'organizzazione "
                        f"ma non visibile all'utente '{conn.username}'. Possibili cause: "
                        f"(a) servizio zombie dopo cancellazione parziale "
                        f"(visti {del_attempted}, rimossi {del_succeeded}); "
                        f"(b) item appartenente a un altro utente dell'org. "
                        f"Cleanup automatico fallito: {cleanup_msg}. "
                        f"Soluzione: usa un nome diverso oppure contatta l'admin "
                        f"per rimuovere il servizio residuo."
                    )
                    return result

    # ── 4. Verifica che i layer richiesti siano tutti presenti ───────
    missing = [n for n in LAYER_ORDER if n not in layers or len(layers[n]) == 0]
    if missing:
        # Non e' un errore bloccante: alcuni siti potrebbero non avere
        # tutti i layer (es. solo Localita+Livelli). Logghiamo soltanto
        logger.warning(f"Layer mancanti o vuoti, saltati: {missing}")

    layers_present = [n for n in LAYER_ORDER if n in layers and len(layers[n]) > 0]
    if not layers_present:
        result["errors"].append("Nessun layer IMDF popolato da caricare")
        return result

    # ── 5. Crea il Feature Service col primo layer (Localita di norma) ──
    first_name = layers_present[0]
    first_gdf = layers[first_name]
    _emit("create_service", step, total_steps,
          f"Layer 0/{len(layers_present)}: creazione '{first_name}' ({len(first_gdf)} feat)...")
    try:
        first_result = client.create_service(
            conn=conn,
            service_name=fs_name,
            layer_name=first_name,
            df=first_gdf,
            geometry_column="geometry",
            srid=target_srid,
            folder_id=folder_id,
        )
    except Exception as e:
        result["errors"].append(f"createService fallito: {e}")
        return result

    service_url = first_result.get("service_url", "")
    if not service_url:
        result["errors"].append(f"createService non ha restituito service_url: {first_result}")
        return result

    result["service_url"] = service_url
    result["service_item_id"] = first_result.get("item_id", "")
    result["layers"].append({
        "id": 0,
        "name": first_name,
        "url": f"{service_url}/0",
        "feature_count": first_result.get("feature_count", 0),
        "errors": first_result.get("errors", []),
    })
    logger.info(f"Layer 0 ({first_name}) creato: {first_result.get('feature_count')} features")
    step += 1

    # ── 6. Aggiungi i layer 1..N ───────────────────────────────────────
    for idx, name in enumerate(layers_present[1:], start=1):
        gdf = layers[name]
        _emit("add_layer", step, total_steps,
              f"Layer {idx}/{len(layers_present)-1}: upload '{name}' ({len(gdf)} feat)...")
        try:
            layer_result = client.add_layer_to_service(
                conn,
                service_url,
                gdf,
                name,
                idx,
                target_srid,
            )
            result["layers"].append({
                "id": idx,
                "name": name,
                "url": f"{service_url}/{idx}",
                "feature_count": layer_result.feature_count,
                "errors": list(layer_result.errors),
            })
            logger.info(f"Layer {idx} ({name}) creato: {layer_result.feature_count} features")
            step += 1
        except Exception as e:
            logger.error(f"Errore aggiunta layer {name}: {e}", exc_info=True)
            result["layers"].append({
                "id": idx,
                "name": name,
                "url": "",
                "feature_count": 0,
                "errors": [str(e)],
            })
            result["errors"].append(f"Layer {name}: {e}")
            step += 1

    # ── 7. Applica renderers (opzionale) ───────────────────────────────
    # — Esri ritorna success=True ma in pratica non aggiorna nulla. Usiamo
    # IMDF decides which renderer to apply; the ArcGIS provider owns the
    # layer-level updateDefinition mechanics.
    if apply_renderers and renderers_data:
        _emit("renderers", step, total_steps, "Applicazione renderer ArcGIS...")
        for layer_info in result["layers"]:
            name = layer_info["name"]
            drawing_info = renderers_data.get(name)
            if not drawing_info:
                continue
            try:
                res = client.update_layer_renderer(
                    conn=conn,
                    layer_url=f"{service_url}/{layer_info['id']}",
                    drawing_info=drawing_info,
                )
                if res.get("success"):
                    result["renderers_applied"] += 1
                    logger.info(f"Renderer applicato a {name}")
                else:
                    logger.warning(f"renderer update {name} non riuscito: {res}")
            except Exception as e:
                logger.warning(f"renderer update {name} eccezione: {e}")
        step += 1

    _emit("done", total_steps, total_steps, "Upload completato")
    # ── 8. Esito finale ────────────────────────────────────────────────
    total_loaded = sum(l["feature_count"] for l in result["layers"])
    result["success"] = total_loaded > 0 and not any(l["errors"] for l in result["layers"])
    logger.info(
        f"upload_imdf_layers done: success={result['success']} "
        f"layers={len(result['layers'])} total_features={total_loaded} "
        f"renderers_applied={result['renderers_applied']}"
    )
    return result
