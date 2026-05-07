"""
Minimal ArcGIS provider protocol - read operations used by IMDF validation.

"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo


@runtime_checkable
class ArcGISProviderProtocol(Protocol):
    """Protocol for ArcGIS read operations consumed by IMDF validation.

    Annotate ``client`` local variables against this protocol so that:
      - type checkers verify the concrete client satisfies the interface;
      - the validator is testable with any object that matches the protocol
        (structural subtyping, no import of the concrete client needed).

    ``@runtime_checkable`` lets callers use ``isinstance(client, ArcGISProviderProtocol)``
    for defensive checks without importing the concrete class.
    """

    def count_features_where(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        where: str,
        *,
        timeout: float = 60.0,
    ) -> int:
        """Count features matching a WHERE clause via POST /query."""
        ...

    def list_layers(
        self,
        conn: ArcGISConnectionInfo,
        service_url: str,
    ) -> List[dict]:
        """List layers of a Feature Service URL."""
        ...

    def get_layer_schema(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
    ) -> dict:
        """Return field schema (fields, spatial reference) for a single layer."""
        ...


__all__ = ["ArcGISProviderProtocol"]
