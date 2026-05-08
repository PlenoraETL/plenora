"""
Smoke contract - STRUCT-001 Seam A: helper pure per il dominio files.

Verifica le invarianti minime del refactor:
"""

from __future__ import annotations

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# 1. Esposizione simboli + identity dal core service


def test_file_helpers_module_exposes_four_helpers():
    from app.shared import file_helpers as fh

    for name in ("FormatDetector", "TypeInferrer", "FileIdGenerator", "ColumnInfoBuilder"):
        cls = getattr(fh, name, None)
        assert cls is not None and isinstance(cls, type), (
            f"file_helpers.{name} mancante o non e' una classe"
        )


def test_core_file_service_uses_shared_helper_objects_by_identity():
    """app.core.files.service espone gli STESSI oggetti di file_helpers,
    cosi' codice che monkeypatcha o fa isinstance via
    `from app.core.files.service import FileIdGenerator` continua a
    comportarsi identicamente."""
    from app.core.files import service as fs_mod
    from app.shared import file_helpers as fh

    assert fs_mod.FormatDetector is fh.FormatDetector
    assert fs_mod.TypeInferrer is fh.TypeInferrer
    assert fs_mod.FileIdGenerator is fh.FileIdGenerator
    assert fs_mod.ColumnInfoBuilder is fh.ColumnInfoBuilder


# 2. FormatDetector


def test_format_detector_csv_xlsx_gpkg_unknown():
    from app.shared.file_helpers import FormatDetector

    assert FormatDetector.detect("data.csv") == "csv"
    assert FormatDetector.detect("data.CSV") == "csv"  # case-insensitive
    assert FormatDetector.detect("report.xlsx") == "xlsx"
    assert FormatDetector.detect("legacy.xls") == "xlsx"  # mappato su xlsx
    assert FormatDetector.detect("layer.gpkg") == "gpkg"
    assert FormatDetector.detect("shape.shp") == "shp"
    assert FormatDetector.detect("data.geojson") == "geojson"
    assert FormatDetector.detect("data.json") == "json"
    assert FormatDetector.detect("dir.gdb") == "gdb"
    assert FormatDetector.detect("bundle.zip") == "zip"
    assert FormatDetector.detect("strange.dxf") == "unknown"


def test_format_detector_geo_vs_tabular():
    from app.shared.file_helpers import FormatDetector

    for geo in ("geojson", "gpkg", "shp", "gdb"):
        assert FormatDetector.is_geospatial(geo) is True
        assert FormatDetector.is_tabular(geo) is False

    for tab in ("csv", "xlsx", "json"):
        assert FormatDetector.is_tabular(tab) is True
        assert FormatDetector.is_geospatial(tab) is False


# 3. TypeInferrer


def test_type_inferrer_int_float_bool_datetime():
    from app.shared.file_helpers import TypeInferrer

    assert TypeInferrer.infer_column_type(pd.Series([1, 2, 3])) == "int"
    assert TypeInferrer.infer_column_type(pd.Series([1.1, 2.2])) == "real"
    assert TypeInferrer.infer_column_type(pd.Series([True, False])) == "bool"
    assert (
        TypeInferrer.infer_column_type(pd.to_datetime(pd.Series(["2025-01-01", "2025-02-01"])))
        == "date"
    )


def test_type_inferrer_str_default_and_short_strings():
    from app.shared.file_helpers import TypeInferrer

    # Stringhe brevi non-WKT, non-date -> str
    assert TypeInferrer.infer_column_type(pd.Series(["foo", "bar"])) == "str"
    # Series vuota -> str
    assert TypeInferrer.infer_column_type(pd.Series([], dtype=object)) == "str"


def test_type_inferrer_wkt_geometry_detection():
    from app.shared.file_helpers import TypeInferrer

    # WKT prefix riconosciuto -> geometry
    s = pd.Series(["POINT (1 2)", "POINT (3 4)"])
    assert TypeInferrer.infer_column_type(s) == "geometry"


def test_type_inferrer_iso_date_detection():
    from app.shared.file_helpers import TypeInferrer

    # ISO date string -> date (cascade _DATE_FORMATS, threshold 80%)
    s = pd.Series(["2025-01-01", "2025-02-01", "2025-03-01"])
    assert TypeInferrer.infer_column_type(s) == "date"


def test_type_inferrer_italian_date_detection():
    from app.shared.file_helpers import TypeInferrer

    # IT format dd/mm/yyyy -> date
    s = pd.Series(["01/03/2025", "15/03/2025", "30/03/2025"])
    assert TypeInferrer.infer_column_type(s) == "date"


def test_type_inferrer_detect_geometry_type_mapping():
    from app.shared.file_helpers import TypeInferrer

    assert TypeInferrer.detect_geometry_type(["Point"]) == "geometry:point"
    assert TypeInferrer.detect_geometry_type(["MultiPolygon"]) == "geometry:multipolygon"
    assert TypeInferrer.detect_geometry_type([]) == "geometry"
    assert TypeInferrer.detect_geometry_type(["UnknownThing"]) == "geometry"


# 4. FileIdGenerator


def test_file_id_generator_opaque_format():
    from app.shared.file_helpers import FileIdGenerator

    # Post-N1: il formato e' `file_<uuid>`, name/timestamp ignorati.
    out = FileIdGenerator.generate(name="my_file", timestamp="20250101_120000", uuid_part="abc123")
    assert out == "file_abc123"
    assert "my_file" not in out
    assert "20250101" not in out


def test_file_id_generator_sanitize_name():
    from app.shared.file_helpers import FileIdGenerator

    # Sanitize: caratteri non alfanumerici e spazi -> underscore, max_length applicato
    assert FileIdGenerator.sanitize_name("Hello World") == "Hello_World"
    assert FileIdGenerator.sanitize_name("file/with:bad*chars") == "filewithbadchars"
    truncated = FileIdGenerator.sanitize_name("x" * 50, max_length=10)
    assert len(truncated) == 10


# 5. ColumnInfoBuilder


def test_column_info_builder_from_simple_dataframe():
    from app.shared.file_helpers import ColumnInfoBuilder

    df = pd.DataFrame({
        "id": [1, 2, 3],
        "score": [1.5, 2.5, 3.5],
        "active": [True, False, True],
        "label": ["alpha", "beta", "gamma"],
    })
    cols = ColumnInfoBuilder.from_dataframe(df)
    by_name = {c.name: c.type for c in cols}
    assert by_name["id"] == "int"
    assert by_name["score"] == "real"
    assert by_name["active"] == "bool"
    assert by_name["label"] == "str"
    assert len(cols) == 4
