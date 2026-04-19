"""Contract minimo del processo backend avviato nei test funzionali.

Questi test non entrano ancora nei flussi autenticati: bloccano solo il
contratto di boot/health del processo FastAPI reale.
"""

from __future__ import annotations

import json
from urllib.request import urlopen


def test_backend_root_returns_json_contract(backend_server):
    with urlopen(f"{backend_server['base_url']}/", timeout=5.0) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("application/json")
        payload = json.loads(resp.read().decode("utf-8"))

    assert set(payload.keys()) == {"status", "service"}
    assert payload["status"] == "ok"
    assert payload["service"] == "PLENORA-ETL API"
