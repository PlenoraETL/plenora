"""
Cluster SERVICE_LIFECYCLE - rimozione/cleanup Feature Service ArcGIS.

Keeps IMDF lifecycle policy in IMDF and delegates generic ArcGIS item/service
delete mechanics to the provider boundary.
"""

from typing import Tuple

from app.utils.logger import get_logger

from .service_discovery import _search_user_items

logger = get_logger("app.core.imdf.arcgis_uploader")


def _try_cleanup_zombie_service(client, conn, fs_name: str) -> Tuple[bool, str]:
    """Cancella un Feature Service "zombie".

    IMDF decides when this cleanup is attempted. The generic hosted admin
    describe + portal item delete mechanics live on the ArcGIS provider.
    """
    return client.cleanup_zombie_feature_service(conn, fs_name)


def _delete_existing_service(client, conn, fs_name: str, folder_id: str = "") -> Tuple[int, int]:
    """Cerca un Feature Service col nome dato e lo cancella.

    Il parametro ``folder_id`` e' tenuto per retrocompatibilita' ma non
    restringe la ricerca: IMDF continua a cercare in qualsiasi folder
    dell'utente per evitare nomi ancora riservati fuori dalla folder target.

    Ritorna ``(attempted, succeeded)``. Il caller puo' skippare il check
    anti-zombie solo se ``attempted == succeeded > 0``.
    """
    matching = _search_user_items(client, conn, fs_name)
    if not matching:
        return (0, 0)

    attempted, succeeded = client.delete_feature_service_items(conn, matching, fs_name)

    if attempted != succeeded:
        logger.warning(
            f"_delete_existing_service('{fs_name}'): partial delete "
            f"{succeeded}/{attempted} item cancellati - il nome potrebbe "
            f"essere ancora riservato"
        )
    return (attempted, succeeded)
