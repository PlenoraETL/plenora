"""test_687 - tabular project-source file exchange matrix.

Scenario covered:
- ESTRAI upstream results are persisted as CSV, JSON and XLSX;
- MANIPOLA downstream projects consume each format through project_source;
- downstream projects save the exchanged data as CSV, JSON and XLSX.

This protects the tabular file-exchange boundary in both directions:
materialized upstream result loading and downstream result persistence.
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
                fileName=f"e2e_test_687_upstream_{fmt}",
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


def _manipola_canvas(source_project_id: str, contract: dict, output_format: str) -> dict:
    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=source_project_id,
        contract=contract,
    )
    columns = source["columns"]
    age_column = next(column for column in columns if column["name"] == "age")
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "age+10",
            "new_column": "age_plus_ten",
            "formula_column_refs": [{"id": age_column["id"], "name": "age"}],
        },
        input_columns=columns,
        position={"x": 260, "y": 0},
    )
    return {
        "nodes": [
            source,
            formula,
            result_node(
                format=output_format,
                fileName=f"e2e_test_687_downstream_{output_format}",
            ),
        ],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-result", "formula-1", "result-1"),
        ],
    }


def _assert_preview_rows(rows: list[dict], label: str) -> None:
    assert_equal(
        [
            (
                row["customer_id"],
                row["customer_name"],
                row["age"],
                row["age_plus_ten"],
            )
            for row in rows[:2]
        ],
        [
            (1, "Anna Rossi", 34, 44),
            (2, "Marco Bianchi", 41, 51),
        ],
        f"{label}: exchanged tabular data must preserve values and formulas",
    )


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_687_postgres"))
    )

    for upstream_format in TABULAR_FORMATS:
        upstream_id = ctx.create_project(
            name=f"e2e_v2_test_687_upstream_{upstream_format}",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_extract_canvas(connection_id, upstream_format),
        )
        upstream_result = ctx.execute_project(upstream_id)
        assert_true(
            upstream_result.get("success"),
            f"{upstream_format}: upstream ESTRAI execution must succeed",
        )

        contract = ctx.get_project(upstream_id).get("output_contract") or {}
        assert_equal(
            [column["physical_name"] for column in contract.get("columns", [])],
            ["customer_id", "customer_name", "age", "status"],
            f"{upstream_format}: upstream output_contract must be stable",
        )

        for downstream_format in TABULAR_FORMATS:
            label = f"{upstream_format}->{downstream_format}"
            downstream_id = ctx.create_project(
                name=f"e2e_v2_test_687_downstream_{upstream_format}_to_{downstream_format}",
                project_type="manipola",
                canvas=_manipola_canvas(upstream_id, contract, downstream_format),
            )
            preview_rows = ctx.preview_project(downstream_id, limit=5).get("rows") or []
            _assert_preview_rows(preview_rows, label)

            result = ctx.execute_project(downstream_id)
            assert_equal(
                int(result.get("rows_count") or 0),
                17,
                f"{label}: downstream execution must persist every active row",
            )


if __name__ == "__main__":
    run_test(test)
