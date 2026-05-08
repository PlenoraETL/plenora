from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def test_response_type_detector_is_importable_and_updates_injected_mapping() -> None:
    from app.core.arricchisci.response_type_detector import ResponseTypeDetector

    detected: dict[str, str] = {}
    detector = ResponseTypeDetector(detected)

    assert detector.infer_scalar_type(True) == "bool"
    assert detector.infer_scalar_type(1) == "int"
    assert detector.infer_scalar_type(1.5) == "real"
    assert detector.infer_scalar_type({"type": "Point", "coordinates": [1, 2]}) == "geometry:point"
    assert detected == {}

    detector.detect_geometry_type("geom", "polygon")

    assert detected == {"geom": "geometry:polygon"}


def test_response_type_detector_flatten_dict_preserves_legacy_row_shape() -> None:
    from app.core.arricchisci.response_type_detector import ResponseTypeDetector

    row = ResponseTypeDetector({}).flatten_dict(
        {
            "whole_float": 42.0,
            "decimal": 42.5,
            "payload": {"a": 1},
            "items": [1, 2],
            "name": "demo",
        }
    )

    assert row == {
        "whole_float": 42,
        "decimal": 42.5,
        "payload": '{"a": 1}',
        "items": "[1, 2]",
        "name": "demo",
    }


def test_response_processing_mixin_keeps_legacy_private_method_delegates() -> None:
    from app.core.arricchisci.enrichment_response_processing import EnrichmentResponseProcessingMixin

    class DummyExecutor(EnrichmentResponseProcessingMixin):
        def __init__(self) -> None:
            self.output_mapping = []
            self.detected_column_types: dict[str, str] = {}

    executor = DummyExecutor()

    assert executor._infer_scalar_type({"type": "Polygon", "coordinates": []}) == "geometry:polygon"
    assert executor._flatten_dict({"n": 7.0, "nested": {"x": 1}}) == {
        "n": 7,
        "nested": '{"x": 1}',
    }

    executor._detect_geometry_type("geometry", "multipoint")

    assert executor.detected_column_types == {"geometry": "geometry:multipoint"}
