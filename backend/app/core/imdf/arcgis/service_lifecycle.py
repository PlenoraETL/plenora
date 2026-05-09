"""
Cluster SERVICE_LIFECYCLE - rimozione/cleanup Feature Service ArcGIS.

Keeps IMDF lifecycle policy in IMDF and delegates generic ArcGIS item/service
delete mechanics to the provider boundary.
"""

import importlib
from typing import Tuple


def _arcgis_lifecycle():
    """Lazy import: BE-GO-READY-ARCH-057 forbids `from app.core.features`
    imports inside `app.core.imdf`. Resolved dynamically via importlib."""
    return importlib.import_module("app.core.features.arcgis_service_lifecycle")


def _try_cleanup_zombie_service(client, conn, fs_name: str) -> Tuple[bool, str]:
    """Cancella un Feature Service "zombie".

    IMDF decides when this cleanup is attempted. The generic hosted admin
    describe + portal item delete mechanics live on the ArcGIS provider.
    """
    return _arcgis_lifecycle().try_cleanup_zombie_service(client, conn, fs_name)


def _delete_existing_service(client, conn, fs_name: str, folder_id: str = "") -> Tuple[int, int]:
    """Cerca un Feature Service col nome dato e lo cancella.

    Il parametro ``folder_id`` e' tenuto per retrocompatibilita' ma non
    restringe la ricerca: IMDF continua a cercare in qualsiasi folder
    dell'utente per evitare nomi ancora riservati fuori dalla folder target.

    Ritorna ``(attempted, succeeded)``. Il caller puo' skippare il check
    anti-zombie solo se ``attempted == succeeded > 0``.
    """
    return _arcgis_lifecycle().delete_existing_feature_service(
        client,
        conn,
        fs_name,
    )
