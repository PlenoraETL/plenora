"""test_702 - Canvas Validation: un canvas valido viene certificato ed e' eseguibile.

Scenario (Epic Canvas Validation §12):
- connessione PostgreSQL reale alla fixture E2E v2;
- progetto ESTRAI valido (``cities`` -> result collegato);
- POST /validate ritorna ``valid=true``/``has_errors=false`` e PERSISTE lo stato
  (``metadata.canvas_validated=true``);
- con lo stato validato, l'esecuzione parte (il gate a esecuzione passa).
"""

from __future__ import annotations

from _e2e import (
    E2EContext,
    assert_equal,
    edge,
    ensure_plenora_test_network_access,
    postgres_fixture_connection_payload,
    result_node,
    run_test,
    source_column,
    source_node,
    unique_name,
)


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_702_pg"))
    )
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
            result_node(format="csv", fileName="e2e_test_702"),
        ],
        "edges": [edge("e-cities-result", "cities", "result-1")],
    }
    project_id = ctx.create_project(
        name="e2e_v2_test_702_valid",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )

    report = ctx.validate_project(project_id)
    assert_equal(report.get("valid"), True, f"canvas valido, issues={report.get('issues')}")
    assert_equal(report.get("has_errors"), False, "nessun errore atteso")

    metadata = ctx.get_project(project_id).get("metadata") or {}
    assert_equal(
        metadata.get("canvas_validated"),
        True,
        "lo stato di validazione deve essere persistito dopo /validate",
    )

    # Gate a esecuzione: con lo stato validato il run parte (execute_project
    # ri-valida e poi esegue). Nessuna eccezione = gate passato + run ok.
    ctx.execute_project(project_id)


if __name__ == "__main__":
    run_test(test)
