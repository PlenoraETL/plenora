"""test_704 - Canvas Validation: la cascata cross-progetto invalida i discendenti.

Scenario (Epic Canvas Validation §12.4):
- progetto ESTRAI a monte con connessione PostgreSQL reale (``cities``), eseguito
  per produrre l'output_contract pubblico;
- progetto MANIPOLA a valle con un ``project_source`` che referenzia il monte;
- validato il progetto a valle (``canvas_validated=true``);
- salvando di nuovo il canvas del MONTE, la cascata deve riportare il valle a
  ``canvas_validated=false`` (un cambio a monte de-certifica la catena).
"""

from __future__ import annotations

from _e2e import (
    E2EContext,
    assert_equal,
    assert_true,
    edge,
    ensure_plenora_test_network_access,
    postgres_fixture_connection_payload,
    project_source_node_from_contract,
    result_node,
    run_test,
    source_column,
    source_node,
    unique_name,
)


def _upstream_canvas(connection_id: str) -> dict:
    return {
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
            result_node(format="csv", fileName="e2e_test_704_upstream"),
        ],
        "edges": [edge("e-cities-result", "cities", "result-1")],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_704_pg"))
    )

    # Monte: ESTRAI reale, eseguito per popolare l'output_contract.
    upstream_id = ctx.create_project(
        name="e2e_v2_test_704_upstream",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_upstream_canvas(connection_id),
    )
    ctx.execute_project(upstream_id)
    contract = ctx.get_project(upstream_id).get("output_contract") or {}
    assert_true(contract.get("columns"), f"il monte deve esporre un output_contract: {contract}")

    # Valle: MANIPOLA con project_source dal monte.
    downstream_canvas = {
        "nodes": [
            project_source_node_from_contract(
                node_id="src-1",
                source_project_id=upstream_id,
                contract=contract,
            ),
            result_node(format="csv", fileName="e2e_test_704_downstream"),
        ],
        "edges": [edge("e-src-result", "src-1", "result-1")],
    }
    downstream_id = ctx.create_project(
        name="e2e_v2_test_704_downstream",
        project_type="manipola",
        canvas=downstream_canvas,
    )

    report = ctx.validate_project(downstream_id)
    assert_true(not report.get("has_errors"), f"il valle e' valido: {report.get('issues')}")
    meta = ctx.get_project(downstream_id).get("metadata") or {}
    assert_equal(meta.get("canvas_validated"), True, "il valle deve risultare validato")

    # Cambio a monte: salvare il canvas del monte deve cascatare l'invalidazione.
    ctx.update_canvas(upstream_id, _upstream_canvas(connection_id))

    meta_after = ctx.get_project(downstream_id).get("metadata") or {}
    assert_equal(
        meta_after.get("canvas_validated"),
        False,
        "un cambio a monte deve riportare il discendente a canvas_validated=false (cascata)",
    )


if __name__ == "__main__":
    run_test(test)
