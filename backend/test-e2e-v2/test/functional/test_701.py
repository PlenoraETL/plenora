"""test_701 - Canvas Validation: un canvas invalido e' segnalato da POST /validate.

Scenario (Epic Canvas Validation §12):
- connessione PostgreSQL reale alla fixture E2E v2;
- progetto ESTRAI con un source su ``cities`` e un nodo result NON collegato
  (nessun edge) -> canvas strutturalmente invalido;
- POST /validate deve ritornare ``valid=false``, ``has_errors=true`` e segnalare
  ``RESULT_NOT_CONNECTED``.
"""

from __future__ import annotations

from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
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
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_701_pg"))
    )
    # source + result MA nessun edge -> il result non e' collegato (invalido).
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
            result_node(),
        ],
        "edges": [],
    }
    project_id = ctx.create_project(
        name="e2e_v2_test_701_invalid",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )

    report = ctx.validate_project(project_id)
    assert_equal(report.get("valid"), False, "canvas con result non collegato deve essere invalido")
    assert_equal(report.get("has_errors"), True, "il report deve avere errori")
    codes = [issue.get("code") for issue in (report.get("issues") or [])]
    assert_true(
        "RESULT_NOT_CONNECTED" in codes,
        f"deve segnalare RESULT_NOT_CONNECTED, codes={codes}",
    )


if __name__ == "__main__":
    run_test(test)
