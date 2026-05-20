"""test_016 - ESTRAI remaining spatial predicates.

Scenario covered:
- ST_Contains with region polygons as source and city points as target;
- ST_Touches and ST_Overlaps negative cases on seeded point/polygon data;
- deterministic counts for every remaining spatial predicate accepted by the
  canvas edge model.
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


def _contains_canvas(connection_id: str) -> dict:
    return {
        "nodes": [
            source_node(
                node_id="regions",
                table_name="regions",
                alias="r",
                connection_id=connection_id,
                columns=[
                    source_column("code", logical_type="str", alias="region_code"),
                    source_column("boundary", logical_type="geometry:multipolygon", visible=False),
                ],
                position={"x": 0, "y": 0},
            ),
            source_node(
                node_id="cities",
                table_name="cities",
                alias="c",
                connection_id=connection_id,
                columns=[
                    source_column("name", logical_type="str", alias="city_name"),
                    source_column("geom", logical_type="geometry:point", visible=False),
                ],
                position={"x": 0, "y": 180},
            ),
            result_node(format="csv", fileName="e2e_test_016_contains"),
        ],
        "edges": [
            {
                "id": "e-regions-cities-contains",
                "source": "regions",
                "target": "cities",
                "join_type": "inner",
                "is_spatial": True,
                "spatial_predicate": "ST_Contains",
                "source_geom_column": "boundary",
                "target_geom_column": "geom",
            },
            edge("e-cities-result", "cities", "result-1"),
        ],
    }


def _city_region_canvas(connection_id: str, predicate: str) -> dict:
    return {
        "nodes": [
            source_node(
                node_id="cities",
                table_name="cities",
                alias="c",
                connection_id=connection_id,
                columns=[
                    source_column("name", logical_type="str", alias="city_name"),
                    source_column("geom", logical_type="geometry:point", visible=False),
                ],
                position={"x": 0, "y": 0},
            ),
            source_node(
                node_id="regions",
                table_name="regions",
                alias="r",
                connection_id=connection_id,
                columns=[
                    source_column("code", logical_type="str", alias="region_code"),
                    source_column("boundary", logical_type="geometry:multipolygon", visible=False),
                ],
                position={"x": 0, "y": 180},
            ),
            result_node(format="csv", fileName=f"e2e_test_016_{predicate.lower()}"),
        ],
        "edges": [
            {
                "id": f"e-cities-regions-{predicate}",
                "source": "cities",
                "target": "regions",
                "join_type": "inner",
                "is_spatial": True,
                "spatial_predicate": predicate,
                "source_geom_column": "geom",
                "target_geom_column": "boundary",
            },
            edge("e-regions-result", "regions", "result-1"),
        ],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_016_postgres"))
    )

    contains_id = ctx.create_project(
        name="e2e_v2_test_016_spatial_contains",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_contains_canvas(connection_id),
    )
    contains_rows = ctx.preview_project(contains_id, limit=100).get("rows") or []
    assert_equal(len(contains_rows), 14, "ST_Contains region->city must match seeded polygons")

    for predicate in ("ST_Touches", "ST_Overlaps"):
        project_id = ctx.create_project(
            name=f"e2e_v2_test_016_spatial_{predicate.lower()}",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_city_region_canvas(connection_id, predicate),
        )
        rows = ctx.preview_project(project_id, limit=100).get("rows") or []
        assert_equal(
            len(rows),
            0,
            f"{predicate} point/polygon join must be empty on the fixture data",
        )


if __name__ == "__main__":
    run_test(test)
