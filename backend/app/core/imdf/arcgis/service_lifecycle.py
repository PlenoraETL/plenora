"""
Cluster SERVICE_LIFECYCLE — rimozione/cleanup Feature Service ArcGIS.

Keeps provider-specific behavior behind a narrow integration boundary.
"""

from typing import Tuple
from urllib.parse import urlparse

from app.utils.logger import get_logger

from .service_discovery import _search_user_items

logger = get_logger("app.core.imdf.arcgis_uploader")


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

    # Bugfix B4.3: i contatori erano referenziati con `+= 1` senza
    # init esplicito — `UnboundLocalError` non appena `matching` era
    # non-vuoto. Il path early-return sopra mascherava il bug nei test
    # esistenti. Init esplicito qui prima del loop. Test di copertura:
    # `tests2/imdf/unit/test_delete_existing_service_counters.py`.
    attempted = 0
    succeeded = 0

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
