"""Contract test — validazione shape delle response degli endpoint critici.

Questi test girano **prima** di tutti gli altri (Fase 1 smoke incluso) e
verificano lo *schema* delle response degli endpoint plenora critici.
NON verificano la logica di business, solo che i builders/factories
(`runner/builders/`, `runner/factories.py`) producano payload accettati
dal backend e che le response abbiano la shape attesa.

Filosofia:
- shallow-but-stable: verifica `set(keys)` e tipi, non valori semantici.
- skip graceful se l'app non è raggiungibile (la fixture
  `plenora_app_alive` di conftest skippa già con messaggio chiaro).
- se uno di questi rompe, va aggiornato il **layer builders** PRIMA di
  scrivere altri test contro un modello mentale ormai sbagliato.

Catalogo: CT-001 .. CT-006 in `test-e2e/catalog/contract.yaml`.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest

# I builders sono opzionali (Agent B): se non importabili, fallback a
# dict inline. Non skippiamo il test se mancano — il senso del contract
# test è che il backend rifiuti il dict, e il fix sia importare/aggiornare
# il builder.
try:
    from runner.builders import CanvasBuilder, ConnectionBuilder
    BUILDERS_AVAILABLE = True
except ImportError:  # pragma: no cover
    CanvasBuilder = None  # type: ignore[assignment]
    ConnectionBuilder = None  # type: ignore[assignment]
    BUILDERS_AVAILABLE = False

try:
    from runner import factories as F
    FACTORIES_AVAILABLE = True
except ImportError:  # pragma: no cover
    F = None  # type: ignore[assignment]
    FACTORIES_AVAILABLE = False


API_PREFIX = "/api"


# ── Helper schema check ────────────────────────────────────────────────

def _assert_keys(
    body: dict,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    """Verifica che ``body`` abbia tutte le ``required`` keys.

    Le ``optional`` sono accettate se presenti; eventuali keys non
    elencate (in `required` o `optional`) sono ammesse silenziosamente —
    il backend è libero di aggiungere campi compatibilmente.

    Fallisce con messaggio descrittivo che elenca le keys mancanti +
    body troncato (utile per debug rapido del contract).
    """
    missing = required - body.keys()
    assert not missing, (
        f"Body senza keys richieste: {sorted(missing)}. "
        f"Body keys presenti: {sorted(body.keys())}. "
        f"Body: {str(body)[:300]}"
    )


def _maybe_id(body: Any) -> str | None:
    """Estrae un id da risposte plenora con shape eterogenea (id | <res>_id)."""
    if not isinstance(body, dict):
        return None
    for key in ("id", "connection_id", "datasource_id", "project_id",
                "api_id", "api_connection_id", "task_id"):
        value = body.get(key)
        if value:
            return str(value)
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        return _maybe_id(metadata)
    return None


# ── CT-001 — POST /api/auth/login shape ────────────────────────────────

@pytest.mark.contract
@pytest.mark.profile_core
@pytest.mark.requires_app
def test_ct_001_login_response_shape(
    e2e_config,
    plenora_app_alive,
    http_client: httpx.Client,
) -> None:
    """CT-001 — `POST /api/auth/login` shape conforme a TokenResponse.

    Ref: ``app/core/auth/models.py::TokenResponse``.
    Required keys: access_token, token_type, user_id, username, group_id,
    role, expires_in. Tipi: access_token str non vuoto, token_type
    "bearer" (case-insensitive), expires_in int > 0.
    """
    payload = {"username": e2e_config.user, "password": e2e_config.password}
    r = http_client.post(f"{API_PREFIX}/auth/login", json=payload)

    assert r.status_code == 200, (
        f"Login deve tornare 200; ricevuto {r.status_code}. Body: {r.text[:300]}"
    )
    body = r.json()
    assert isinstance(body, dict), f"Body login non è dict: {type(body)}"

    _assert_keys(
        body,
        required={
            "access_token", "token_type", "user_id", "username",
            "group_id", "role", "expires_in",
        },
    )

    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 10, (
        f"access_token shape sbagliata: {body['access_token']!r}"
    )
    assert body["token_type"].lower() == "bearer", (
        f"token_type atteso 'bearer', ricevuto {body['token_type']!r}"
    )
    assert isinstance(body["expires_in"], int) and body["expires_in"] > 0, (
        f"expires_in atteso int positivo, ricevuto {body['expires_in']!r}"
    )
    for str_key in ("user_id", "username", "group_id", "role"):
        assert isinstance(body[str_key], str) and body[str_key], (
            f"{str_key} deve essere str non vuoto: {body[str_key]!r}"
        )


# ── CT-002 — POST /api/connections shape (postgres) ────────────────────

@pytest.mark.contract
@pytest.mark.profile_core
@pytest.mark.requires_app
def test_ct_002_connections_post_shape(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    """CT-002 — `POST /api/connections` con payload postgres builder.

    Verifica:
    - Status 200/201 con payload valido (ConnectionBuilder.postgres).
    - Response body ha campo id (`id` o `connection_id`).
    - Idempotenza body: rifare POST stesso `name` → 4xx (409/422/400)
      e NON 5xx (il backend deve sapere distinguere conflict da crash).
    """
    name = f"ct-pg-{uuid.uuid4().hex[:6]}"
    if BUILDERS_AVAILABLE and ConnectionBuilder is not None:
        payload = ConnectionBuilder.postgres(name=name)
    else:
        payload = {
            "name": name,
            "description": "ct-002 fallback",
            "type": "postgres",
            "host": "plenora-e2e-postgres",
            "port": 5432,
            "database": "plenora_test",
            "username": "plenora",
            "password": "plenora_test_pwd",
        }

    r = http_client.post(
        f"{API_PREFIX}/connections", json=payload, headers=auth_headers,
    )
    if r.status_code >= 500:
        pytest.fail(
            f"POST /api/connections ha ritornato 5xx con payload builder valido: "
            f"HTTP {r.status_code} — {r.text[:300]}"
        )
    assert r.status_code in (200, 201), (
        f"Atteso 200/201, ricevuto {r.status_code}. Body: {r.text[:300]}"
    )

    body = r.json()
    assert isinstance(body, dict), f"Body create connection non è dict: {type(body)}"
    cid = _maybe_id(body)
    assert cid, f"Response senza id riconoscibile: {body}"

    # Cleanup-aware: cattura cid per il finally
    try:
        # Idempotenza body: stessa POST con stesso name -> 4xx, NON 5xx
        r2 = http_client.post(
            f"{API_PREFIX}/connections", json=payload, headers=auth_headers,
        )
        assert r2.status_code < 500, (
            f"POST duplicata deve tornare 4xx, ricevuto 5xx: "
            f"HTTP {r2.status_code} — {r2.text[:300]}"
        )
        assert 400 <= r2.status_code < 500, (
            f"POST duplicata deve tornare 4xx (idealmente 409/422), ricevuto "
            f"{r2.status_code}. Body: {r2.text[:300]}"
        )
    finally:
        # Best-effort cleanup
        try:
            http_client.delete(
                f"{API_PREFIX}/connections/{cid}", headers=auth_headers,
            )
        except httpx.HTTPError:
            pass


# ── CT-003 — POST /api/connections/{id}/test shape ─────────────────────

@pytest.mark.contract
@pytest.mark.profile_core
@pytest.mark.requires_app
def test_ct_003_connection_test_response_shape(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    """CT-003 — `POST /api/connections/{id}/test` shape ConnectionTestResult.

    Ref: contratto `{success: bool, message: str, details?: dict}`
    (NON `{ok, latency_ms}` come scritto nel catalogo legacy).

    Sia ``success: true`` che ``success: false`` sono response valide:
    questo è un test di shape, NON di logica connettività.
    """
    name = f"ct-pg-test-{uuid.uuid4().hex[:6]}"
    if BUILDERS_AVAILABLE and ConnectionBuilder is not None:
        create_payload = ConnectionBuilder.postgres(name=name)
    else:
        create_payload = {
            "name": name,
            "description": "ct-003 fallback",
            "type": "postgres",
            "host": "plenora-e2e-postgres",
            "port": 5432,
            "database": "plenora_test",
            "username": "plenora",
            "password": "plenora_test_pwd",
        }

    r_create = http_client.post(
        f"{API_PREFIX}/connections", json=create_payload, headers=auth_headers,
    )
    if r_create.status_code >= 400:
        pytest.skip(
            f"Setup CT-003 fallito (POST connections): "
            f"HTTP {r_create.status_code} — {r_create.text[:200]}"
        )
    cid = _maybe_id(r_create.json())
    if not cid:
        pytest.skip(f"Setup CT-003: response create senza id: {r_create.json()}")

    try:
        r = http_client.post(
            f"{API_PREFIX}/connections/{cid}/test",
            json={}, headers=auth_headers,
        )
        assert r.status_code == 200, (
            f"Atteso 200 (test connection ritorna sempre 200 con success bool); "
            f"ricevuto {r.status_code}. Body: {r.text[:300]}"
        )
        body = r.json()
        assert isinstance(body, dict), (
            f"Body test connection non è dict: {type(body)}"
        )
        _assert_keys(body, required={"success", "message"})
        assert isinstance(body["success"], bool), (
            f"success deve essere bool, ricevuto {type(body['success']).__name__}: "
            f"{body['success']!r}"
        )
        assert isinstance(body["message"], str), (
            f"message deve essere str, ricevuto {type(body['message']).__name__}"
        )
        # `details` è opzionale; se presente deve essere dict
        if "details" in body and body["details"] is not None:
            assert isinstance(body["details"], dict), (
                f"details, se presente, deve essere dict: "
                f"{type(body['details']).__name__}"
            )
    finally:
        try:
            http_client.delete(
                f"{API_PREFIX}/connections/{cid}", headers=auth_headers,
            )
        except httpx.HTTPError:
            pass


# ── CT-004 — POST /api/projects shape ──────────────────────────────────

@pytest.mark.contract
@pytest.mark.profile_core
@pytest.mark.requires_app
def test_ct_004_projects_post_shape(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """CT-004 — `POST /api/projects` shape ProjectCreate.

    Verifica:
    - 200/201 con response che ha id (`id` o `project_id`).
    - Payload NON include `group_id` esplicito (deriva da JWT lato BE).
    - Cleanup: `DELETE /api/projects/{id}` ritorna 200/204 (best-effort).
    """
    name = f"ct-prj-{uuid.uuid4().hex[:6]}"
    if FACTORIES_AVAILABLE and F is not None:
        payload = F.project_estrai_payload(
            name=name, default_connection_id=postgres_conn_id,
        )
    else:
        payload = {
            "name": name,
            "description": "ct-004 fallback",
            "project_type": "estrai",
            "default_connection_id": postgres_conn_id,
        }

    # Vincolo di contract: il payload NON deve contenere group_id
    assert "group_id" not in payload, (
        f"Il payload project NON deve includere group_id (deriva da JWT). "
        f"Payload contiene: {list(payload.keys())}"
    )

    r = http_client.post(
        f"{API_PREFIX}/projects", json=payload, headers=auth_headers,
    )
    if r.status_code >= 500:
        pytest.fail(
            f"POST /api/projects 5xx con payload factory valido: "
            f"HTTP {r.status_code} — {r.text[:300]}"
        )
    assert r.status_code in (200, 201), (
        f"Atteso 200/201, ricevuto {r.status_code}. Body: {r.text[:300]}"
    )

    body = r.json()
    assert isinstance(body, dict), f"Body create project non è dict: {type(body)}"
    pid = _maybe_id(body)
    assert pid, f"Response project senza id riconoscibile: {body}"

    # Cleanup
    r_del = http_client.delete(
        f"{API_PREFIX}/projects/{pid}", headers=auth_headers,
    )
    assert r_del.status_code in (200, 204, 202, 404), (
        f"DELETE project deve tornare 200/204/202/404; ricevuto "
        f"{r_del.status_code}. Body: {r_del.text[:200]}"
    )


# ── CT-005 — PUT /api/projects/{id}/canvas shape ───────────────────────

@pytest.mark.contract
@pytest.mark.profile_core
@pytest.mark.requires_app
def test_ct_005_canvas_put_shape(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """CT-005 — `PUT /api/projects/{id}/canvas` shape CanvasWithPlugins.

    Usa CanvasBuilder().with_estrai_sql(...) con connection_id postgres
    appena creata. Verifica:
    - 200 con shape persistita.
    - `GET /api/projects/{id}` riflette il canvas (presenza di campi
      canvas/nodes/edges nella response del progetto).
    """
    # Setup project
    project_name = f"ct-canvas-{uuid.uuid4().hex[:6]}"
    project_payload = {
        "name": project_name,
        "description": "ct-005 project",
        "project_type": "estrai",
        "default_connection_id": postgres_conn_id,
    }
    r_proj = http_client.post(
        f"{API_PREFIX}/projects", json=project_payload, headers=auth_headers,
    )
    if r_proj.status_code >= 400:
        pytest.skip(
            f"Setup CT-005 (POST project) fallito: "
            f"HTTP {r_proj.status_code} — {r_proj.text[:200]}"
        )
    pid = _maybe_id(r_proj.json())
    if not pid:
        pytest.skip(f"Setup CT-005: project senza id: {r_proj.json()}")

    try:
        # Build canvas via CanvasBuilder (fallback dict inline)
        if BUILDERS_AVAILABLE and CanvasBuilder is not None:
            canvas_body = (
                CanvasBuilder()
                .with_estrai_sql(connection_id=postgres_conn_id, table="customers")
                .build()
            )
        else:
            canvas_body = {
                "nodes": [
                    {
                        "id": "src_ct005",
                        "type": "source",
                        "position": {"x": 0, "y": 0},
                        "connection_id": postgres_conn_id,
                        "table_name": "customers",
                        "alias": "customers",
                    }
                ],
                "edges": [],
            }

        # PUT canvas
        r_put = http_client.put(
            f"{API_PREFIX}/projects/{pid}/canvas",
            json=canvas_body, headers=auth_headers,
        )
        if r_put.status_code >= 500:
            pytest.fail(
                f"PUT canvas 5xx con builder valido: HTTP {r_put.status_code} — "
                f"{r_put.text[:300]}"
            )
        assert r_put.status_code in (200, 201, 204), (
            f"Atteso 200/201/204 da PUT canvas, ricevuto {r_put.status_code}. "
            f"Body: {r_put.text[:300]}"
        )

        # GET project per verificare riflesso del canvas
        r_get = http_client.get(
            f"{API_PREFIX}/projects/{pid}", headers=auth_headers,
        )
        assert r_get.status_code == 200, (
            f"GET project dopo PUT canvas atteso 200; ricevuto "
            f"{r_get.status_code}. Body: {r_get.text[:200]}"
        )
        body = r_get.json()
        assert isinstance(body, dict), f"GET project non è dict: {type(body)}"

        # Il canvas può essere inline (`nodes`/`edges` top-level) o sotto
        # chiave `canvas`/`canvas_state`. Accettiamo qualsiasi delle forme:
        # è la shape del *contract* a essere oggetto del test.
        has_canvas_shape = (
            ("nodes" in body and "edges" in body)
            or ("canvas" in body and isinstance(body["canvas"], dict))
            or ("canvas_state" in body and isinstance(body["canvas_state"], dict))
        )
        assert has_canvas_shape, (
            f"GET project dopo PUT canvas non riflette il canvas. "
            f"Keys body: {sorted(body.keys())}. Body: {str(body)[:300]}"
        )
    finally:
        try:
            http_client.delete(
                f"{API_PREFIX}/projects/{pid}", headers=auth_headers,
            )
        except httpx.HTTPError:
            pass


# ── CT-006 — POST /api/projects/{id}/execute shape ─────────────────────

@pytest.mark.contract
@pytest.mark.profile_core
@pytest.mark.requires_app
def test_ct_006_project_execute_shape(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """CT-006 — `POST /api/projects/{id}/execute` shape task_info.

    Verifica:
    - 200/202 con `task_id` (o `id`) nel body.
    - Poll `GET /api/tasks/{task_id}` ritorna status terminale entro 60s
      (no asserzione su success/fail — solo shape).
    """
    # Setup project + canvas minimo
    project_payload = {
        "name": f"ct-exec-{uuid.uuid4().hex[:6]}",
        "description": "ct-006 project",
        "project_type": "estrai",
        "default_connection_id": postgres_conn_id,
    }
    r_proj = http_client.post(
        f"{API_PREFIX}/projects", json=project_payload, headers=auth_headers,
    )
    if r_proj.status_code >= 400:
        pytest.skip(
            f"Setup CT-006 (POST project) fallito: "
            f"HTTP {r_proj.status_code} — {r_proj.text[:200]}"
        )
    pid = _maybe_id(r_proj.json())
    if not pid:
        pytest.skip(f"Setup CT-006: project senza id: {r_proj.json()}")

    try:
        # PUT canvas minimo (1 source)
        if BUILDERS_AVAILABLE and CanvasBuilder is not None:
            canvas_body = (
                CanvasBuilder()
                .with_estrai_sql(connection_id=postgres_conn_id, table="customers")
                .build()
            )
        else:
            canvas_body = {
                "nodes": [
                    {
                        "id": "src_ct006",
                        "type": "source",
                        "position": {"x": 0, "y": 0},
                        "connection_id": postgres_conn_id,
                        "table_name": "customers",
                        "alias": "customers",
                    }
                ],
                "edges": [],
            }
        r_canvas = http_client.put(
            f"{API_PREFIX}/projects/{pid}/canvas",
            json=canvas_body, headers=auth_headers,
        )
        if r_canvas.status_code >= 400:
            pytest.skip(
                f"Setup CT-006 (PUT canvas) fallito: "
                f"HTTP {r_canvas.status_code} — {r_canvas.text[:200]}"
            )

        # Execute
        r_exec = http_client.post(
            f"{API_PREFIX}/projects/{pid}/execute",
            json={}, headers=auth_headers,
        )
        if r_exec.status_code >= 500:
            pytest.fail(
                f"POST execute 5xx: HTTP {r_exec.status_code} — "
                f"{r_exec.text[:300]}"
            )
        assert r_exec.status_code in (200, 201, 202), (
            f"Atteso 200/201/202 da execute, ricevuto {r_exec.status_code}. "
            f"Body: {r_exec.text[:300]}"
        )
        exec_body = r_exec.json()
        assert isinstance(exec_body, dict), f"execute body non è dict: {type(exec_body)}"

        task_id = exec_body.get("task_id") or exec_body.get("id")
        if not task_id and isinstance(exec_body.get("task"), dict):
            task_id = exec_body["task"].get("task_id")
        assert task_id, (
            f"execute body senza task_id riconoscibile. "
            f"Keys: {sorted(exec_body.keys())}. Body: {str(exec_body)[:300]}"
        )

        # Poll fino a stato terminale o timeout 60s
        deadline = time.monotonic() + 60.0
        last_body: dict[str, Any] = {}
        terminal = {"success", "failure", "cancelled", "error",
                    "completed", "failed"}
        terminal_reached = False
        while time.monotonic() < deadline:
            r_task = http_client.get(
                f"{API_PREFIX}/tasks/{task_id}", headers=auth_headers,
            )
            if r_task.status_code >= 500:
                pytest.fail(
                    f"GET /tasks/{{id}} 5xx: HTTP {r_task.status_code} — "
                    f"{r_task.text[:300]}"
                )
            if r_task.status_code == 404:
                # task non ancora visibile, ritenta
                time.sleep(1.0)
                continue
            assert r_task.status_code == 200, (
                f"Atteso 200 da GET task; ricevuto {r_task.status_code}. "
                f"Body: {r_task.text[:200]}"
            )
            last_body = r_task.json() if r_task.headers.get(
                "content-type", ""
            ).startswith("application/json") else {}
            status = (last_body.get("status") or "").lower()
            if status in terminal:
                terminal_reached = True
                break
            time.sleep(1.0)

        assert terminal_reached, (
            f"Task {task_id} non ha raggiunto stato terminale entro 60s. "
            f"Ultimo body: {str(last_body)[:300]}"
        )
        # Shape task body: deve avere almeno `status` (str)
        assert "status" in last_body, (
            f"Task body senza key `status`: {sorted(last_body.keys())}"
        )
        assert isinstance(last_body["status"], str), (
            f"`status` deve essere str, ricevuto {type(last_body['status']).__name__}"
        )
    finally:
        try:
            http_client.delete(
                f"{API_PREFIX}/projects/{pid}", headers=auth_headers,
            )
        except httpx.HTTPError:
            pass
