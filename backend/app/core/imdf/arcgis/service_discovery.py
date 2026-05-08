"""
Cluster SERVICE_DISCOVERY — folder + Feature Service discovery/lookup

sul portal ArcGIS.
"""

from typing import List, Optional, Tuple

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISDiscoveryProviderProtocol
from app.utils.logger import get_logger

logger = get_logger("app.core.imdf.arcgis_uploader")


_IMDF_NON_USER_FOLDER_TITLES = {"", "(root)", "root", "system", "utilities"}


def _is_imdf_user_folder(folder: dict) -> bool:
    folder_id = str(folder.get("id") or "").strip()
    title = str(folder.get("title") or "").strip()
    if not folder_id:
        return False
    return title.lower() not in _IMDF_NON_USER_FOLDER_TITLES


def _list_user_folders(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
) -> List[dict]:
    """KEEP_COMPAT: read-only IMDF folder-list wrapper.

    Lista folder dell'utente corrente (senza crearne).
    Ritorna lista di dict [{id, title, ...}]. Usato dal validator per
    risolvere ownerFolder (id) → title prima di confrontare con la
    selezione utente.

    Guardrail: delegate discovery to `client.list_folders`; only IMDF folder
    filtering belongs here.
    """
    try:
        folders = client.list_folders(conn) or []
        return [folder for folder in folders if _is_imdf_user_folder(folder)]
    except Exception as e:
        logger.warning(f"_list_user_folders: errore: {e}")
        return []


def _ensure_folder(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    folder_name: str,
) -> Optional[str]:
    """KEEP_COMPAT: IMDF wrapper over the generic ArcGIS folder boundary.

    Guardrail: keep this as root-folder compatibility plus delegation to
    `client.ensure_folder`; new folder logic belongs in the provider.
    """
    if not folder_name:
        return ""
    return client.ensure_folder(conn, folder_name)


def _is_service_name_available(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    fs_name: str,
) -> Tuple[bool, Optional[str]]:
    """Controlla presso il portal se il nome servizio e' disponibile.

    KEEP_COMPAT: fail-open IMDF wrapper around
    `client.is_service_name_available`.

    Returns:
        (available, error_message):
            available = True → il nome e' libero, createService andra' a buon fine
            available = False → il nome e' occupato; error_message se disponibile
    """
    try:
        return client.is_service_name_available(conn, fs_name)
    except Exception as e:
        logger.warning(f"_is_service_name_available error: {e}")
        return True, str(e)  # fail-open: assume disponibile, poi createService dara' errore


def _enumerate_all_user_items(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
) -> List[dict]:
    """Enumera TUTTI gli item (in tutte le folder + root) dell'utente corrente.
    Approccio robusto che non dipende dall'endpoint /search (che in alcuni
    casi non matcha esattamente per via della tokenizzazione del titolo).

    Ritorna lista di item {id, title, type, name, url, ownerFolder, ...}.

    KEEP_COMPAT: IMDF wrapper over `client.list_user_items`; only
    Feature Service matching policy stays here.
    """
    try:
        return client.list_user_items(conn) or []
    except Exception as e:
        logger.warning(f"_enumerate_all_user_items: list_user_items fallita: {e}")
        return []


def _search_user_items(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    fs_name: str,
) -> List[dict]:
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

    # 3. name esatto (es. se fs_name e' il service name non il title
    matching = [it for it in fs_items
                if (it.get("name") or "") == fs_name
                or (it.get("name") or "").lower() == fs_name_lower]
    if matching:
        logger.info(f"_search_user_items: {len(matching)} match per name")
        return matching

    # 4. URL contiene fs_name (/services/{fs_name}/FeatureServer
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


def _find_existing_service(
    client: ArcGISDiscoveryProviderProtocol,
    conn: ArcGISConnectionInfo,
    fs_name: str,
    folder_id: str = "",
) -> tuple:
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
