"""HTTP adapter protocol accepted by the ArcGIS provider composition root."""

from __future__ import annotations

from typing import Any, Protocol


class ArcGISProviderHttpClientProtocol(Protocol):
    """Minimal HTTP client shape forwarded to the concrete ArcGIS provider."""

    def get(
        self,
        url: str,
        params: dict | None = None,
        timeout: int = 30,
        headers: dict | None = None,
    ) -> Any: ...

    def post(
        self,
        url: str,
        data: dict | None = None,
        timeout: int = 30,
        headers: dict | None = None,
    ) -> Any: ...


__all__ = ["ArcGISProviderHttpClientProtocol"]
