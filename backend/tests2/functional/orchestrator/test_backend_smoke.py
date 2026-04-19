"""Smoke test del backend reale avviato come processo."""

from __future__ import annotations

import json
from urllib.request import urlopen


def test_backend_root_health(backend_server):
    with urlopen(f"{backend_server['base_url']}/", timeout=5.0) as resp:
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))

    assert payload["status"] == "ok"
    assert payload["service"] == "PLENORA-ETL API"
