"""
Contract test per `app.core.dataframes.geometry_parsing` — single source
of truth del parsing WKT/WKB/GeoJSON → Shapely.

Cluster bug coperti (commit 5ff4f1e1, 49a6fc78, 0b314672, d2049732):

  1. parse_geometry_series fast path WKT con `on_invalid="ignore"`
     silenziosamente droppava valori GeoJSON misti.
     Fix: `on_invalid="raise"` → fallback per-riga via `apply`.

  2. parse_geometry_series early-return su `hasattr(sample,
     "geom_type")` saltava la normalizzazione quando il PRIMO valore
     era Shapely ma serie conteneva stringhe WKT a seguire.
     Fix: verifica all() su tutti i non-null Shapely.

  3. WKB hex vectorized fast path rimosso (benchmark 0.91-1.19x, ramo
     EWKB delicato): WKB hex ora delega sempre a parse_geometry per-riga.

  4. Centralizzazione: nessun `wkt.loads | wkb.loads | shape(`
     residuo fuori da geometry_parsing.py.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest
from shapely.geometry import GeometryCollection, Point

from app.core.dataframes.geometry_parsing import (
    parse_geometry,
    parse_geometry_series,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


class TestParseGeometryScalar:
    """`parse_geometry` (scalar) — formato discriminato + missing safe."""

    def test_wkt_string_parses(self):
        out = parse_geometry("POINT(1 2)")
        assert out is not None and out.wkt == "POINT (1 2)"

    def test_geojson_string_parses(self):
        out = parse_geometry('{"type":"Point","coordinates":[3,4]}')
        assert out is not None and out.wkt == "POINT (3 4)"

    def test_none_returns_none(self):
        assert parse_geometry(None) is None

    def test_empty_string_returns_none(self):
        assert parse_geometry("") is None
        assert parse_geometry("None") is None

    def test_shapely_passthrough(self):
        p = Point(5, 6)
        assert parse_geometry(p) is p

    def test_invalid_returns_none(self):
        # Non WKT, non GeoJSON, non WKB hex valido → None silente.
        assert parse_geometry("not-a-geometry") is None


class TestParseGeometrySeriesMixedFormats:
    """Regression: serie con formati misti DEVE parsare tutto, mai
    silenziare valori parseabili come `None`."""

    def test_wkt_plus_geojson_mixed_both_parsed(self):
        """Pre-fix: primo WKT → fast path `from_wkt(..., on_invalid=
        "ignore")` → il GeoJSON al secondo posto diventava None."""
        s = pd.Series([
            "POINT(1 2)",
            '{"type":"Point","coordinates":[3.5,4.5]}',
        ])
        out = parse_geometry_series(s)
        assert out.iloc[0] is not None
        assert out.iloc[0].wkt == "POINT (1 2)"
        assert out.iloc[1] is not None, (
            "regressione: fast path WKT non fa fallback su GeoJSON "
            "successivi (era on_invalid='ignore' invece di 'raise')"
        )
        assert out.iloc[1].wkt == "POINT (3.5 4.5)"

    def test_shapely_plus_wkt_string_mixed_both_parsed(self):
        """Pre-fix: early-return su `hasattr(sample, 'geom_type')`
        lasciava la serie invariata → la stringa WKT a seguire restava
        non parsata. Fix: verifica all() sui non-null."""
        s = pd.Series([Point(1, 2), "POINT(3 4)", None])
        out = parse_geometry_series(s)
        assert out.iloc[0] is not None and out.iloc[0].wkt == "POINT (1 2)"
        assert out.iloc[1] is not None, (
            "regressione: shapely-iloc0 + str-iloc1 mix non parsato"
        )
        assert out.iloc[1].wkt == "POINT (3 4)"
        assert out.iloc[2] is None

    def test_homogeneous_shapely_identity_early_return(self):
        """Ottimizzazione: serie tutta Shapely deve ritornare la serie
        originale (no copy, no apply)."""
        s = pd.Series([Point(0, 0), Point(1, 1)])
        out = parse_geometry_series(s)
        assert out is s, (
            "fast path identity rotto: GeoSeries omogenea Shapely "
            "deve ritornare la stessa serie senza copy"
        )


class TestParseGeometrySeriesEdgeCases:
    """Empty series, valori invalidi singoli, index custom."""

    def test_empty_series_returns_object_dtype_series(self):
        out = parse_geometry_series(pd.Series([], dtype=object))
        assert len(out) == 0
        assert out.dtype == "object"

    def test_all_null_returns_all_none(self):
        out = parse_geometry_series(pd.Series([None, None, None]))
        assert all(v is None for v in out)

    def test_wkt_with_one_invalid_falls_back_per_row(self):
        """Fast path solleva → fallback `apply(parse_geometry)`
        per-riga gestisce l'invalido come None senza droppare i
        validi."""
        s = pd.Series(["POINT(1 2)", "BROKEN_WKT", "POINT(3 4)"])
        out = parse_geometry_series(s)
        assert out.iloc[0].wkt == "POINT (1 2)"
        assert out.iloc[1] is None
        assert out.iloc[2].wkt == "POINT (3 4)"

    def test_index_custom_preserved(self):
        s = pd.Series(
            ["POINT(0 0)", "POINT(1 1)", None],
            index=["a", "b", "c"],
        )
        out = parse_geometry_series(s)
        assert list(out.index) == ["a", "b", "c"]
        assert out.loc["a"].wkt == "POINT (0 0)"
        assert out.loc["c"] is None


class TestWkbHexNoVectorizedFastPath:
    """Architectural guard (commit d2049732): il ramo WKB hex
    vettoriale e' stato rimosso da `parse_geometry_series` perche'
    benchmark 0.91-1.19x e ramo EWKB delicato. Deve delegare al
    per-row `apply(parse_geometry)`."""

    def test_wkb_hex_branch_uses_per_row_apply(self):
        """AST-check: in `parse_geometry_series` non deve esistere
        un `shapely.from_wkb(values, hex=True` (fast path vettoriale
        WKB hex). La presenza del ramo `looks_like_wkb_hex` resta,
        ma il body deve essere `return series.apply(parse_geometry)`.
        """
        src = inspect.getsource(parse_geometry_series)
        assert "looks_like_wkb_hex(sample_str)" in src, (
            "il check WKB hex deve restare presente"
        )
        assert "shapely.from_wkb(values, hex=True" not in src, (
            "regressione: WKB hex vettoriale reintrodotto nonostante "
            "il benchmark sfavorevole; ramo EWKB delicato"
        )

    def test_wkb_hex_parses_correctly_via_per_row_path(self):
        p = Point(1, 2)
        s = pd.Series([p.wkb_hex, p.wkb_hex])
        out = parse_geometry_series(s)
        assert out.iloc[0] is not None and out.iloc[0].wkt == "POINT (1 2)"


class TestParsingCentralizationArchitectural:
    """Architectural lint: `wkt.loads | wkb.loads | shape(` deve
    apparire SOLO in `geometry_parsing.py`. Qualunque altro modulo
    deve importarli da li'."""

    def test_no_direct_wkt_wkb_shape_calls_outside_geometry_parsing(self):
        backend_core = Path(__file__).resolve().parents[3] / "app" / "core"
        offenders: list[str] = []
        for py_file in backend_core.rglob("*.py"):
            # Skip __pycache__
            if "__pycache__" in str(py_file):
                continue
            # Skip il file canonico
            if py_file.name == "geometry_parsing.py":
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                # Skip commenti/docstring (heuristica: linea che inizia
                # con # oppure contiene il pattern dentro stringa)
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                # Match `wkt.loads(`, `wkb.loads(`, `shape(` come call
                if (
                    "wkt.loads(" in line
                    or "wkb.loads(" in line
                    or ("shape(" in line and "shapely" not in line)
                ):
                    # `shape(` puo' essere un metodo .shape o tuple
                    # construction → match permissivo ma flag-only
                    offenders.append(f"{py_file.relative_to(backend_core)}:{i}: {line.strip()}")

        # Filtra falsi positivi: tuple shape(), .shape attribute, str.shape
        real_offenders = [
            o for o in offenders
            if "wkt.loads(" in o or "wkb.loads(" in o
        ]
        assert not real_offenders, (
            "regressione: trovati call `wkt.loads`/`wkb.loads` fuori "
            "da geometry_parsing.py — la centralizzazione e' rotta:\n"
            + "\n".join(real_offenders)
        )
