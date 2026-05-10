"""Builder per payload `POST /api/api-connections` (REST/ArcGIS).

Le factory di `ApiConnectionBuilder` ritornano dict pronti per
POST `/api/api-connections`. I nomi dei campi (`auth_type`,
`key_location`, `key_name`, `token_url`, ...) sono allineati con
quanto gia' usato dal `conftest.py` runner.

Pure function: nessun side-effect, nessuna I/O.
"""
from __future__ import annotations


class ApiConnectionBuilder:
    """Factory di payload per `POST /api/api-connections`."""

    @staticmethod
    def api_key(
        *,
        base_url: str,
        key: str = "PLENORA_TEST_KEY",
        location: str = "header",
        header: str = "X-API-Key",
        name: str = "e2e-api-key",
        description: str = "e2e REST api_key fixture",
    ) -> dict:
        """REST con autenticazione `api_key` (header o query).

        Args:
            location: ``"header"`` | ``"query"``.
            header: nome dell'header (o del query parameter) che porta
                la chiave. Default ``X-API-Key`` (header convention).
        """
        return {
            "name": name,
            "description": description,
            "connection_type": "rest",
            "base_url": base_url,
            "auth_type": "api_key",
            "api_key": key,
            "key_location": location,
            "key_name": header,
        }

    @staticmethod
    def bearer(
        *,
        base_url: str,
        token: str = "OAUTH_FAKE_TOKEN",
        name: str = "e2e-bearer",
        description: str = "e2e REST bearer fixture",
    ) -> dict:
        """REST con autenticazione `bearer` (token statico)."""
        return {
            "name": name,
            "description": description,
            "connection_type": "rest",
            "base_url": base_url,
            "auth_type": "bearer",
            "token": token,
        }

    @staticmethod
    def basic(
        *,
        base_url: str,
        username: str = "tester",
        password: str = "tester_pass",
        name: str = "e2e-basic",
        description: str = "e2e REST basic auth fixture",
    ) -> dict:
        """REST con autenticazione `basic` (RFC 7617)."""
        return {
            "name": name,
            "description": description,
            "connection_type": "rest",
            "base_url": base_url,
            "auth_type": "basic",
            "username": username,
            "password": password,
        }

    @staticmethod
    def oauth2_client_credentials(
        *,
        base_url: str,
        token_url: str,
        client_id: str = "plenora",
        client_secret: str = "plenora_secret",
        name: str = "e2e-oauth2-cc",
        description: str = "e2e REST OAuth2 client_credentials fixture",
    ) -> dict:
        """REST con OAuth2 grant `client_credentials`."""
        return {
            "name": name,
            "description": description,
            "connection_type": "rest",
            "base_url": base_url,
            "auth_type": "oauth2_client_credentials",
            "token_url": token_url,
            "client_id": client_id,
            "client_secret": client_secret,
        }

    @staticmethod
    def oauth2_password(
        *,
        base_url: str,
        token_url: str,
        username: str,
        password: str,
        client_id: str = "plenora",
        client_secret: str = "plenora_secret",
        name: str = "e2e-oauth2-pwd",
        description: str = "e2e REST OAuth2 password grant fixture",
    ) -> dict:
        """REST con OAuth2 `password` grant (resource owner)."""
        return {
            "name": name,
            "description": description,
            "connection_type": "rest",
            "base_url": base_url,
            "auth_type": "oauth2_password",
            "token_url": token_url,
            "username": username,
            "password": password,
            "client_id": client_id,
            "client_secret": client_secret,
        }

    @staticmethod
    def arcgis_token(
        *,
        base_url: str,
        token_url: str,
        username: str = "tester",
        password: str = "tester_pass",
        name: str = "e2e-arcgis",
        description: str = "e2e ArcGIS token fixture",
    ) -> dict:
        """ArcGIS REST connection con `generateToken`."""
        return {
            "name": name,
            "description": description,
            "connection_type": "arcgis",
            "base_url": base_url,
            "auth_type": "arcgis_token",
            "token_url": token_url,
            "username": username,
            "password": password,
        }
