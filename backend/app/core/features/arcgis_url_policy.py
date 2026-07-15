"""Origin policy for authenticated ArcGIS HTTP requests."""

from __future__ import annotations

from urllib.parse import urlparse


def _effective_port(parsed) -> int:
    if parsed.port is not None:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def assert_arcgis_target_url(
    server_url: str,
    target_url: str,
    *,
    trusted_base_url: str | None = None,
) -> None:
    """Confine a target to the configured origin or a discovered base URL."""
    server = urlparse(server_url or "")
    target = urlparse(target_url or "")
    server_host = (server.hostname or "").lower().rstrip(".")
    target_host = (target.hostname or "").lower().rstrip(".")
    if not server_host or not target_host or target.scheme not in ("http", "https"):
        raise ValueError("URL ArcGIS non valido")

    if trusted_base_url:
        trusted = urlparse(trusted_base_url)
        trusted_host = (trusted.hostname or "").lower().rstrip(".")
        trusted_path = trusted.path.rstrip("/") + "/"
        target_path = target.path.rstrip("/") + "/"
        if (
            trusted.scheme == target.scheme
            and trusted_host == target_host
            and _effective_port(trusted) == _effective_port(target)
            and target_path.startswith(trusted_path)
        ):
            return

    server_is_online = server_host == "arcgis.com" or server_host.endswith(".arcgis.com")
    if server_is_online:
        allowed = target_host == "arcgis.com" or target_host.endswith(".arcgis.com")
    else:
        allowed = (
            target_host == server_host
            and _effective_port(target) == _effective_port(server)
        )
    if not allowed:
        raise ValueError("URL ArcGIS fuori dall'host autorizzato dalla connessione")


__all__ = ["assert_arcgis_target_url"]
