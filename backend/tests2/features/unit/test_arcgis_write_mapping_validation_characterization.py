"""Characterization — validazione di ArcGISWriteMappingContract.apply PRIMA di
estrarre check_mapping (Epic CanvasVal 0.4). Pinna i raise/ordine attuali, così
il rewire verso la regola pura condivisa resta backward-compatible.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.connections.arcgis_write_mapping import ArcGISWriteMappingContract


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract]


def _df():
    return pd.DataFrame([{"a": 1, "b": 2, "geometry": "POINT (0 0)"}])


def test_duplicate_sources_raise():
    c = ArcGISWriteMappingContract.from_raw([
        {"source": "a", "dest": "A1"},
        {"source": "a", "dest": "A2"},
    ])
    with pytest.raises(ValueError, match="sorgenti duplicate: a"):
        c.apply(_df(), geometry_column="geometry")


def test_duplicate_dests_raise():
    c = ArcGISWriteMappingContract.from_raw([
        {"source": "a", "dest": "X"},
        {"source": "b", "dest": "X"},
    ])
    with pytest.raises(ValueError, match="destinazioni duplicate: X"):
        c.apply(_df(), geometry_column="geometry")


def test_missing_source_raises():
    c = ArcGISWriteMappingContract.from_raw([{"source": "nope", "dest": "X"}])
    with pytest.raises(ValueError, match="colonne sorgente mancanti: nope"):
        c.apply(_df(), geometry_column="geometry")


def test_match_column_not_produced_raises():
    c = ArcGISWriteMappingContract.from_raw([{"source": "a", "dest": "A1"}])
    with pytest.raises(ValueError, match="colonna match richiesta: B_ID"):
        c.apply(_df(), geometry_column="geometry", required_match_column="B_ID")


def test_no_active_mappings_returns_unchanged():
    c = ArcGISWriteMappingContract.from_raw([{"source": "a", "dest": "X", "skip": True}])
    out = c.apply(_df(), geometry_column="geometry")
    assert list(out.df.columns) == ["a", "b", "geometry"]
    assert out.geometry_column == "geometry"


def test_order_dup_source_before_missing():
    # 'x' duplicata E assente: deve scattare prima il dup-source (ordine di apply).
    c = ArcGISWriteMappingContract.from_raw([
        {"source": "x", "dest": "X1"},
        {"source": "x", "dest": "X2"},
    ])
    with pytest.raises(ValueError, match="sorgenti duplicate: x"):
        c.apply(_df(), geometry_column="geometry")
