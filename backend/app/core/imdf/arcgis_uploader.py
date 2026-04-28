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
from typing import Dict, Optional

from app.utils.logger import get_logger

# Cleanup imports B4.4 (Phase 5 publish_ops extraction):
#   - `List` / `Tuple` rimossi da `typing` (erano usati solo nelle
#     signature di `_add_layer_to_service` / `_publish_mode_existing`,
#     extracted in B4.4) — solo `Dict` / `Optional` restano per la
#     signature di `upload_imdf_layers`.
#   - `from urllib.parse import urlparse` rimosso (era usato dai 6
#     helper di `service_discovery` extracted in B4.2 e dai 2 helper
#     di `service_lifecycle` extracted in B4.3 — l'orchestrator
#     `upload_imdf_layers` non ne fa uso diretto).
#   - `import requests` rimosso (top-level, mai usato direttamente
#     da `upload_imdf_layers`; i moduli figli usano `client._http`).
# `json` mantenuto: serve a `upload_imdf_layers` per
# `updateDefinition: json.dumps(update_def)` (renderers application).

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
            logger.info(f"Folder '{folder_name}' -> id={folder_id or '(root)'}")
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

# Phase 5 B4.2 cutover: cluster SERVICE_DISCOVERY (`_list_user_folders`,
# `_ensure_folder`, `_is_service_name_available`,
# `_enumerate_all_user_items`, `_search_user_items`,
# `_find_existing_service`) spostato in
# `app.core.imdf.arcgis.service_discovery`. Re-export in coda al file
# via facade. Vedi `orchestrate/phase_5_b4/preflight.md` §6.


# Phase 5 B4.3 cutover: cluster SERVICE_LIFECYCLE
# (`_try_cleanup_zombie_service`, `_delete_existing_service`) spostato
# in `app.core.imdf.arcgis.service_lifecycle`. Re-export in coda al
# file via facade. Vedi `orchestrate/phase_5_b4/preflight.md` §6.


# Phase 5 B4.4 cutover: cluster PUBLISH_OPS (`_add_layer_to_service`,
# `_publish_mode_existing`) spostato in
# `app.core.imdf.arcgis.publish_ops`. Re-export in coda al file via
# facade. Vedi `orchestrate/phase_5_b4/preflight.md` §6.


# =============================================================================
# APPEND / UPSERT su FS esistente
# =============================================================================

# Phase 5 B4.2 cutover: `_find_existing_service` spostato in
# `app.core.imdf.arcgis.service_discovery` (re-exported in coda
# al file via facade).


# Phase 5 B4.1 cutover: cluster SQL_HELPERS (_quote_sql_value,
# _chunked, _query_oid_by_key) spostato in
# `app.core.imdf.arcgis.sql_helpers`. Re-export in coda al file via
# facade. Le 3 funzioni sono internamente coese (`_query_oid_by_key`
# usa `_chunked` e `_quote_sql_value` per name lookup nello stesso
# nuovo modulo, NESSUNA dipendenza esterna verso `arcgis_uploader`).
#
# Invarianti pubbliche preservate:
#   - `from app.core.imdf.arcgis_uploader import _quote_sql_value` /
#     `_chunked` / `_query_oid_by_key` continua a funzionare grazie al
#     facade re-export.
#   - `validator.py:130` lazy-import `_quote_sql_value` + `_chunked`
#     da `app.core.imdf.arcgis_uploader` continua a funzionare.
#   - `tests2/shared/test_hypothesis_property_contract.py:16` con
#     `from app.core.imdf.arcgis_uploader import _quote_sql_value`
#     continua a funzionare.
# Vedi anche `orchestrate/phase_5_b4/preflight.md` §6.


# Phase 5 B4.4 cutover: `_publish_mode_existing` spostato in
# `app.core.imdf.arcgis.publish_ops` (re-exported in coda al file
# via facade).


# Phase 5 B4.1 cutover: cluster SQL_HELPERS spostato in
# `app.core.imdf.arcgis.sql_helpers`. Re-export per preservare i path
# pubblici esistenti:
#   - `from app.core.imdf.arcgis_uploader import _quote_sql_value` /
#     `_chunked` / `_query_oid_by_key`
#   - `validator.py:130` lazy-import `_quote_sql_value` + `_chunked`
#   - `tests2/shared/test_hypothesis_property_contract.py:16`
#     property test su `_quote_sql_value`
# Pattern §6.1 facade — single-source canonica vive in
# `arcgis/sql_helpers.py`. Vedi `orchestrate/phase_5_b4/preflight.md`
# §6 + §3.2 (semantica monkeypatch.setattr su uploader_mod).
from .arcgis.sql_helpers import (  # noqa: F401  (re-export)
    _quote_sql_value,
    _chunked,
    _query_oid_by_key,
)

# Phase 5 B4.2 cutover: cluster SERVICE_DISCOVERY (6 helper di
# folder/Feature Service discovery sul portal ArcGIS) spostato in
# `app.core.imdf.arcgis.service_discovery`. Re-export per preservare
# i path pubblici esistenti + il contract monkeypatch dei test:
#   - `from app.core.imdf.arcgis_uploader import _list_user_folders`,
#     `_ensure_folder`, `_is_service_name_available`,
#     `_enumerate_all_user_items`, `_search_user_items`,
#     `_find_existing_service` continua a funzionare.
#   - `validator.py:130` lazy-import di `_find_existing_service` +
#     `_is_service_name_available` + `_list_user_folders` continua a
#     funzionare (lazy import legge l'attributo del modulo facade
#     al call time, dopo eventuali monkeypatch).
#   - `monkeypatch.setattr(uploader_mod, "<helper>", fake)` nei 3 test
#     contract `tests2/security/unit/test_imdf_validate_publish_*.py`
#     continua a propagare:
#       * Path A — validator lazy import: legge l'attributo correntemente
#         bindato sul facade dopo il rebind. ✓
#       * Path B — `upload_imdf_layers` interno (uploader.py): name
#         lookup nello scope di uploader, che re-exporta i 6 simboli
#         come attributi. Rebind dell'attributo del facade visto dal
#         chiamante locale. ✓
#   - Path C — chiamate intra-cluster (`_ensure_folder` →
#     `_list_user_folders`, `_find_existing_service` → `_search_user_items`,
#     `_search_user_items` → `_enumerate_all_user_items`) avvengono
#     all'interno di `service_discovery.py`: name lookup nello scope
#     del modulo figlio, NON sul facade. Trade-off documentato
#     esplicitamente nella docstring di `service_discovery.py` —
#     nessun test contract attuale dipende da questo path.
# `_search_user_items` chiamato anche da `_delete_existing_service`
# (lifecycle, resta in uploader): name lookup in uploader → facade
# re-export → patch propagation safe.
# Pattern §6.1 facade. Vedi `orchestrate/phase_5_b4/preflight.md` §3.2
# (analisi semantica monkeypatch dettagliata).
from .arcgis.service_discovery import (  # noqa: F401  (re-export)
    _list_user_folders,
    _ensure_folder,
    _is_service_name_available,
    _enumerate_all_user_items,
    _search_user_items,
    _find_existing_service,
)

# Phase 5 B4.3 cutover: cluster SERVICE_LIFECYCLE (2 helper di
# rimozione/cleanup Feature Service) spostato in
# `app.core.imdf.arcgis.service_lifecycle`. Re-export per preservare i
# path pubblici esistenti + la propagazione di `monkeypatch.setattr`
# su `uploader_mod`:
#   - `from app.core.imdf.arcgis_uploader import
#     _try_cleanup_zombie_service` / `_delete_existing_service` continua
#     a funzionare (anche se nessun caller esterno li importa attualmente).
#   - Internal caller `upload_imdf_layers` (in uploader.py) usa name
#     lookup nello scope uploader → vede l'attributo facade rebindato
#     da eventuali `monkeypatch.setattr(uploader_mod, ...)` (Path B).
#   - Cross-cluster call `_delete_existing_service → _search_user_items`
#     avviene dentro `service_lifecycle.py` via
#     `from .service_discovery import _search_user_items`. Patch su
#     `uploader_mod._search_user_items` NON sarebbe vista da questo
#     path (Path C — nessun test contract attuale dipende, verificato
#     in B4.2 mini-scan).
# Pattern §6.1 facade. Vedi `orchestrate/phase_5_b4/preflight.md` §3.2.
from .arcgis.service_lifecycle import (  # noqa: F401  (re-export)
    _try_cleanup_zombie_service,
    _delete_existing_service,
)

# Phase 5 B4.4 cutover: cluster PUBLISH_OPS (2 helper di scrittura
# layer-level su Feature Service: replace + append/upsert/update)
# spostato in `app.core.imdf.arcgis.publish_ops`. Re-export per
# preservare i path pubblici esistenti + la propagazione di
# `monkeypatch.setattr` su `uploader_mod`:
#   - `from app.core.imdf.arcgis_uploader import _add_layer_to_service`
#     / `_publish_mode_existing` continua a funzionare (anche se nessun
#     caller esterno li importa attualmente).
#   - Internal caller `upload_imdf_layers` (in uploader.py) usa name
#     lookup nello scope uploader → vede l'attributo facade rebindato
#     da eventuali `monkeypatch.setattr(uploader_mod, ...)` (Path B).
#   - Cross-cluster call `_publish_mode_existing → _query_oid_by_key`
#     / `_chunked` / `_quote_sql_value` avviene dentro `publish_ops.py`
#     via `from .sql_helpers import ...`. Patch su
#     `uploader_mod.<sql_helper>` NON sarebbe vista da questo path
#     (Path C — nessun test contract attuale dipende, verificato in
#     B4.1 mini-scan: solo property test su `_quote_sql_value`,
#     no monkeypatch).
# Pattern §6.1 facade. Vedi `orchestrate/phase_5_b4/preflight.md` §3.2.
from .arcgis.publish_ops import (  # noqa: F401  (re-export)
    _add_layer_to_service,
    _publish_mode_existing,
)
