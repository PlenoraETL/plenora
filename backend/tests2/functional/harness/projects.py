"""Helper di dominio progetti per i test funzionali."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response


def validate_project_canvas(base_url: str, project_id: str, headers: dict) -> dict:
    """Valida il canvas del progetto (contratto validate-first).

    Dal 2026-06-02 (46470106) POST /projects/{id}/execute e' fail-fast 409
    su progetto non validato (gate CANVAS_VALIDATION_GATE_ENABLED, default
    ON): il client DEVE chiamare /validate prima di eseguire — e' il flusso
    del pulsante Valida nel FE. I functional replicano lo stesso contratto.

    Asserisce validazione riuscita (200 + valid=True) e ritorna il report.
    """
    status, report, _ = http_json_response(
        f"{base_url}/api/projects/{project_id}/validate",
        method="POST",
        headers=headers,
        timeout=10.0,
    )
    assert status == 200, f"validate atteso 200, got {status}: {report!r}"
    assert report.get("valid") is True, f"canvas non valido: {report!r}"
    return report
