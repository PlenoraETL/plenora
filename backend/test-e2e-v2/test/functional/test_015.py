"""test_015 - ESTRAI positive spatial join predicates.

Scenario covered:
- real PostgreSQL/PostGIS fixture connection;
- spatial joins between city points and region multipolygons;
- ST_Intersects, ST_Within and ST_DWithin predicates;
- deterministic counts and sentinel joined city/region pairs.
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


def _city_region_canvas(connection_id: str, predicate: str, distance: float | None = None) -> dict:
    spatial_edge = {
        "id": f"e-cities-regions-{predicate}",
        "source": "cities",
        "target": "regions",
        "join_type": "inner",
        "is_spatial": True,
        "spatial_predicate": predicate,
        "source_geom_column": "geom",
        "target_geom_column": "boundary",
    }
    if distance is not None:
        spatial_edge["spatial_distance"] = distance
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
            result_node(format="csv", fileName=f"e2e_test_015_{predicate.lower()}"),
        ],
        "edges": [spatial_edge, edge("e-regions-result", "regions", "result-1")],
    }


def test(ctx: E2EContext) -> None:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name("e2e_v2_test_015_postgres"))
    )

    for predicate, distance in [
        ("ST_Intersects", None),
        ("ST_Within", None),
        ("ST_DWithin", 0.0),
    ]:
        project_id = ctx.create_project(
            name=f"e2e_v2_test_015_spatial_{predicate.lower()}",
            project_type="estrai",
            default_connection_id=connection_id,
            canvas=_city_region_canvas(connection_id, predicate, distance),
        )
        rows = ctx.preview_project(project_id, limit=100).get("rows") or []
        pairs = {(row["city_name"], row["region_code"]) for row in rows}
        assert_equal(
            len(rows),
            14,
            f"{predicate} city-region spatial join must match seeded polygons",
        )
        assert_equal(
            {"Milano", "Roma", "Napoli", "M\u00eantina"} <= {city for city, _ in pairs},
            True,
            f"{predicate} must include representative fixture cities",
        )
        assert_equal(
            ("Tokyo Outpost", "CAM") in pairs,
            False,
            f"{predicate} must not join far-away Tokyo Outpost to Campania",
        )


if __name__ == "__main__":
    run_test(test)
