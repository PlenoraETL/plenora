"""test_690 - geospatial project-type exchange matrix beyond ESTRAI->MANIPOLA.

Scenario covered:
- ESTRAI GeoJSON, GeoPackage and Shapefile results are consumed by ARRICCHISCI;
- MANIPOLA GeoJSON, GeoPackage and Shapefile results are consumed by
  MANIPOLA and ARRICCHISCI;
- ARRICCHISCI GeoJSON, GeoPackage and Shapefile results are consumed by
  MANIPOLA and ARRICCHISCI.

This complements test_688, which covers the format matrix for ESTRAI ->
MANIPOLA only.
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
                    source_column("geom", logical_type="geometry:point", alias="geometry"),
                ],
            ),
            result_node(
                format=fmt,
                fileName=f"e2e_test_690_estrai_{fmt}",
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


def _project_source(source_project_id: str, contract: dict, node_id: str = "source-1") -> dict:
    return project_source_node_from_contract(
        node_id=node_id,
        source_project_id=source_project_id,
        contract=contract,
    )


def _producer_manipola_canvas(source_project_id: str, contract: dict, fmt: str) -> dict:
    source = _project_source(source_project_id, contract)
    city_id_column = next(column for column in source["columns"] if column["name"] == "city_id")
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "city_id+2000",
            "new_column": "producer_code",
            "formula_column_refs": [{"id": city_id_column["id"], "name": "city_id"}],
        },
        input_columns=source["columns"],
        position={"x": 260, "y": 0},
    )
    return {
        "nodes": [
            source,
            formula,
            result_node(
                format=fmt,
                fileName=f"e2e_test_690_manipola_{fmt}",
                geomColumn="geometry",
                data={"geomColumn": "geometry"},
            ),
        ],
        "edges": [
            edge("e-source-formula", "source-1", "formula-1"),
            edge("e-formula-result", "formula-1", "result-1"),
        ],
    }


def _pass_through_arricchisci_canvas(
    source_project_id: str, contract: dict, fmt: str, enrich_api_id: str
) -> dict:
    from _arricchisci_contract_final import _with_noop_enricher
    source = _project_source(source_project_id, contract)
    canvas = {
        "nodes": [
            source,
            result_node(
                format=fmt,
                fileName=f"e2e_test_690_arricchisci_{fmt}",
                geomColumn="geometry",
                data={"geomColumn": "geometry"},
            ),
        ],
        "edges": [edge("e-source-result", "source-1", "result-1")],
    }
    # Regola ARRICCHISCI_NO_ENRICHER: api_enrich no-op (aggiunge solo _enrich,
    # preserva righe/valori/geometria upstream) per validare il pass-through.
    return _with_noop_enricher(canvas, enrich_api_id)


def _consumer_manipola_canvas(source_project_id: str, contract: dict) -> dict:
    source = _project_source(source_project_id, contract)
    city_id_column = next(column for column in source["columns"] if column["name"] == "city_id")
    formula = transform_node(
        node_id="formula-1",
        transform_op="formula",
        config={
            "formula": "city_id+1",
            "new_column": "consumer_code",
            "formula_column_refs": [{"id": city_id_column["id"], "name": "city_id"}],
        },
        input_columns=source["columns"],
        position={"x": 260, "y": 0},
    )
    return {
        "nodes": [
            source,
            formula,
            result_node(
                format="geojson",
                fileName="e2e_test_690_consumer_manipola",
                geomColumn="geometry",
                data={"geomColumn": "geometry"},
            ),
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


def _assert_pass_through_rows(ctx: E2EContext, project_id: str, label: str) -> None:
    rows = ctx.preview_project(project_id, limit=5).get("rows") or []
    assert_true(rows, f"{label}: preview must return geospatial rows")
    assert_equal(
        (rows[0]["city_id"], rows[0]["city_name"]),
        (1, "Milano"),
        f"{label}: project_source must preserve geospatial attributes",
    )


def _assert_manipola_rows(ctx: E2EContext, project_id: str, label: str) -> None:
    rows = ctx.preview_project(project_id, limit=5).get("rows") or []
    assert_true(rows, f"{label}: preview must return geospatial rows")
    assert_equal(
        (rows[0]["city_id"], rows[0]["city_name"], rows[0]["consumer_code"]),
        (1, "Milano", 2),
        f"{label}: MANIPOLA consumer must transform exchanged geospatial data",
    )


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    from _arricchisci_contract_final import _noop_enrich_api
    enrich_api_id = _noop_enrich_api(ctx, "test_690")
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_690_postgres"))
    )

    estrai_by_format: dict[str, tuple[str, dict]] = {}
    for fmt in GEOSPATIAL_FORMATS:
        estrai_by_format[fmt] = _create_executed_project(
            ctx,
            name=f"e2e_v2_test_690_estrai_{fmt}",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_extract_canvas(connection_id, fmt),
        )
        arr_consumer_id, _ = _create_executed_project(
            ctx,
            name=f"e2e_v2_test_690_estrai_{fmt}_to_arricchisci",
            project_type="arricchisci",
            canvas=_pass_through_arricchisci_canvas(
                estrai_by_format[fmt][0],
                estrai_by_format[fmt][1],
                "geojson",
                enrich_api_id,
            ),
        )
        _assert_pass_through_rows(ctx, arr_consumer_id, f"estrai {fmt}->arricchisci")

    base_id, base_contract = estrai_by_format["geojson"]
    for producer_type in ("manipola", "arricchisci"):
        for producer_format in GEOSPATIAL_FORMATS:
            if producer_type == "manipola":
                producer_id, producer_contract = _create_executed_project(
                    ctx,
                    name=f"e2e_v2_test_690_manipola_producer_{producer_format}",
                    project_type="manipola",
                    canvas=_producer_manipola_canvas(base_id, base_contract, producer_format),
                )
            else:
                producer_id, producer_contract = _create_executed_project(
                    ctx,
                    name=f"e2e_v2_test_690_arricchisci_producer_{producer_format}",
                    project_type="arricchisci",
                    canvas=_pass_through_arricchisci_canvas(
                        base_id,
                        base_contract,
                        producer_format,
                        enrich_api_id,
                    ),
                )

            manipola_id, _ = _create_executed_project(
                ctx,
                name=f"e2e_v2_test_690_{producer_type}_{producer_format}_to_manipola",
                project_type="manipola",
                canvas=_consumer_manipola_canvas(producer_id, producer_contract),
            )
            _assert_manipola_rows(
                ctx,
                manipola_id,
                f"{producer_type} {producer_format}->manipola",
            )

            arricchisci_id, _ = _create_executed_project(
                ctx,
                name=f"e2e_v2_test_690_{producer_type}_{producer_format}_to_arricchisci",
                project_type="arricchisci",
                canvas=_pass_through_arricchisci_canvas(
                    producer_id,
                    producer_contract,
                    "geojson",
                    enrich_api_id,
                ),
            )
            _assert_pass_through_rows(
                ctx,
                arricchisci_id,
                f"{producer_type} {producer_format}->arricchisci",
            )


if __name__ == "__main__":
    run_test(test)
