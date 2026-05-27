from __future__ import annotations

from app.core.dataframes.geospatial_io import exported_column_names_for_format
from app.core.output_contract import build_output_contract


def test_shapefile_export_columns_use_dbf_laundered_physical_names():
    source_columns = ["city_id", "city_name", "producer_code", "geometry"]

    physical_columns = exported_column_names_for_format(
        source_columns,
        "shp",
        geom_column="geometry",
    )

    assert physical_columns == ["city_id", "city_name", "producer_c", "geometry"]

    contract = build_output_contract(
        project_id="producer",
        result_columns=[
            {
                "source_name": source,
                "name": physical,
                "type": "object",
            }
            for source, physical in zip(source_columns, physical_columns)
        ],
    )

    by_source = {column.source_name: column for column in contract.columns}
    assert by_source["producer_code"].physical_name == "producer_c"
