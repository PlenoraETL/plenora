"""Minimal ArcGIS provider protocol - read operations used by IMDF validation.

ADR-0023 / BE-GO-READY-ARCH-058-W5.

Defines only what ``imdf/validator.py`` needs from an ArcGIS client:
  - ``count_features_where``  - WHERE-filtered count via POST /query
  - ``list_layers``           - layer list for a Feature Service URL
  - ``get_layer_schema``      - field schema for a single layer

``ArcGISClient`` (``app.core.features.arcgis_client``) satisfies this
protocol via structural subtyping - no explicit declaration required
on the concrete class.

Leaf module: stdlib + ``arcgis_connection`` (same package).
No ``app.core.*``, no heavy runtime libraries.
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
