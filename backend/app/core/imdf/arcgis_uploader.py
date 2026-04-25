"""
IMDF ArcGIS uploader — caricamento dei 5 layer IMDF su ArcGIS Online.

Orchestrator end-to-end (Fase 1B):

    upload_imdf_layers(
        connection_id="arcgis_...",
        layers={"Localita": gdf, "OperaPrincipale": gdf, ...},
        fs_name="LO2244_test",
        folder_name="test_plenora",
        apply_renderers=False,
        renderers_data=None,
    )

Cosa fa, in ordine:
 1. Carica `ArcGISConnectionInfo` dal DB usando features_service
 2. Trova (o crea) la folder ArcGIS dove salvare il Feature Service
 3. Se un FS con `fs_name` esiste gia' nella folder lo elimina (idempotenza)
 4. Crea un nuovo FS vuoto via createService
 5. Per ogni layer nell'ordine canonico (Localita, OperaPrincipale, Livello,
    Locale, Details) aggiunge la definizione (`addToDefinition`) e popola
    le features (`addFeatures` con batching nativo del client).
 6. Opzionalmente applica i renderers.json layer per layer.
 7. Ritorna un dict con esito e dettagli per ogni layer.

Riusa quanto possibile da `app.core.features.arcgis_client` (un singolo
ArcGISClient) per evitare di duplicare logica esistente di:
  - autenticazione + token cache
  - batching addFeatures
  - schema field derivation (`_python_type_to_esri_field`)
  - geom type detection (`_esri_geometry_type`)
  - feature serialization (`dataframe_to_esri_features`)
  - update renderer (`update_renderer`)

Il metodo `create_service` di ArcGISClient pero' supporta solo single layer:
per i layer 2-5 questo modulo costruisce direttamente il payload
`addToDefinition` con la stessa struttura di `create_service`.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Ordine canonico dei layer IMDF dentro il Feature Service.
# Importante: il layer 0 e' "Localita" (gerarchia top-down).
LAYER_ORDER = ["Localita", "OperaPrincipale", "Livello", "Locale", "Details"]

# Colonne business-key per layer IMDF. Usate dalle modalita' `upsert` per
# identificare le features esistenti da sostituire. I nomi sono ricavati
# sperimentalmente dal GDB generato da imdf.plenora.it (pyogrio read_info).
DEFAULT_MATCH_COLUMNS = {
    "Localita":       "SITE_ID",
    "OperaPrincipale":"FACILITY_ID",
    "Livello":        "LEVEL_ID",
    "Locale":         "UNIT_ID",
    "Details":        "DETAIL_ID",
}

# Modalita' supportate per la pubblicazione.
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
            - 'append':  aggiunge features ai layer del FS esistente
            - 'upsert':  per ogni layer cancella le features con match_col nei
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
    from app.core.features.features_service import FeaturesService
    from app.core.features.arcgis_client import create_client

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
    fs_svc = FeaturesService()
    conn_pyd = fs_svc.get_connection(connection_id)
    if not conn_pyd:
        result["errors"].append(f"Connessione ArcGIS '{connection_id}' non trovata")
        return result
    conn = fs_svc._to_connection_info(conn_pyd)
    if not conn.username:
        result["errors"].append("La connection ArcGIS non ha username (richiesto per createService)")
        return result

    client = create_client()

    # Total steps for progress: folder + delete + N layers + (renderers?)
    layers_present_preview = [n for n in LAYER_ORDER if n in layers and len(layers[n]) > 0]
    total_steps = 1 + 1 + len(layers_present_preview) + (1 if apply_renderers and renderers_data else 0)
    step = 0

    # ── 2. Folder: trova o crea ───────────────────────────────────────
    _emit("folder", step, total_steps, f"Preparazione folder '{folder_name or '(root)'}'...")
    folder_id = ""
    if folder_name:
        try:
            folder_id = _ensure_folder(client, conn, folder_name) or ""
            result["folder_id"] = folder_id
            logger.info(f"Folder '{folder_name}' → id={folder_id or '(root)'}")
        except Exception as e:
            result["errors"].append(f"Errore ensure_folder: {e}")
            return result
    step += 1

    # ── 3. Dispatch per modalita' ─────────────────────────────────────
    # replace → cancella FS esistente e procede col path create_service
    # append/upsert → trova FS esistente e delega a funzione dedicata,
    #                 ritornando qui con il risultato gia' pronto.
    if mode in ("append", "upsert"):
        _emit("find_service", step, total_steps,
              f"Ricerca FS esistente '{fs_name}'...")
        existing_url, existing_item_id, _existing_folder = _find_existing_service(client, conn, fs_name, folder_id)
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

        return _publish_mode_existing(
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
        del_attempted, del_succeeded = _delete_existing_service(client, conn, fs_name, folder_id)
        if del_succeeded > 0:
            logger.info(
                f"Feature Service '{fs_name}': {del_succeeded}/{del_attempted} item cancellati"
            )
    except Exception as e:
        # Non blocchiamo: il check anti-zombie sotto dara' un messaggio chiaro
        logger.warning(f"delete_existing_service: {e}")
    step += 1

    # Check anti-zombie: lo saltiamo SOLO se il delete e' stato totale
    # (attempted == succeeded > 0), cioe' abbiamo effettivamente rimosso
    # tutti gli item trovati. In caso di:
    #   - nessun item trovato (attempted=0) → check necessario per zombie
    #   - partial delete (attempted>succeeded) → check necessario, il nome
    #     potrebbe essere ancora riservato sul hosted server
    full_delete = del_attempted > 0 and del_attempted == del_succeeded
    if not full_delete:
        available, _err = _is_service_name_available(client, conn, fs_name)
        if not available:
            # Tentativo automatico di cleanup zombie: l'item esiste sul
            # hosted server ma non e' nell'enumerazione utente. Proviamo a
            # recuperare il serviceItemId via admin describe e cancellarlo
            # direttamente. Funziona se l'utente e' il proprietario
            # dell'item (caso tipico di cancellazioni parziali).
            logger.info(
                f"replace '{fs_name}': nome riservato non visibile, tentativo "
                f"cleanup zombie via hosted admin fallback"
            )
            cleaned, cleanup_msg = _try_cleanup_zombie_service(client, conn, fs_name)
            if cleaned:
                _emit("cleanup", step, total_steps, f"Zombie '{fs_name}' rimosso, procedo...")
                logger.info(f"zombie cleanup success: {cleanup_msg}")
            else:
                # Re-check disponibilita' per sicurezza (il cleanup puo'
                # avere effetti anche se la risposta non era chiara)
                available2, _ = _is_service_name_available(client, conn, fs_name)
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
        # tutti i layer (es. solo Localita+Livelli). Logghiamo soltanto.
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
            layer_result = _add_layer_to_service(
                client=client,
                conn=conn,
                service_url=service_url,
                gdf=gdf,
                layer_name=name,
                layer_id=idx,
                srid=target_srid,
            )
            result["layers"].append({
                "id": idx,
                "name": name,
                "url": f"{service_url}/{idx}",
                "feature_count": layer_result.get("feature_count", 0),
                "errors": layer_result.get("errors", []),
            })
            logger.info(f"Layer {idx} ({name}) creato: {layer_result.get('feature_count')} features")
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
    # Nota: NON usiamo client.update_renderer() perche' quel metodo posta
    # su `service-level updateDefinition` con `{layers: [{id, drawingInfo}]}`
    # — Esri ritorna success=True ma in pratica non aggiorna nulla. Usiamo
    # invece il pattern layer-level: POST su `.../FeatureServer/<id>/updateDefinition`
    # con payload `{drawingInfo: {renderer: ...}}` (verificato sperimentalmente).
    if apply_renderers and renderers_data:
        _emit("renderers", step, total_steps, "Applicazione renderer ArcGIS...")
        token = client.authenticate(conn)
        admin_base = service_url.replace("/rest/services/", "/rest/admin/services/")
        referer = {"Referer": conn.server_url}
        for layer_info in result["layers"]:
            name = layer_info["name"]
            drawing_info = renderers_data.get(name)
            if not drawing_info:
                continue
            try:
                update_def = {"drawingInfo": drawing_info}
                upd_url = f"{admin_base}/{layer_info['id']}/updateDefinition"
                r = client._http.post(
                    upd_url,
                    data={
                        "updateDefinition": json.dumps(update_def),
                        "f": "json",
                        "token": token or "",
                    },
                    timeout=30,
                    headers=referer,
                )
                res = r.json()
                if res.get("success"):
                    result["renderers_applied"] += 1
                    logger.info(f"Renderer applicato a {name}")
                else:
                    logger.warning(f"updateDefinition {name} non riuscito: {res}")
            except Exception as e:
                logger.warning(f"updateDefinition {name} eccezione: {e}")
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


# =============================================================================
# HELPER PRIVATI
# =============================================================================

def _list_user_folders(client, conn) -> List[dict]:
    """Read-only: lista folder dell'utente corrente (senza crearne).
    Ritorna lista di dict [{id, title, ...}]. Usato dal validator per
    risolvere ownerFolder (id) → title prima di confrontare con la
    selezione utente.
    """
    token = client.authenticate(conn)
    parsed = urlparse(conn.server_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
    referer = {"Referer": conn.server_url}

    list_url = f"{base_url}/content/users/{conn.username}"
    params = {"f": "json"}
    if token:
        params["token"] = token
    try:
        r = client._http.get(list_url, params=params, timeout=30, headers=referer)
        data = r.json()
        return data.get("folders", []) or []
    except Exception as e:
        logger.warning(f"_list_user_folders: errore: {e}")
        return []


def _ensure_folder(client, conn, folder_name: str) -> Optional[str]:
    """Trova (o crea) una folder nei contenuti dell'utente. Ritorna folder_id."""
    token = client.authenticate(conn)
    parsed = urlparse(conn.server_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
    referer = {"Referer": conn.server_url}

    # 1. Lista folder esistenti
    list_url = f"{base_url}/content/users/{conn.username}"
    params = {"f": "json"}
    if token:
        params["token"] = token

    r = client._http.get(list_url, params=params, timeout=30, headers=referer)
    data = r.json()
    folders = data.get("folders", []) or []
    for f in folders:
        if f.get("title") == folder_name:
            return f.get("id")

    # 2. Crea
    create_url = f"{base_url}/content/users/{conn.username}/createFolder"
    create_data = {"title": folder_name, "f": "json"}
    if token:
        create_data["token"] = token
    r = client._http.post(create_url, data=create_data, timeout=30, headers=referer)
    res = r.json()
    if not res.get("success"):
        raise RuntimeError(f"createFolder fallito: {res}")
    return (res.get("folder") or {}).get("id")


def _try_cleanup_zombie_service(client, conn, fs_name: str) -> Tuple[bool, str]:
    """Cancella un Feature Service "zombie": service name riservato sul
    hosted server ma portal item non visibile nell'enumerazione utente.

    Strategia:
      1. Query admin hosted `GET /admin/services/{fs_name}.FeatureServer?f=json`
         → ottiene il `serviceItemId` del portal item associato.
      2. `POST /content/users/{username}/items/{itemId}/delete?permanentDelete=true`
         → cancella l'item (e il hosted service cascade).

    Fallisce silenziosamente (ritorna False) se:
      - l'admin describe non ritorna un itemId
      - il delete del portal item fallisce (permessi insufficienti)

    Ritorna (success, message).
    """
    token = client.authenticate(conn)
    parsed = urlparse(conn.server_url)
    portal_base = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
    referer = {"Referer": conn.server_url}

    # 1. Trova l'admin hosted server URL per questa org.
    # Per ArcGIS Online: https://services-eu{N}.arcgis.com/{orgId}/arcgis/admin/services
    # Il base e' scoperto via `/portals/self` che ritorna i service URL.
    try:
        pr = client._http.get(
            f"{portal_base}/portals/self",
            params={"f": "json", "token": token},
            headers=referer, timeout=30,
        )
        portal_info = pr.json()
        # `urlKey` + `customBaseUrl` costruiscono l'URL pubblico, ma per
        # i servizi hosted dobbiamo leggere `helperServices` o
        # `defaultHostingServerUrl`. La sezione utile e' `helperServices`.
        hosting_url = None
        for srv in (portal_info.get("helperServices") or {}).get("hostingServers", []) or []:
            if srv.get("admin"):
                hosting_url = srv["admin"]
                break
        # Fallback: cerca nei service URL gia' noti in altri endpoint
        if not hosting_url:
            # ArcGIS Online pattern: services-eu{N}.arcgis.com/{orgId}/arcgis
            org_id = portal_info.get("id") or portal_info.get("orgId")
            if org_id:
                # Prova i pattern piu' comuni (EU1 / US) — meglio di niente
                for host in ("services-eu1.arcgis.com", "services.arcgis.com", "services1.arcgis.com"):
                    hosting_url = f"https://{host}/{org_id}/arcgis"
                    # Verifica l'esistenza con un GET admin services list
                    test = client._http.get(
                        f"{hosting_url}/admin/services",
                        params={"f": "json", "token": token},
                        headers=referer, timeout=15,
                    )
                    try:
                        if "services" in test.json():
                            break
                    except Exception:
                        pass
                    hosting_url = None
        if not hosting_url:
            return (False, "hosting server URL non determinato da /portals/self")
    except Exception as e:
        return (False, f"portal/self failed: {e}")

    # 2. Admin describe del servizio per ottenere serviceItemId
    admin_base = f"{hosting_url.rstrip('/')}/admin/services"
    describe_url = f"{admin_base}/{fs_name}.FeatureServer"
    try:
        dr = client._http.get(
            describe_url,
            params={"f": "json", "token": token},
            headers=referer, timeout=30,
        )
        admin_info = dr.json()
        if "error" in admin_info:
            return (False, f"admin describe error: {admin_info['error']}")
        service_item_id = admin_info.get("serviceItemId")
        if not service_item_id:
            return (False, "serviceItemId non presente in admin describe")
    except Exception as e:
        return (False, f"admin describe failed: {e}")

    # 3. Cancella il portal item via user delete endpoint (permanentDelete)
    # Nota: usiamo il path `/users/{username}/items/{id}/delete` che richiede
    # che l'utente sia il proprietario dell'item. Se l'item appartiene a un
    # altro utente dell'org, questa chiamata fallisce con 403 — in quel caso
    # l'utente corrente non ha modo di rimuovere lo zombie senza intervento
    # admin, e il caller deve mostrare il messaggio di errore originale.
    del_url = f"{portal_base}/content/users/{conn.username}/items/{service_item_id}/delete"
    del_data = {"f": "json", "permanentDelete": "true"}
    if token:
        del_data["token"] = token
    try:
        rr = client._http.post(del_url, data=del_data, headers=referer, timeout=30)
        res = rr.json()
        if res.get("success"):
            logger.info(
                f"Zombie cleanup: item {service_item_id} ('{fs_name}') cancellato "
                f"via admin fallback"
            )
            return (True, f"item {service_item_id} cancellato")
        else:
            return (False, f"delete item failed: {res}")
    except Exception as e:
        return (False, f"delete request failed: {e}")


def _is_service_name_available(client, conn, fs_name: str) -> Tuple[bool, Optional[str]]:
    """Controlla presso il portal se il nome servizio e' disponibile.

    Usa `/sharing/rest/portals/self/isServiceNameAvailable` che interroga
    il hosted server indipendentemente dalla visibilita' del portal item —
    utile per rilevare "zombie services" (item cancellato ma nome ancora
    riservato) o nomi di altri utenti dell'organizzazione.

    Returns:
        (available, error_message):
            available = True → il nome e' libero, createService andra' a buon fine
            available = False → il nome e' occupato; error_message se disponibile
    """
    token = client.authenticate(conn)
    parsed = urlparse(conn.server_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
    referer = {"Referer": conn.server_url}
    params = {
        "name": fs_name,
        "type": "Feature Service",
        "f": "json",
    }
    if token:
        params["token"] = token
    try:
        r = client._http.get(
            f"{base_url}/portals/self/isServiceNameAvailable",
            params=params, timeout=20, headers=referer,
        )
        data = r.json()
        if "error" in data:
            return True, f"check fallito: {data['error'].get('message', '')}"
        return bool(data.get("available", False)), None
    except Exception as e:
        logger.warning(f"_is_service_name_available error: {e}")
        return True, str(e)  # fail-open: assume disponibile, poi createService dara' errore


def _enumerate_all_user_items(client, conn) -> List[dict]:
    """Enumera TUTTI gli item (in tutte le folder + root) dell'utente corrente.
    Approccio robusto che non dipende dall'endpoint /search (che in alcuni
    casi non matcha esattamente per via della tokenizzazione del titolo).

    Ritorna lista di item {id, title, type, name, url, ownerFolder, ...}.
    """
    token = client.authenticate(conn)
    parsed = urlparse(conn.server_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
    referer = {"Referer": conn.server_url}

    def _get(url, params):
        if token:
            params = {**params, "token": token}
        r = client._http.get(url, params=params, timeout=30, headers=referer)
        return r.json()

    all_items: List[dict] = []

    # Root content
    try:
        root_data = _get(
            f"{base_url}/content/users/{conn.username}",
            {"f": "json", "num": 1000},
        )
        all_items.extend(root_data.get("items", []) or [])
        folders = root_data.get("folders", []) or []
    except Exception as e:
        logger.warning(f"_enumerate_all_user_items: root fallita: {e}")
        folders = []

    # Per ogni folder
    for f in folders:
        fid = f.get("id")
        if not fid:
            continue
        try:
            folder_data = _get(
                f"{base_url}/content/users/{conn.username}/{fid}",
                {"f": "json", "num": 1000},
            )
            all_items.extend(folder_data.get("items", []) or [])
        except Exception as e:
            logger.warning(f"_enumerate_all_user_items: folder {fid} fallita: {e}")

    return all_items


def _search_user_items(client, conn, fs_name: str) -> List[dict]:
    """Trova Feature Service dell'utente corrente che matchano `fs_name`.

    Match strategia (in ordine, il primo che trova qualcosa vince):
      1. title esatto (case-sensitive)
      2. title esatto (case-insensitive)
      3. name esatto (il service name ArcGIS, usato nell'URL)

    Usa l'enumerazione completa folder-by-folder per essere robusto.
    """
    all_items = _enumerate_all_user_items(client, conn)
    fs_items  = [it for it in all_items if it.get("type") == "Feature Service"]

    logger.info(
        f"_search_user_items: enumerati {len(all_items)} item totali, "
        f"{len(fs_items)} Feature Service per utente '{conn.username}'. "
        f"Cerco: '{fs_name}'"
    )

    if not fs_items:
        return []

    # 1. title esatto
    matching = [it for it in fs_items if it.get("title") == fs_name]
    if matching:
        logger.info(f"_search_user_items: {len(matching)} match per title esatto")
        return matching

    # 2. title case-insensitive
    fs_name_lower = fs_name.lower()
    matching = [it for it in fs_items if (it.get("title") or "").lower() == fs_name_lower]
    if matching:
        logger.info(f"_search_user_items: {len(matching)} match per title case-insensitive")
        return matching

    # 3. name esatto (es. se fs_name e' il service name non il title)
    matching = [it for it in fs_items
                if (it.get("name") or "") == fs_name
                or (it.get("name") or "").lower() == fs_name_lower]
    if matching:
        logger.info(f"_search_user_items: {len(matching)} match per name")
        return matching

    # 4. URL contiene fs_name (/services/{fs_name}/FeatureServer)
    matching = [it for it in fs_items
                if f"/services/{fs_name}/" in (it.get("url") or "")]
    if matching:
        logger.info(f"_search_user_items: {len(matching)} match per URL pattern")
        return matching

    # Niente trovato — log dei titoli esistenti per debug
    titles_preview = [f"{it.get('title')} (name={it.get('name')})" for it in fs_items[:10]]
    logger.warning(
        f"_search_user_items: NESSUN match per '{fs_name}'. "
        f"Primi FS dell'utente: {titles_preview}"
    )
    return []


def _delete_existing_service(client, conn, fs_name: str, folder_id: str = "") -> Tuple[int, int]:
    """Cerca un Feature Service col nome dato (in QUALSIASI folder dell'utente)
    e lo cancella. Il parametro folder_id e' tenuto per retrocompatibilita'
    ma non restringe piu' la ricerca — usiamo /sharing/rest/search globale
    per catturare anche FS che fossero in folder diverse da quella target.

    Ritorna una tupla `(attempted, succeeded)`:
      - attempted: numero di item trovati col nome fs_name
      - succeeded: numero di item effettivamente cancellati
    Il caller puo' decidere di skippare il check anti-zombie solo se
    `attempted == succeeded > 0` (delete completo); in caso di partial
    delete il nome puo' essere ancora riservato e va ri-controllato.
    """
    token = client.authenticate(conn)
    parsed = urlparse(conn.server_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
    referer = {"Referer": conn.server_url}

    matching = _search_user_items(client, conn, fs_name)
    if not matching:
        return (0, 0)

    for it in matching:
        item_id = it.get("id")
        if not item_id:
            continue
        attempted += 1

        # `permanentDelete=true` bypassa il recycle bin di ArcGIS Online
        # (introdotto nel 2024). Senza questo flag l'item finisce nel
        # cestino e il service NAME rimane RISERVATO per ~14 giorni,
        # impedendo `createService` di ricrearlo con lo stesso nome.
        del_url = f"{base_url}/content/users/{conn.username}/items/{item_id}/delete"
        del_data = {"f": "json", "permanentDelete": "true"}
        if token:
            del_data["token"] = token
        item_deleted = False
        try:
            dr = client._http.post(del_url, data=del_data, timeout=30, headers=referer)
            dres = dr.json()
            if dres.get("success"):
                item_deleted = True
                logger.info(
                    f"Item ArcGIS cancellato (permanent): {item_id} ('{fs_name}', "
                    f"folder={it.get('ownerFolder') or 'root'})"
                )
            else:
                logger.warning(f"delete item {item_id} risposta: {dres}")
                # Fallback: se il parametro permanentDelete non e' supportato
                # (Enterprise vecchie), prova senza flag + chiamata esplicita
                # a /permanentDelete come secondo step.
                dr2 = client._http.post(
                    del_url, data={"f": "json", **({"token": token} if token else {})},
                    timeout=30, headers=referer,
                )
                if dr2.json().get("success"):
                    # Tentativo di empty dal recycle bin
                    perm_url = f"{base_url}/content/users/{conn.username}/items/{item_id}/permanentDelete"
                    try:
                        pr = client._http.post(
                            perm_url, data={"f": "json", **({"token": token} if token else {})},
                            timeout=30, headers=referer,
                        )
                        if pr.json().get("success"):
                            item_deleted = True
                            logger.info(f"Item {item_id} permanent-deleted via fallback endpoint")
                    except Exception as pe:
                        logger.warning(f"permanentDelete fallback fallito: {pe}")
        except Exception as e:
            logger.warning(f"delete item {item_id} fallita: {e}")

        if item_deleted:
            succeeded += 1

    if attempted != succeeded:
        logger.warning(
            f"_delete_existing_service('{fs_name}'): partial delete "
            f"{succeeded}/{attempted} item cancellati — il nome potrebbe "
            f"essere ancora riservato"
        )
    return (attempted, succeeded)


def _add_layer_to_service(
    client,
    conn,
    service_url: str,
    gdf,
    layer_name: str,
    layer_id: int,
    srid: int = 4326,
) -> Dict:
    """Aggiunge un layer a un Feature Service esistente.

    Stesso pattern di `ArcGISClient.create_service` (addToDefinition + addFeatures)
    ma su un FS gia' creato e con id layer arbitrario (1, 2, 3, ...).
    """
    import geopandas as gpd

    token = client.authenticate(conn)
    referer = {"Referer": conn.server_url}
    admin_url = service_url.replace("/rest/services/", "/rest/admin/services/")

    # ── Determina geom_type dal GeoDataFrame (tipo maggioritario) ─────
    # Uso value_counts invece della prima feature: feature stray di tipo
    # diverso (es. 1 Point in mezzo a Polygon per errore di caricamento)
    # non deve determinare il tipo del layer — tutte le altre fallirebbero.
    geom_type = "esriGeometryPolygon"
    if isinstance(gdf, gpd.GeoDataFrame) and len(gdf) > 0 and gdf.geometry is not None:
        geom_types = gdf.geometry.dropna().geom_type
        if len(geom_types) > 0:
            majority_type = geom_types.value_counts().idxmax()
            geom_type = client._esri_geometry_type(majority_type)
            unique_types = set(geom_types)
            if len(unique_types) > 1:
                logger.warning(
                    f"_add_layer_to_service('{layer_name}'): DataFrame ha "
                    f"{len(unique_types)} tipi di geometria ({unique_types}); "
                    f"usato '{majority_type}', le altre potrebbero fallire."
                )
        if gdf.crs:
            srid = gdf.crs.to_epsg() or srid

    # ── Costruisci fields a partire dalle colonne (no geometry) ───────
    fields = [
        {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID", "editable": False},
    ]
    for col in gdf.columns:
        if col in ("geometry", "OBJECTID", "objectid"):
            continue
        fields.append(client._python_type_to_esri_field(col, gdf[col].dtype))

    # ── Calcola extent dai dati ───────────────────────────────────────
    extent = {
        "xmin": -180, "ymin": -90, "xmax": 180, "ymax": 90,
        "spatialReference": {"wkid": srid},
    }
    try:
        if isinstance(gdf, gpd.GeoDataFrame) and gdf.geometry is not None and len(gdf) > 0:
            bounds = gdf.geometry.dropna().total_bounds
            if len(bounds) == 4 and all(abs(b) < 1e15 for b in bounds):
                extent = {
                    "xmin": float(bounds[0]),
                    "ymin": float(bounds[1]),
                    "xmax": float(bounds[2]),
                    "ymax": float(bounds[3]),
                    "spatialReference": {"wkid": srid},
                }
    except Exception as e:
        logger.warning(f"Impossibile calcolare extent per {layer_name}: {e}")

    drawing_info = client._default_drawing_info(geom_type)

    layer_def = {
        "layers": [{
            "id": layer_id,
            "name": layer_name,
            "type": "Feature Layer",
            "geometryType": geom_type,
            "objectIdField": "OBJECTID",
            "fields": fields,
            "extent": extent,
            "drawingInfo": drawing_info,
            "hasAttachments": False,
            "capabilities": "Create,Delete,Query,Update,Editing",
        }]
    }

    # ── addToDefinition ────────────────────────────────────────────────
    add_def_data = {
        "addToDefinition": json.dumps(layer_def),
        "f": "json",
    }
    if token:
        add_def_data["token"] = token

    response = client._http.post(
        f"{admin_url}/addToDefinition",
        data=add_def_data,
        timeout=60,
        headers=referer,
    )
    add_result = response.json()
    if not add_result.get("success"):
        return {
            "success": False,
            "feature_count": 0,
            "errors": [f"addToDefinition fallito: {add_result}"],
        }

    # ── addFeatures ────────────────────────────────────────────────────
    layer_url = f"{service_url}/{layer_id}"
    features = client.dataframe_to_esri_features(
        gdf, "geometry", srid, operation="create_service"
    )
    write_result = client.add_features(conn, layer_url, features)

    return {
        "success": write_result.get("added_count", 0) > 0,
        "feature_count": write_result.get("added_count", 0),
        "errors": write_result.get("errors", []),
    }


# =============================================================================
# APPEND / UPSERT su FS esistente
# =============================================================================

def _find_existing_service(client, conn, fs_name: str, folder_id: str = "") -> tuple:
    """Cerca un Feature Service col nome dato IN QUALSIASI FOLDER dell'utente
    corrente (via /sharing/rest/search) e ritorna una tupla
    (service_url, item_id, owner_folder). owner_folder è la folder ArcGIS
    effettiva in cui vive l'item (None = root).
    Ritorna (None, None, None) se non esiste.
    """
    matching = _search_user_items(client, conn, fs_name)
    if not matching:
        return None, None, None
    it = matching[0]
    return it.get("url"), it.get("id"), it.get("ownerFolder")


def _quote_sql_value(v) -> str:
    """Quote un valore per una clausola WHERE SQL.

    Gestisce:
      - None / pd.NA / np.nan / pd.NaT   → 'NULL'
      - bool                              → 1 / 0 (ArcGIS non accetta True/False)
      - int / float / numpy int64/float64 → as-is, convertito a Python native
      - str (incluso numpy str)           → single-quoted, apici raddoppiati
      - altri tipi                        → str(v) + quoting

    La conversione a Python native e' necessaria perche' i GeoDataFrame
    caricati da pyogrio possono avere dtype numpy (int64, float64) che
    `isinstance(v, (int, float))` NON riconosce, con il risultato di passarli
    per il branch string producendo letterali tipo 'np.int64(123)'.
    """
    # NULL (incluso pd.NA / np.nan / pd.NaT)
    try:
        import pandas as _pd
        if v is None or _pd.isna(v):
            return "NULL"
    except (TypeError, ValueError):
        # pd.isna solleva su tipi non comparabili (es. tuple). Fallback:
        if v is None:
            return "NULL"

    # bool (prima di int, perche' True/False sono int in Python)
    # Gestisce anche numpy.bool_ che NON e' sottoclasse di Python bool.
    try:
        import numpy as _np
        if isinstance(v, (bool, _np.bool_)):
            return "1" if bool(v) else "0"
        if isinstance(v, _np.integer):
            return str(int(v))
        if isinstance(v, _np.floating):
            f = float(v)
            if f != f:  # NaN
                return "NULL"
            return repr(f)
    except ImportError:
        if isinstance(v, bool):
            return "1" if v else "0"

    # Integer / float Python nativi
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return "NULL" if v != v else repr(v)

    # Stringhe e tutto il resto
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _chunked(seq, size):
    """Yield chunks successivi di al massimo `size` elementi.

    Raises:
        ValueError: se size <= 0 (eviterebbe un infinite loop con buf
            che non raggiunge mai la condizione `len(buf) >= size`).
    """
    if size <= 0:
        raise ValueError(f"_chunked: size deve essere > 0, ricevuto {size}")
    buf = []
    for item in seq:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def _query_oid_by_key(
    client, conn, layer_url: str, match_col: str, values: list,
) -> Tuple[Dict[str, int], List[str]]:
    """Costruisce un dict {str(match_val): OBJECTID} interrogando il FS per
    le features con `match_col IN (values)`. Batcha la query su WHERE da 200.

    Se una o piu' batch falliscono, `oid_by_key` ritorna PARZIALE: il caller
    deve controllare `errors` (non-vuoto → parziale) e decidere se abortire
    o procedere. In caso di parziale viene loggato un warning con il conteggio.

    Returns (oid_by_key, errors).
    """
    token = client.authenticate(conn)
    referer = {"Referer": conn.server_url}
    oid_by_key: Dict[str, int] = {}
    errors: List[str] = []
    expected = len(values)
    batches_ok = 0
    batches_failed = 0

    for batch in _chunked(values, 200):
        where = f"{match_col} IN ({','.join(_quote_sql_value(v) for v in batch)})"
        params = {
            "where": where,
            "outFields": f"OBJECTID,{match_col}",
            "returnGeometry": "false",
            "f": "json",
        }
        if token:
            params["token"] = token
        try:
            r = client._http.get(f"{layer_url}/query", params=params, timeout=30, headers=referer)
            data = r.json()
            for feat in (data.get("features") or []):
                a = feat.get("attributes") or {}
                key = a.get(match_col)
                oid = a.get("OBJECTID") or a.get("objectid")
                if key is not None and oid is not None:
                    oid_by_key[str(key)] = int(oid)
            batches_ok += 1
        except Exception as e:
            batches_failed += 1
            errors.append(f"query OBJECTID batch {batches_ok + batches_failed}: {e}")

    if errors or (len(oid_by_key) < expected):
        logger.warning(
            f"_query_oid_by_key({match_col}): expected {expected} values, "
            f"resolved {len(oid_by_key)} OID. Batches: {batches_ok} ok, "
            f"{batches_failed} failed. Errors: {len(errors)}"
        )
    return oid_by_key, errors


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
      - upsert: per ogni batch di business-key chiama `delete_features` con
                WHERE IN (...) e poi `add_features` delle nuove feature
      - update: `update_features` con OBJECTID risolto tramite match column.
                Le feature del bundle senza match → skipped (niente insert).
    """
    import pandas as _pd

    # 1. Lista i layer del FS esistente (per risolvere name → url+id)
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
        # Pre-condizioni bloccanti:
        #   1. match_col esiste nel bundle
        #   2. nessun NULL in match_col
        #   3. nessun duplicato in match_col
        #   4. TUTTI i valori del bundle hanno un OBJECTID sul FS (no skip)
        # Se una fallisce → errore, nessuna chiamata a updateFeatures per il layer.
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
                    # iniettano OBJECTID sulle feature sbagliate (data loss).
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
        # (added per append/upsert, updated per update). Il frontend lo mostra
        # come "feat." con eventuali colonne extra deleted/skipped.
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
    # sul FS (add/update/delete). `skipped_count` NON conta come attivita':
    # se tutti i layer sono skipped, l'operazione non ha prodotto modifiche
    # e success=False (il frontend mostra "Errori presenti"). Questo evita
    # il falso positivo "✓ Pubblicato" quando in update strict tutti i
    # pre-check falliscono e ogni layer viene skippato integralmente.
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
