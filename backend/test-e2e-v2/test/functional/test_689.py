"""test_689 - tabular project-type exchange matrix beyond ESTRAI->MANIPOLA.

Scenario covered:
- ESTRAI outputs CSV, JSON and XLSX consumed by ARRICCHISCI;
- MANIPOLA outputs CSV, JSON and XLSX consumed by MANIPOLA and ARRICCHISCI;
- ARRICCHISCI outputs CSV, JSON and XLSX consumed by MANIPOLA and ARRICCHISCI.

The previous file-format matrix covers ESTRAI -> MANIPOLA. This test covers the
remaining tabular producer/consumer boundaries where the same materialized blob
format metadata must survive project_source loading.
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
    transform_node,
    unique_name,
)


TABULAR_FORMATS = ("csv", "json", "xlsx")


def _extract_canvas(connection_id: str, fmt: str) -> dict:
    return {
        "nodes": [
            source_node(
                node_id="customers",
                table_name="customers",
                alias="c",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int", alias="customer_id"),
                    source_column("full_name", logical_type="str", alias="customer_name"),
                    source_column("age", logical_type="int"),
                    source_column("status", logical_type="str"),
                ],
                filters=[{"column": "status", "operator": "=", "value": "active"}],
            ),
            result_node(
                format=fmt,
                fileName=f"e2e_test_689_estrai_{fmt}",
                order_by_enabled=True,
                order_by_columns=[{"column": "id", "direction": "ASC"}],
                data={
                    "order_by_enabled": True,
                    "order_by_columns": [{"column": "id", "direction": "ASC"}],
                },
            ),
        ],
        "edges": [edge("e-customers-result", "customers", "result-1")],
    }


def _project_source(source_project_id: str, contract: dict, node_id: str = "source-1") -> dict:
    return project_source_node_from_contract(
        node_id=node_id,
        source_project_id=source_project_id,
        contract=contract,
    )


def _producer_manipola_canvas(source_project_id: str, contract: dict, fmt: str) -> dict:
    source = _project_source(source_project_id, contract)
    age_column = next(column for column in source["columns"] if column["name"] == "age")
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "age+100",
            "new_column": "producer_score",
            "formula_column_refs": [{"id": age_column["id"], "name": "age"}],
        },
        input_columns=source["columns"],
        position={"x": 260, "y": 0},
    )
    return {
        "nodes": [
            source,
            formula,
            result_node(format=fmt, fileName=f"e2e_test_689_manipola_{fmt}"),
        ],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-result", "formula-1", "result-1"),
        ],
    }


def _pass_through_arricchisci_canvas(source_project_id: str, contract: dict, fmt: str) -> dict:
    source = _project_source(source_project_id, contract)
    return {
        "nodes": [
            source,
            result_node(format=fmt, fileName=f"e2e_test_689_arricchisci_{fmt}"),
        ],
        "edges": [edge("e-source-result", "source-1", "result-1")],
    }


def _consumer_manipola_canvas(source_project_id: str, contract: dict, fmt: str) -> dict:
    source = _project_source(source_project_id, contract)
    age_column = next(column for column in source["columns"] if column["name"] == "age")
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "age+1",
            "new_column": "consumer_score",
            "formula_column_refs": [{"id": age_column["id"], "name": "age"}],
        },
        input_columns=source["columns"],
        position={"x": 260, "y": 0},
    )
    return {
        "nodes": [
            source,
            formula,
            result_node(format=fmt, fileName=f"e2e_test_689_consumer_manipola_{fmt}"),
        ],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-result", "formula-1", "result-1"),
        ],
    }


def _create_executed_project(
    ctx: E2EContext,
    *,
    name: str,
    project_type: str,
    canvas: dict,
    default_connection_id: str | None = None,
) -> tuple[str, dict]:
    project_id = ctx.create_project(
        name=name,
        project_type=project_type,
        default_connection_id=default_connection_id,
        canvas=canvas,
    )
    result = ctx.execute_project(project_id)
    assert_true(result.get("success"), f"{name}: execution must succeed")
    contract = ctx.get_project(project_id).get("output_contract") or {}
    assert_true(contract.get("columns"), f"{name}: output_contract must be persisted")
    return project_id, contract


def _assert_consumer_rows(ctx: E2EContext, project_id: str, label: str) -> None:
    rows = ctx.preview_project(project_id, limit=5).get("rows") or []
    assert_true(rows, f"{label}: preview must return rows")
    assert_equal(
        (rows[0]["customer_id"], rows[0]["customer_name"], rows[0]["age"]),
        (1, "Anna Rossi", 34),
        f"{label}: project_source must preserve public upstream values",
    )


def _assert_manipola_rows(ctx: E2EContext, project_id: str, label: str) -> None:
    rows = ctx.preview_project(project_id, limit=5).get("rows") or []
    assert_true(rows, f"{label}: preview must return rows")
    assert_equal(
        (rows[0]["customer_id"], rows[0]["age"], rows[0]["consumer_score"]),
        (1, 34, 35),
        f"{label}: MANIPOLA consumer must transform exchanged data",
    )


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_689_postgres"))
    )

    estrai_by_format: dict[str, tuple[str, dict]] = {}
    for fmt in TABULAR_FORMATS:
        estrai_by_format[fmt] = _create_executed_project(
            ctx,
            name=f"e2e_v2_test_689_estrai_{fmt}",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_extract_canvas(connection_id, fmt),
        )

        arr_consumer_id, _ = _create_executed_project(
            ctx,
            name=f"e2e_v2_test_689_estrai_{fmt}_to_arricchisci",
            project_type="arricchisci",
            canvas=_pass_through_arricchisci_canvas(
                estrai_by_format[fmt][0],
                estrai_by_format[fmt][1],
                "csv",
            ),
        )
        _assert_consumer_rows(ctx, arr_consumer_id, f"estrai {fmt}->arricchisci")

    for producer_type in ("manipola", "arricchisci"):
        for producer_format in TABULAR_FORMATS:
            base_id, base_contract = estrai_by_format["csv"]
            if producer_type == "manipola":
                producer_id, producer_contract = _create_executed_project(
                    ctx,
                    name=f"e2e_v2_test_689_manipola_producer_{producer_format}",
                    project_type="manipola",
                    canvas=_producer_manipola_canvas(base_id, base_contract, producer_format),
                )
            else:
                producer_id, producer_contract = _create_executed_project(
                    ctx,
                    name=f"e2e_v2_test_689_arricchisci_producer_{producer_format}",
                    project_type="arricchisci",
                    canvas=_pass_through_arricchisci_canvas(
                        base_id,
                        base_contract,
                        producer_format,
                    ),
                )

            manipola_id, _ = _create_executed_project(
                ctx,
                name=f"e2e_v2_test_689_{producer_type}_{producer_format}_to_manipola",
                project_type="manipola",
                canvas=_consumer_manipola_canvas(producer_id, producer_contract, "csv"),
            )
            _assert_manipola_rows(
                ctx,
                manipola_id,
                f"{producer_type} {producer_format}->manipola",
            )

            arricchisci_id, _ = _create_executed_project(
                ctx,
                name=f"e2e_v2_test_689_{producer_type}_{producer_format}_to_arricchisci",
                project_type="arricchisci",
                canvas=_pass_through_arricchisci_canvas(
                    producer_id,
                    producer_contract,
                    "csv",
                ),
            )
            _assert_consumer_rows(
                ctx,
                arricchisci_id,
                f"{producer_type} {producer_format}->arricchisci",
            )


if __name__ == "__main__":
    run_test(test)
