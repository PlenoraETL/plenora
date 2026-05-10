"""Routers for the Plenora E2E fake REST server.

Each module exposes a ``router`` (``fastapi.APIRouter``) that is mounted in
``server.py`` under a fixed prefix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Static credentials shared by every router. They live here so a single
# constant is the source of truth for both the endpoints and the README.
API_KEY = "PLENORA_TEST_KEY"
BEARER_TOKEN = "PLENORA_TEST_BEARER"
BASIC_USER = "tester"
BASIC_PASS = "tester_pass"
OAUTH_CLIENT_ID = "plenora"
OAUTH_CLIENT_SECRET = "plenora_secret"
OAUTH_TOKEN = "OAUTH_FAKE_TOKEN"
ARCGIS_USER = "tester"
ARCGIS_PASS = "tester_pass"
ARCGIS_TOKEN = "ARCGIS_FAKE_TOKEN"
# Token issued by the ArcGIS-style OAuth2 client_credentials flow. Some
# ArcGIS endpoints accept either ARCGIS_TOKEN (generateToken-style) or
# this OAuth bearer token.
ARCGIS_OAUTH_TOKEN = "ARCGIS_OAUTH_TOKEN"
ARCGIS_OAUTH_CLIENT_ID = "plenora"
ARCGIS_OAUTH_CLIENT_SECRET = "plenora_secret"
# SharePoint REST API + Azure AD emulation. The fake-rest server exposes a
# bare-bones subset of the SharePoint REST surface plus AAD-style token /
# error endpoints (used by tests that exercise the SharePoint connector).
SHAREPOINT_FAKE_TOKEN = "SHAREPOINT_FAKE_TOKEN"
SHAREPOINT_OAUTH_CLIENT_ID = "plenora_sp"
SHAREPOINT_OAUTH_CLIENT_SECRET = "plenora_sp_secret"

# Path to the bundled fixtures (resolved once, used by every router).
FIXTURES_DIR: Path = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Load and return a JSON/GeoJSON fixture by file name."""
    path = FIXTURES_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)
