"""Generic ArcGIS SQL helper functions used by provider workflows."""

from __future__ import annotations

from typing import Iterator, List, Sequence, Tuple

from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.arcgis_provider import ArcGISReadProviderProtocol
from app.shared.contracts.payloads import IntMap
from app.shared.connections.arcgis_sql import (
    chunk_arcgis_values,
    quote_arcgis_sql_value,
)


def quote_sql_value(value: object) -> str:
    """Quote a Python value for an ArcGIS SQL WHERE literal."""
    return quote_arcgis_sql_value(value)


def chunked(values: Sequence[object], size: int) -> Iterator[Sequence[object]]:
    """Yield ordered chunks of at most ``size`` values."""
    yield from chunk_arcgis_values(values, size)


def query_oid_by_key(
    client: ArcGISReadProviderProtocol,
    conn: ArcGISConnectionInfo,
    layer_url: str,
    match_col: str,
    values: Sequence[object],
) -> Tuple[IntMap, List[str]]:
    """Resolve ``{business_key: OBJECTID}`` through the ArcGIS provider."""
    return client.query_oid_by_key(conn, layer_url, match_col, values)


__all__ = ["chunked", "query_oid_by_key", "quote_sql_value"]
