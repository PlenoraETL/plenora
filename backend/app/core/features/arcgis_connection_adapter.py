"""KEEP_COMPAT wrapper for the moved ArcGIS connection resolver."""

from __future__ import annotations

from app.core.connections.arcgis_connection_resolver import resolve_arcgis_connection


__all__ = ["resolve_arcgis_connection"]
