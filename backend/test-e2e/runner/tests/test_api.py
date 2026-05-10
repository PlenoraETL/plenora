"""API-001..API-XXX — REST API contract smokes.

Catalogo: backend/test-e2e/catalog/api.yaml
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest


API_PREFIX = "/api"


def _poll_task(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    task_id: str,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    terminal = {
        "success", "completed", "failure", "failed", "error", "cancelled",
    }
    while time.monotonic() < deadline:
        r = http_client.get(
            f"{API_PREFIX}/tasks/{task_id}", headers=auth_headers,
        )
        if r.status_code == 200:
            body = r.json()
            if (body.get("status") or "").lower() in terminal:
                return body
        time.sleep(1)
    raise AssertionError(f"Task {task_id} non terminato in {timeout}s")


# ── API-001 — Connection con credenziali errate ──────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_api_001_connection_test_with_wrong_credentials(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    """API-001 — Catalog: "201 sulla creazione, ma POST
    /connections/{id}/test ritorna 200 + {ok: false, error: ...}".

    Plenora accetta create-time qualsiasi configurazione; il test
    di connettività e' un endpoint dedicato che non solleva 4xx
    su credenziali sbagliate (sarebbe scomodo per la UX wizard),
    ma ritorna `200 + {ok: false}` con il dettaglio dell'errore.
    """
    payload = {
        "name": f"e2e-api001-{uuid.uuid4().hex[:6]}",
        "description": "API-001 wrong creds",
        "type": "postgres",
        "host": "plenora-e2e-postgres",
        "port": 5432,
        "database": "plenora_test",
        "username": "plenora",
        "password": "wrong_password_xxx",
    }
    r_create = http_client.post(
        f"{API_PREFIX}/connections", json=payload, headers=auth_headers,
    )
    assert r_create.status_code in (200, 201), (
        f"create wrong-creds: {r_create.status_code} — {r_create.text[:200]}"
    )
    cid = r_create.json().get("id")
    assert cid

    try:
        r_test = http_client.post(
            f"{API_PREFIX}/connections/{cid}/test", headers=auth_headers,
        )
        assert r_test.status_code == 200, (
            f"connection test: atteso 200, ottenuto "
            f"{r_test.status_code} — {r_test.text[:200]}"
        )
        body = r_test.json()
        # Plenora ConnectionTestResult ha shape {success: bool, message, ...}.
        # "ok" e' la chiave del catalog ma il code ritorna "success".
        ok = body.get("ok")
        if ok is None:
            ok = body.get("success")
        assert ok is False, (
            f"API-001 atteso ok=False con creds errate, ottenuto {body}"
        )
        # Il messaggio di errore deve contenere keyword pertinenti.
        msg = (
            str(body.get("error") or "")
            + " " + str(body.get("message") or "")
        ).lower()
        assert any(k in msg for k in (
            "password", "auth", "creden", "fail", "errore", "denied"
        )), f"API-001 error non descrittivo: {msg!r}"
    finally:
        http_client.delete(
            f"{API_PREFIX}/connections/{cid}", headers=auth_headers,
        )


# ── API-002 — Connection POST senza campi obbligatori → 422 ──────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_api_002_connection_validation_error(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    """API-002 — Catalog: "422 con detail strutturato (validation
    errors)". Pydantic via FastAPI restituisce 422 con
    `detail = [{loc, msg, type, ...}, ...]`.
    """
    # Manca `host`, `database`, `password` — campi obbligatori
    # per type=postgres.
    r = http_client.post(
        f"{API_PREFIX}/connections",
        json={"name": f"api002-{uuid.uuid4().hex[:6]}", "type": "postgres"},
        headers=auth_headers,
    )
    assert r.status_code == 422, (
        f"API-002 atteso 422, ottenuto {r.status_code} — {r.text[:200]}"
    )
    detail = r.json().get("detail")
    # FastAPI standard: detail e' una lista di errori. Plenora lo
    # avvolge in `{"error":..., "details":..., "detail":...}` ma
    # il `detail` interno deve restare strutturato.
    if isinstance(detail, dict):
        # envelope plenora — naviga al sub-detail
        detail = (
            detail.get("errors")
            or detail.get("detail")
            or detail.get("details", {}).get("errors")
        )
    assert isinstance(detail, list) and len(detail) > 0, (
        f"API-002 detail non strutturato: {r.json()}"
    )
    # Almeno un errore deve avere 'loc' / 'msg' / 'type'.
    sample = detail[0]
    assert isinstance(sample, dict), f"errore validation non dict: {sample}"
    keys = set(sample.keys())
    assert keys & {"loc", "msg", "type"}, (
        f"API-002 errore privo di campi pydantic: {sample}"
    )


# ── API-009 — Canvas con connection cancellata → run failed ─────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_api_009_run_with_deleted_connection(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    """API-009 — Catalog: "run status=failed, error message
    strutturato, no 500".

    1. Crea connection
    2. Crea project ESTRAI con canvas che la riferisce
    3. DELETE connection
    4. Execute project → task deve concludere `failure`/`failed`
       con messaggio che indica connection mancante. Il task
       endpoint NON deve restituire 500.
    """
    # 1. Connection
    r_c = http_client.post(
        f"{API_PREFIX}/connections",
        json={
            "name": f"api009-{uuid.uuid4().hex[:6]}",
            "description": "API-009 sacrificale",
            "type": "postgres",
            "host": "plenora-e2e-postgres",
            "port": 5432,
            "database": "plenora_test",
            "username": "plenora",
            "password": "plenora_test_pwd",
        },
        headers=auth_headers,
    )
    assert r_c.status_code in (200, 201), r_c.text[:200]
    cid = r_c.json()["id"]

    # 2. Project + canvas che riferisce cid
    r_p = http_client.post(
        f"{API_PREFIX}/projects",
        json={
            "name": f"api009-{uuid.uuid4().hex[:6]}",
            "description": "API-009",
            "project_type": "estrai",
            "default_connection_id": cid,
        },
        headers=auth_headers,
    )
    assert r_p.status_code in (200, 201), r_p.text[:200]
    pid = (r_p.json().get("metadata") or {}).get("id") or r_p.json().get("id")

    canvas = {
        "nodes": [
            {"id": "src", "type": "source",
             "position": {"x": 100, "y": 100},
             "connection_id": cid, "table_name": "customers",
             "columns": [{"name": "id", "type": "int", "visible": True}]},
            {"id": "res", "type": "result",
             "position": {"x": 400, "y": 100}},
        ],
        "edges": [
            {"id": "e1", "source": "src", "target": "res"},
        ],
    }
    http_client.put(
        f"{API_PREFIX}/projects/{pid}/canvas",
        json=canvas, headers=auth_headers,
    )

    try:
        # 3. Cancella la connection (i progetti che la
        # referenziano restano dirty).
        r_del = http_client.delete(
            f"{API_PREFIX}/connections/{cid}", headers=auth_headers,
        )
        # Plenora puo' restituire 409 se non permette delete con
        # dipendenze. In quel caso skippiamo l'asserzione successiva
        # e re-creiamo lo scenario buttando la default_connection_id
        # del progetto a un id finto. Per ora gestiamo entrambi i
        # casi.
        if r_del.status_code == 409:
            # Forza connessione "spezzata" PATCH/PUT non disponibile
            # per i project, quindi cancelliamo il record DB-side
            # via altra strada non esposta. Skip se policy 409.
            pytest.skip(
                "Plenora rifiuta DELETE connection con dipendenze "
                f"(409). API-009 in questa configurazione non puo' "
                f"esercitare il path 'connection cancellata'."
            )
        assert r_del.status_code in (200, 204), (
            f"DELETE connection: {r_del.status_code} — "
            f"{r_del.text[:200]}"
        )

        # 4. Execute → task deve fallire con messaggio gestito
        r_exec = http_client.post(
            f"{API_PREFIX}/projects/{pid}/execute",
            json={}, headers=auth_headers,
        )
        # L'endpoint /execute potrebbe gia' bloccare con 422 se
        # rileva preflight la connection mancante. Accettiamo
        # entrambe le strade purche' la failure sia "controllata".
        if r_exec.status_code in (400, 404, 409, 422):
            body = r_exec.json()
            err_text = str(body).lower()
            assert any(k in err_text for k in (
                "connection", "connessione", "non trovat", "missing",
            )), f"API-009 error non descrittivo (sync): {body}"
            return
        assert r_exec.status_code in (200, 201, 202), (
            f"execute (deleted conn) atteso success-pre-runtime, "
            f"ottenuto {r_exec.status_code} — {r_exec.text[:200]}"
        )
        task_id = r_exec.json().get("task_id") or r_exec.json().get("id")
        assert task_id

        final = _poll_task(http_client, auth_headers, str(task_id), timeout=60)
        status = (final.get("status") or "").lower()
        assert status in {"failure", "failed", "error"}, (
            f"API-009 task atteso failure, ottenuto {status!r}: {final}"
        )
        msg = str(final.get("message") or final.get("error") or "").lower()
        assert any(k in msg for k in (
            "connection", "connessione", "non trovat", "missing",
        )), f"API-009 error message non descrittivo (task): {final}"
    finally:
        http_client.delete(
            f"{API_PREFIX}/projects/{pid}", headers=auth_headers,
        )


# ── API-014 — DELETE project con dipendenze → mark orphan ────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_api_014_delete_project_with_schedule_dependency(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """API-014 — Catalog `api.yaml` chiede 409 con dependencies
    `kind=schedule`, ma il behavior implementato e' diverso e
    intenzionale (vedi `core/projects/service.delete_project:449-462`):

       "Non cancelliamo gli schedule: lo storico (log esecuzioni)
        resta visibile e l'utente decide se rimuoverli manualmente
        dalla UI"

    DELETE del project ritorna 204 e marca i suoi schedule come
    `runtime.status='orphan'`. Il blocco 409 con dependencies vale
    solo per `eseguibili` e `dependent_projects` (manipola/arricchisci
    che usano questo come source).

    Il test riflette il behavior implementato, non la spec literale
    del catalog: assert sul transition idle -> orphan dello schedule.
    Il catalog `api.yaml` va aggiornato per descrivere il behavior
    reale (TODO follow-up).
    """
    r_p = http_client.post(
        f"{API_PREFIX}/projects",
        json={
            "name": f"api014-{uuid.uuid4().hex[:6]}",
            "description": "API-014",
            "project_type": "estrai",
            "default_connection_id": postgres_conn_id,
        },
        headers=auth_headers,
    )
    assert r_p.status_code in (200, 201), r_p.text[:200]
    pid = (r_p.json().get("metadata") or {}).get("id") or r_p.json().get("id")

    # canvas minimale per validita' schedule
    http_client.put(
        f"{API_PREFIX}/projects/{pid}/canvas",
        json={
            "nodes": [
                {"id": "src", "type": "source",
                 "position": {"x": 100, "y": 100},
                 "connection_id": postgres_conn_id,
                 "table_name": "customers",
                 "columns": [{"name": "id", "type": "int", "visible": True}]},
                {"id": "res", "type": "result",
                 "position": {"x": 400, "y": 100}},
            ],
            "edges": [{"id": "e1", "source": "src", "target": "res"}],
        },
        headers=auth_headers,
    )

    schedule_id = None
    try:
        r_s = http_client.post(
            f"{API_PREFIX}/schedules",
            json={
                "project_id": pid,
                "cron_expression": "59 23 31 12 *",
                "timezone": "Europe/Rome",
                "description": "API-014 dep",
                "use_cache": False,
                "max_retries": 0,
                "retry_backoff_seconds": [60],
                "append_conflicts": "skip",
                "log_skipped_rows": True,
                "concurrency_max_queue": 1,
            },
            headers=auth_headers,
        )
        assert r_s.status_code in (200, 201), r_s.text[:200]
        schedule_id = r_s.json().get("id") or r_s.json().get("schedule_id")
        assert schedule_id

        # Pre-condizione: schedule e' `idle` prima del delete
        r_sched_pre = http_client.get(
            f"{API_PREFIX}/schedules/{schedule_id}", headers=auth_headers,
        )
        assert r_sched_pre.status_code == 200
        runtime_pre = (r_sched_pre.json().get("runtime") or {})
        assert runtime_pre.get("status") in ("idle", "running", "queued"), (
            f"API-014 schedule pre-delete in stato inatteso: {runtime_pre}"
        )

        # DELETE project — implementato come 204 con mark-orphan dei
        # schedule referenzianti (NON 409 come da catalog literal).
        r_del = http_client.delete(
            f"{API_PREFIX}/projects/{pid}", headers=auth_headers,
        )
        assert r_del.status_code in (200, 204), (
            f"API-014 atteso 204, ottenuto {r_del.status_code} — "
            f"{r_del.text[:200]}"
        )

        # Verifica behavior canonico: lo schedule resta vivo ma e'
        # transitato a `runtime.status='orphan'`. Il caller puo'
        # decidere se eliminarlo manualmente o ripristinare il project.
        r_sched_post = http_client.get(
            f"{API_PREFIX}/schedules/{schedule_id}", headers=auth_headers,
        )
        assert r_sched_post.status_code == 200, (
            f"API-014 schedule sparito dopo delete project: "
            f"{r_sched_post.status_code} — {r_sched_post.text[:200]}"
        )
        runtime_post = (r_sched_post.json().get("runtime") or {})
        assert runtime_post.get("status") == "orphan", (
            f"API-014 atteso runtime.status='orphan' dopo DELETE "
            f"project, ottenuto {runtime_post}"
        )
    finally:
        if schedule_id:
            http_client.delete(
                f"{API_PREFIX}/schedules/{schedule_id}",
                headers=auth_headers,
            )
        http_client.delete(
            f"{API_PREFIX}/projects/{pid}", headers=auth_headers,
        )
