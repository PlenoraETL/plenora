"""test_688 - geospatial project-source file exchange matrix.

Scenario covered:
- ESTRAI upstream results are persisted as GeoJSON, GeoPackage and Shapefile;
- MANIPOLA downstream projects consume each format through project_source;
- downstream projects save the exchanged geometry as GeoJSON, GeoPackage and
  Shapefile.

This protects both in-memory geospatial exchange (GeoJSON) and path-bound
exchange formats (GPKG and zipped Shapefile).
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


GEOSPATIAL_FORMATS = ("geojson", "gpkg", "shp")


def _extract_canvas(connection_id: str, fmt: str) -> dict:
    return {
        "nodes": [
            source_node(
                node_id="cities",
                table_name="cities",
                alias="c",
                connection_id=connection_id,
                columns=[
                    source_column("id", logical_type="int", alias="city_id"),
                    source_column("name", logical_type="str", alias="city_name"),
                    source_column(
                        "geom",
                        logical_type="geometry:point",
                        alias="geometry",
                    ),
                ],
            ),
            result_node(
                format=fmt,
                fileName=f"e2e_test_688_upstream_{fmt}",
                geomColumn="geometry",
                order_by_enabled=True,
                order_by_columns=[{"column": "id", "direction": "ASC"}],
                data={
                    "geomColumn": "geometry",
                    "order_by_enabled": True,
                    "order_by_columns": [{"column": "id", "direction": "ASC"}],
                },
            ),
        ],
        "edges": [edge("e-cities-result", "cities", "result-1")],
    }


def _manipola_canvas(source_project_id: str, contract: dict, output_format: str) -> dict:
    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=source_project_id,
        contract=contract,
    )
    columns = source["columns"]
    city_id_column = next(column for column in columns if column["name"] == "city_id")
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "city_id+1000",
            "new_column": "city_code",
            "formula_column_refs": [{"id": city_id_column["id"], "name": "city_id"}],
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
                fileName=f"e2e_test_688_downstream_{output_format}",
                geomColumn="geometry",
                data={"geomColumn": "geometry"},
            ),
        ],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-result", "formula-1", "result-1"),
        ],
    }


def _assert_preview_rows(rows: list[dict], label: str) -> None:
    assert_true(rows, f"{label}: preview must return geospatial rows")
    assert_equal(
        [
            (row["city_id"], row["city_name"], row["city_code"])
            for row in rows[:2]
        ],
        [
            (1, "Milano", 1001),
            (2, "Bergamo", 1002),
        ],
        f"{label}: exchanged geospatial data must preserve attributes",
    )


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_688_postgres"))
    )

    for upstream_format in GEOSPATIAL_FORMATS:
        upstream_id = ctx.create_project(
            name=f"e2e_v2_test_688_upstream_{upstream_format}",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_extract_canvas(connection_id, upstream_format),
        )
        upstream_result = ctx.execute_project(upstream_id)
        assert_true(
            upstream_result.get("success"),
            f"{upstream_format}: upstream ESTRAI geospatial execution must succeed",
        )

        contract = ctx.get_project(upstream_id).get("output_contract") or {}
        assert_equal(
            [column["physical_name"] for column in contract.get("columns", [])],
            ["city_id", "city_name", "geometry"],
            f"{upstream_format}: upstream geospatial output_contract must be stable",
        )

        for downstream_format in GEOSPATIAL_FORMATS:
            label = f"{upstream_format}->{downstream_format}"
            downstream_id = ctx.create_project(
                name=f"e2e_v2_test_688_downstream_{upstream_format}_to_{downstream_format}",
                project_type="manipola",
                canvas=_manipola_canvas(upstream_id, contract, downstream_format),
            )
            preview_rows = ctx.preview_project(downstream_id, limit=5).get("rows") or []
            _assert_preview_rows(preview_rows, label)

            result = ctx.execute_project(downstream_id)
            assert_true(
                int(result.get("rows_count") or 0) > 0,
                f"{label}: downstream geospatial execution must persist rows",
            )


if __name__ == "__main__":
    run_test(test)
