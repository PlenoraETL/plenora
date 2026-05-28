"""
Contract test per `app.core.dataframes.geometry_serialization` —
single source of truth della serializzazione Shapely → WKT.

Cluster bug coperti (commit 406b5031, 572a15a0):

  1. Missing contract: tutti i missing (`None`, NaN, `pd.NA`) devono
     normalizzare a `None`. Pre-fix le serializzazioni per-riga
     (`apply(lambda g: g.wkt if g else None)`) producevano `NaN` su
     alcuni path e potevano sollevare AttributeError su `pd.NA.wkt`.

  2. `empty_as_null=True`: geometrie vuote (POLYGON EMPTY,
     GEOMETRYCOLLECTION EMPTY) → None invece di stringa WKT "EMPTY".

  3. `preserve_non_geometry=True`: caller (es. `geospatial_io.
     _converti_geometrie_secondarie_in_wkt`) puo' avere colonne
     mix Shapely + altro; i non-geometry passano invariati nell'
     output invece di esplodere su `shapely.to_wkt`.

  4. Centralizzazione: nessun `.apply(...wkt)` residuo nei moduli
     core (escluso geospatial_io fino al fix 572a15a0).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import GeometryCollection, Point, Polygon

from app.core.dataframes.geometry_serialization import (
    geometry_series_to_wkt,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


class TestMissingNormalization:
    """`None`/NaN/pd.NA → `None` uniformemente."""

    def test_none_returns_none(self):
        out = geometry_series_to_wkt(pd.Series([Point(1, 2), None], dtype=object))
        assert out.iloc[0] == "POINT (1 2)"
        assert out.iloc[1] is None

    def test_nan_returns_none(self):
        out = geometry_series_to_wkt(
            pd.Series([Point(1, 2), np.nan], dtype=object)
        )
        assert out.iloc[0] == "POINT (1 2)"
        assert out.iloc[1] is None, (
            "regressione: NaN passa attraverso come stringa 'nan' o "
            "solleva su shapely.to_wkt — il contratto richiede None"
        )

    def test_pd_na_returns_none(self):
        out = geometry_series_to_wkt(
            pd.Series([Point(1, 2), pd.NA], dtype=object)
        )
        assert out.iloc[0] == "POINT (1 2)"
        assert out.iloc[1] is None, (
            "regressione: pd.NA non normalizzato a None — pre-fix il "
            "lambda `g.wkt if g else None` sollevava AttributeError "
            "perche' pd.NA non e' falsy in modo coerente"
        )

    def test_all_missing_returns_all_none(self):
        out = geometry_series_to_wkt(
            pd.Series([None, np.nan, pd.NA], dtype=object)
        )
        assert all(v is None for v in out)


class TestEmptyAsNullFlag:
    """`empty_as_null=False` (default) preserva empty come stringa WKT;
    `True` la normalizza a `None`."""

    def test_default_preserves_empty_geom_as_wkt(self):
        s = pd.Series([Point(1, 2), GeometryCollection()])
        out = geometry_series_to_wkt(s)
        assert out.iloc[0] == "POINT (1 2)"
        assert "EMPTY" in out.iloc[1], (
            f"default deve preservare empty come 'EMPTY' string, "
            f"ottenuto: {out.iloc[1]!r}"
        )

    def test_empty_as_null_normalizes_empty_to_none(self):
        s = pd.Series([Point(1, 2), GeometryCollection()])
        out = geometry_series_to_wkt(s, empty_as_null=True)
        assert out.iloc[0] == "POINT (1 2)"
        assert out.iloc[1] is None


class TestPreserveNonGeometryFlag:
    """`preserve_non_geometry=True` lascia invariati i valori non-
    Shapely non-missing nell'output."""

    def test_default_strict_mode_no_passthrough(self):
        """Senza il flag, il caller assume serie omogenea Shapely o
        missing. Se ci sono non-Shapely sopravvissuti, l'output
        avrebbe comportamento indefinito; il flag e' opt-in
        esplicito."""
        s = pd.Series([Point(1, 2), None])
        out = geometry_series_to_wkt(s)
        assert out.iloc[0] == "POINT (1 2)" and out.iloc[1] is None

    def test_preserve_non_geometry_keeps_string(self):
        s = pd.Series([Point(1, 2), "raw_string", Point(3, 4)])
        out = geometry_series_to_wkt(s, preserve_non_geometry=True)
        assert out.iloc[0] == "POINT (1 2)"
        assert out.iloc[1] == "raw_string", (
            "regressione: preserve_non_geometry non riposiziona il "
            "valore originale invariato"
        )
        assert out.iloc[2] == "POINT (3 4)"

    def test_preserve_non_geometry_keeps_dict(self):
        geojson_dict = {"type": "Point", "coordinates": [1, 2]}
        s = pd.Series(
            [Point(0, 0), geojson_dict],
            dtype=object,
        )
        out = geometry_series_to_wkt(s, preserve_non_geometry=True)
        assert out.iloc[0] == "POINT (0 0)"
        assert out.iloc[1] == geojson_dict

    def test_preserve_with_empty_as_null_combo(self):
        s = pd.Series([Point(1, 2), GeometryCollection(), "passthrough"])
        out = geometry_series_to_wkt(
            s, empty_as_null=True, preserve_non_geometry=True
        )
        assert out.iloc[0] == "POINT (1 2)"
        assert out.iloc[1] is None
        assert out.iloc[2] == "passthrough"


class TestIndexAndDtype:
    """Index custom preservato; dtype output sempre object."""

    def test_index_preserved_with_string_keys(self):
        s = pd.Series(
            [Point(0, 0), None, Point(2, 2)],
            index=["a", "b", "c"],
        )
        out = geometry_series_to_wkt(s)
        assert list(out.index) == ["a", "b", "c"]
        assert out.loc["a"] == "POINT (0 0)"
        assert out.loc["b"] is None
        assert out.loc["c"] == "POINT (2 2)"

    def test_output_dtype_object(self):
        s = pd.Series([Point(1, 2)])
        out = geometry_series_to_wkt(s)
        assert out.dtype == "object"


class TestWktFormat:
    """`rounding_precision=-1, trim=True` produce WKT compatto
    equivalente al `.wkt` attribute Shapely sui non-null."""

    def test_wkt_output_equivalent_to_scalar_wkt(self):
        geoms = [Point(1.5, 2.5), Point(3, 4)]
        s = pd.Series(geoms)
        out = geometry_series_to_wkt(s)
        for i, g in enumerate(geoms):
            assert out.iloc[i] == g.wkt, (
                f"output WKT divergente da .wkt scalar: "
                f"{out.iloc[i]!r} vs {g.wkt!r}"
            )

    def test_polygon_wkt_format(self):
        poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        out = geometry_series_to_wkt(pd.Series([poly]))
        assert out.iloc[0].startswith("POLYGON")


class TestSerializationCentralizationArchitectural:
    """Architectural lint: nessun `.apply(...wkt)` residuo nei file
    core dopo la centralizzazione (commit 572a15a0)."""

    def test_no_per_row_wkt_apply_in_core_modules(self):
        """Sweep su `backend/app/core` per pattern `.apply(...wkt`
        — exclude il modulo helper stesso (puo' menzionarlo in
        docstring/commenti)."""
        backend_core = Path(__file__).resolve().parents[3] / "app" / "core"
        offenders: list[str] = []
        for py_file in backend_core.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            if py_file.name == "geometry_serialization.py":
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                # Pattern .apply(...wkt — match permissivo
                if ".apply(" in line and ".wkt" in line:
                    offenders.append(
                        f"{py_file.relative_to(backend_core)}:{i}: "
                        f"{line.strip()}"
                    )
        assert not offenders, (
            "regressione centralizzazione: trovato `.apply(...wkt)` "
            "fuori da geometry_serialization.py. Migrare a "
            "`geometry_series_to_wkt`:\n" + "\n".join(offenders)
        )
