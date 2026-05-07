"""
ArcGIS connection info - lightweight provider-neutral dataclass.

ADR-0023 / BE-GO-READY-ARCH-058-W5: moved from
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ArcGISConnectionInfo:
    """Lightweight connection config consumed by the ArcGIS HTTP client.

    No metadata fields (name, creation date) - those live in the DB
    model (``ArcGISConnection``).  Analogous to ``DatabaseConnection``
    in ``query_executor.py``.
    """

    server_url: str
    server_type: str    # arcgis_online | arcgis_enterprise | arcgis_server
    auth_type: str      # token | oauth2 | public
    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""
    token_url: str = ""  # custom token-generation URL


__all__ = ["ArcGISConnectionInfo"]
