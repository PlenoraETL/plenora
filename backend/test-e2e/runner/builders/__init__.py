"""Test data builders per i test E2E plenora.

Espone factory fluent per costruire i payload HTTP dei principali
endpoint plenora senza dover scrivere dict letterali in ogni test:

    * :class:`CanvasBuilder`        -> body PUT /api/projects/{id}/canvas
    * :class:`ConnectionBuilder`    -> body POST /api/connections
    * :class:`ApiConnectionBuilder` -> body POST /api/api-connections
    * :class:`DatasourceBuilder`    -> body POST /api/datasources

I builder sono *pure functions*: nessuna I/O, nessun side-effect.
Input -> dict.
"""
from .api_connection import ApiConnectionBuilder
from .canvas import CanvasBuilder
from .connection import ConnectionBuilder
from .datasource import DatasourceBuilder

__all__ = [
    "ApiConnectionBuilder",
    "CanvasBuilder",
    "ConnectionBuilder",
    "DatasourceBuilder",
]
