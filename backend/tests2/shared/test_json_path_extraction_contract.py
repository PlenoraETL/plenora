from app.shared import api_utils
from app.shared.json_path import extract_json_value, geojson_to_wkt


def test_json_path_extracts_nested_filter_with_dotted_key():
    payload = {
        "features": [
            {"properties": {"origine": "old", "p1": 1}},
            {"properties": {"origine": "asc3_2021", "p1": 42}},
        ]
    }

    assert (
        extract_json_value(payload, "features[properties.origine=asc3_2021].properties.p1")
        == 42
    )


def test_json_path_preserves_longest_first_dotted_dict_keys():
    payload = {"attributes": {"UniqueValue.Pixel Value": "A"}}

    assert extract_json_value(payload, "attributes.UniqueValue.Pixel Value") == "A"


def test_geojson_geometry_values_are_converted_to_wkt():
    payload = {"geometry": {"type": "Point", "coordinates": [12.5, 41.9]}}

    assert extract_json_value(payload, "geometry") == "POINT (12.5 41.9)"
    assert geojson_to_wkt(payload["geometry"]) == "POINT (12.5 41.9)"


def test_api_utils_no_longer_owns_json_path_helpers():
    assert not hasattr(api_utils, "extract_json_value")
    assert not hasattr(api_utils, "geojson_to_wkt")
