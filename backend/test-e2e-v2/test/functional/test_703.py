"""test_703 - Canvas Validation: il gate a esecuzione blocca un progetto NON validato.

Scenario (Epic Canvas Validation §12, gate ON di default):
- connessione PostgreSQL reale alla fixture E2E v2;
- progetto ESTRAI con canvas VALIDO ma MAI validato (``canvas_validated=false``);
- chiamando /execute RAW (senza il validate-first dell'harness) il run deve
  essere BLOCCATO dal gate (result.success=false / messaggio di validazione),
  invece di partire.
"""

from __future__ import annotations

import time

from _e2e import (
    E2EContext,
    assert_true,
    edge,
    ensure_plenora_test_network_access,
    postgres_fixture_connection_payload,
    result_node,
    run_test,
    source_column,
    source_node,
    unique_name,
    _json_request,
)


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_703_pg"))
    )
    # Canvas VALIDO (cities -> result) ma deliberatamente NON validato.
    canvas = {
        "nodes": [
            source_node(
                node_id="cities",
                table_name="cities",
                alias="ci",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int"),
                    source_column("name", logical_type="str"),
                ],
            ),
            result_node(format="csv", fileName="e2e_test_703"),
        ],
        "edges": [edge("e-cities-result", "cities", "result-1")],
    }
    project_id = ctx.create_project(
        name="e2e_v2_test_703_unvalidated",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )

    # Stato di partenza: mai validato.
    metadata = ctx.get_project(project_id).get("metadata") or {}
    assert_true(
        metadata.get("canvas_validated") in (False, None),
        f"un progetto appena creato non deve risultare validato: {metadata}",
    )

    # /execute RAW (bypassa il validate-first dell'harness) → atteso BLOCCO.
    task = _json_request(
        "POST",
        f"/projects/{project_id}/execute",
        token=ctx.token,
        payload={"force_recalculate": True},
    )
    task_id = task.get("task_id") if isinstance(task, dict) else None
    assert_true(task_id, f"execute deve ritornare un task_id: {task!r}")

    # Poll tollerante: il blocco puo' presentarsi come task terminato con
    # result.success=false o con un messaggio di validazione.
    deadline = time.time() + 60
    final = None
    while time.time() < deadline:
        status = _json_request(
            "GET", f"/tasks/{task_id}", token=ctx.token, expected=(200, 404)
        )
        state = str((status or {}).get("status") or "").lower()
        if state in {"success", "failure", "failed", "revoked"}:
            final = status
            break
        time.sleep(0.5)

    assert_true(final is not None, "il task di esecuzione non e' terminato in tempo")
    result = (final or {}).get("result") or {}
    message = str(result.get("message") or "")
    blocked = result.get("success") is False or "validat" in message.lower()
    assert_true(
        blocked,
        f"l'esecuzione di un progetto NON validato deve essere bloccata dal gate: {final!r}",
    )

    # Contro-prova: dopo /validate (canvas valido) il run parte.
    report = ctx.validate_project(project_id)
    assert_true(not report.get("has_errors"), f"il canvas e' valido: {report.get('issues')}")
    ctx.execute_project(project_id)


if __name__ == "__main__":
    run_test(test)
