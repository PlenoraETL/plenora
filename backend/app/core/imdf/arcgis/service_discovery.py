"""
Cluster SERVICE_DISCOVERY — folder + Feature Service discovery/lookup
sul portal ArcGIS.

Origine
=======
Estratto da `app.core.imdf.arcgis_uploader` come parte di Phase 5
Batch B4.2 (cluster split, vedi
`orchestrate/phase_5_b4/preflight.md` §6 + §6.1 facade pattern
ratificato in B1).

Razionale del raggruppamento: 6 helper che condividono il dominio
"discovery read-only su content/folder/items dell'utente ArcGIS",
chiamate REST con autenticazione token + Referer header:

  - `_list_user_folders`         — read-only: lista folder utente.
                                   Consumata dal validator per
                                   risolvere ownerFolder id → title.
  - `_ensure_folder`              — find-or-create folder; legge le
                                   folder direttamente e crea quella
                                   mancante quando necessario.
  - `_is_service_name_available` — check disponibilita' service name
                                   via `/portals/self/isServiceNameAvailable`.
  - `_enumerate_all_user_items`  — paginated enumeration tutti gli
                                   items (root + folders).
  - `_search_user_items`          — match Feature Service per fs_name
                                   con cascata title/name/url; chiama
                                   `_enumerate_all_user_items` intra-cluster.
  - `_find_existing_service`     — locate (url, item_id, ownerFolder)
                                   per fs_name; chiama `_search_user_items`
                                   intra-cluster.

Coupling test contract (CRITICO — strategia preservazione)
==========================================================
3 dei 6 helper sono `monkeypatch.setattr(uploader_mod, "<name>", fake)`
nei test contract:
  - `tests2/security/unit/test_imdf_validate_publish_folder_contract.py`
  - `tests2/security/unit/test_imdf_validate_publish_schema_contract.py`
  - `tests2/security/unit/test_imdf_validate_publish_robustness_contract.py`

Simboli patchati: `_find_existing_service`, `_is_service_name_available`,
`_list_user_folders`.

`monkeypatch.setattr` REBINDA l'attributo del modulo (NON muta in-place).
Strategia di preservazione, verificata pre-implementazione (cfr.
`PHASE_5_B4_PROPOSAL` §B4.2 mini-scan):

1. **Path A — `validator.publish_validate` lazy import**:
   `validator.py:130` fa `from app.core.imdf.arcgis_uploader import (
   ..., _find_existing_service, _is_service_name_available,
   _list_user_folders)` DENTRO la funzione. Questo lazy import legge
   l'attributo del modulo `arcgis_uploader` AL MOMENTO della chiamata,
   dopo che `monkeypatch.setattr(uploader_mod, "<name>", fake)` ha gia'
   rebindato. → vede il fake. ✓

2. **Path B — `upload_imdf_layers` interno a uploader.py**:
   `upload_imdf_layers` chiama i simboli per name lookup nel suo
   modulo (es. riga 191 `_find_existing_service(...)`). `uploader.py`
   re-exporta i 6 helper via `from .arcgis.service_discovery import
   ...`. Dopo `monkeypatch.setattr(uploader_mod, "_find_existing_service",
   fake)`, l'attributo `arcgis_uploader._find_existing_service` punta al
   fake (rebind locale al facade); il name lookup in `upload_imdf_layers`
   risolve allo stesso modulo `arcgis_uploader` → vede il fake. ✓

3. **Path C — chiamate intra-cluster (NON patchate dai test esistenti)**:
   `_find_existing_service` → `_search_user_items` →
   `_enumerate_all_user_items` vivono tutti in `service_discovery.py`.
   Questi name lookup interni risolvono nello scope del modulo figlio,
   NON nel facade `arcgis_uploader`. **Conseguenza**: un eventuale test
   futuro che patchasse `uploader_mod._search_user_items` e poi chiamasse
   `_find_existing_service` attraverso il modulo figlio non vedrebbe il
   fake. Nessun test contract attuale dipende da questo path; i test
   esistenti patchano e chiamano attraverso il facade/lazy import.
   Documentato qui per future modifiche del cluster.

`_search_user_items` e' chiamato da `_delete_existing_service`
(lifecycle, resta in `arcgis_uploader.py` post-B4.2). Quel chiamante
risolve `_search_user_items` per name lookup nello scope di uploader,
che lo re-exporta dal facade → la chiamata vive sempre attraverso il
facade attribute → patch propagation safe.

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.imdf.arcgis_uploader import _list_user_folders` /
    `_ensure_folder` / `_is_service_name_available` /
    `_enumerate_all_user_items` / `_search_user_items` /
    `_find_existing_service` continua a funzionare grazie al facade
    re-export in `arcgis_uploader.py`.
  - `validator.py:130` lazy-import `_find_existing_service` +
    `_is_service_name_available` + `_list_user_folders` continua a
    funzionare.
  - 3 test contract con `monkeypatch.setattr(uploader_mod, "<name>", ...)`
    continuano a funzionare (verificato sui 3 path A/B/C sopra).

Logger naming
=============
Logger creato esplicitamente come
`"app.core.imdf.arcgis_uploader"` (NON `__name__`) per preservare il
contratto osservabile pre-extraction: i record `_list_user_folders:
errore: ...`, `_is_service_name_available error: ...`,
`_enumerate_all_user_items: root fallita: ...`, `_search_user_items:
...` continuano ad apparire sotto il logger name dei consumer.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`arcgis_uploader.py` al momento di B4.2. Nessuna modifica di logica,
comment, log payload, ordine di operazione, fail-open di
`_is_service_name_available`, cascata di match in `_search_user_items`.
"""

from typing import List, Optional, Tuple
from urllib.parse import urlparse

from app.utils.logger import get_logger

logger = get_logger("app.core.imdf.arcgis_uploader")


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
