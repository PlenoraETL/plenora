"""[phase-5-b4][batch3] Bugfix copertura — `_delete_existing_service`
contatori `attempted`/`succeeded` non inizializzati.

Pre-B4.3:
  - Il body di `_delete_existing_service` referenziava
    `attempted += 1` e `succeeded += 1` senza init esplicito.
  - Quando `matching` non era vuoto, il primo `attempted += 1`
    sollevava `UnboundLocalError`.
  - I test esistenti coprivano solo il path early-return
    `if not matching: return (0, 0)` — mascheravano il bug.

B4.3 chiude il debito dentro il cluster lifecycle (cfr. docstring di
`app/core/imdf/arcgis/service_lifecycle.py` sezione "Bugfix incluso
in B4.3 — UnboundLocalError contatori delete"). Init esplicito
`attempted = 0` e `succeeded = 0` aggiunto dopo l'early-return,
prima del loop.

Questo file aggiunge 2 test mirati sul path non coperto:
  1. **happy**: 1 item trovato, delete primario success →
     ritorna `(1, 1)` SENZA UnboundLocalError.
  2. **partial/fail**: 1 item trovato, delete primario fail +
     fallback fail → ritorna `(1, 0)`.

I test patchano `_search_user_items` (tramite il facade del modulo
service_lifecycle dove la chiamata avviene per name lookup importato)
e `client._http.post` per controllare lo stato senza chiamate REST
reali.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.imdf.arcgis_uploader import _delete_existing_service


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


class _FakeConn:
    """Stub minimale del ConnectionInfo richiesto da
    `_delete_existing_service`. Solo gli attributi acceduti dal body
    (server_url + username) sono necessari per i 2 test."""

    def __init__(self):
        self.server_url = "https://example.com/portal/sharing/rest"
        self.username = "u"


class _FakeResp:
    """Mimica `requests.Response.json()` per i 2 endpoint chiamati."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttp:
    """HTTP client a 2 risposte: primary `delete` + fallback dr2.
    `_query_oid_by_key` non e' raggiunto in questi test (no token
    `authenticate` ne' `query` calls). Il body di `_delete_existing_service`
    fa solo `client.authenticate(conn)` + `client._http.post(...)`."""

    def __init__(self, primary_payload, fallback_payload=None, perm_payload=None):
        self.primary_payload = primary_payload
        self.fallback_payload = fallback_payload
        self.perm_payload = perm_payload
        self.calls = []

    def post(self, url, data=None, timeout=None, headers=None):
        self.calls.append(url)
        # 1° call: /items/{id}/delete (con permanentDelete=true)
        # 2° call: stesso URL (fallback senza flag)
        # 3° call: /items/{id}/permanentDelete (fallback finale)
        if "/permanentDelete" in url:
            return _FakeResp(self.perm_payload or {"success": False})
        if len([c for c in self.calls if "/permanentDelete" not in c]) == 1:
            return _FakeResp(self.primary_payload)
        return _FakeResp(self.fallback_payload or {"success": False})


class _FakeClient:
    def __init__(self, http):
        self._http = http

    def authenticate(self, conn):
        return "tok"


# HAPPY PATH — primary delete success → (1, 1) NIENTE UnboundLocalError


def test_delete_existing_service_happy_path_returns_one_one():
    """Pre-B4.3: questo test sollevava UnboundLocalError sul primo
    `attempted += 1`. Post-B4.3: deve ritornare `(1, 1)` con
    `_search_user_items` che ritorna 1 item e delete primario success.
    """
    client = _FakeClient(
        _FakeHttp(primary_payload={"success": True})
    )
    conn = _FakeConn()

    with patch(
        "app.core.imdf.arcgis.service_lifecycle._search_user_items",
        return_value=[{"id": "item-abc", "ownerFolder": "root"}],
    ):
        result = _delete_existing_service(client, conn, "MyFS")

    assert result == (1, 1), (
        f"atteso (1, 1) sul happy path single-item; ottenuto {result}. "
        f"Pre-B4.3 questo path sollevava UnboundLocalError."
    )


# PARTIAL FAIL — primary fail + fallback fail → (1, 0)


def test_delete_existing_service_partial_fail_returns_one_zero():
    """1 item trovato; delete primario non-success; fallback senza flag
    pure non-success → succeeded resta 0, attempted = 1. Conferma che
    init `attempted = 0` / `succeeded = 0` regge anche su path di
    fallimento (no UnboundLocalError, contatori coerenti)."""
    client = _FakeClient(
        _FakeHttp(
            primary_payload={"success": False, "error": "denied"},
            fallback_payload={"success": False},
        )
    )
    conn = _FakeConn()

    with patch(
        "app.core.imdf.arcgis.service_lifecycle._search_user_items",
        return_value=[{"id": "item-xyz", "ownerFolder": None}],
    ):
        result = _delete_existing_service(client, conn, "MyFS")

    assert result == (1, 0), (
        f"atteso (1, 0) su partial-fail single-item; ottenuto {result}."
    )
