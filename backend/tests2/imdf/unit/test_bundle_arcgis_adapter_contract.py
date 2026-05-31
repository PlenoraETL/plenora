"""Contract: IndoorBundleArcGISAdapter (bundle -> payload multi-layer generico).

Verifica che l'adapter IMDF traduca un ``plenora.indoor.bundle`` in
``ArcGISLayerSpec`` ordinati (match_column da ``identity``) + renderer per nome
layer (da ``renderer_ref`` -> ``renderers``), e che sia raggiungibile via il
registry generico di CARICA.
"""

from __future__ import annotations

import pytest

from app.core.carica.multilayer_artifact_registry import (
    MultiLayerArtifact,
    resolve_multilayer_artifact,
)
from app.core.imdf.bundle_arcgis_adapter import IndoorBundleArcGISAdapter


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


def _bundle() -> dict:
    """Bundle minimo valido a 2 layer (Localita, Locale), Point geometry."""
    return {
        "schema": "plenora.indoor.bundle",
        "version": "1.0",
        "metadata": {"site_ids": ["LO2956"]},
        "manifest": {
            "schema_descriptor": {
                "schema": "plenora.indoor.schema",
                "version": "1.0",
                "crs": "EPSG:4326",
                "layers": {
                    "Localita": {
                        "geometry": {"type": "Point", "has_z": False},
                        "identity": ["id"],
                        "fields": [
                            {"name": "id", "source": "id", "type": "string", "nullable": False},
                            {"name": "nome", "source": "name.it", "type": "string", "nullable": True},
                        ],
                        "renderer_ref": "Localita",
                    },
                    "Locale": {
                        "geometry": {"type": "Point", "has_z": False},
                        "identity": ["id"],
                        "fields": [
                            {"name": "id", "source": "id", "type": "string", "nullable": False},
                            {"name": "area", "source": "extension.area", "type": "float", "nullable": True},
                        ],
                        "renderer_ref": "Locale",
                    },
                },
            }
        },
        "renderers": {
            "Localita": {"renderer": {"type": "simple", "symbol": {"color": [1, 2, 3]}}},
            "Locale": {"renderer": {"type": "simple", "symbol": {"color": [4, 5, 6]}}},
        },
        "layers": {
            "Localita": {
                "type": "FeatureCollection",
                "features": [
                    {"properties": {"id": "L1", "name": {"it": "Loc Uno"}},
                     "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}},
                ],
            },
            "Locale": {
                "type": "FeatureCollection",
                "features": [
                    {"properties": {"id": "R1", "extension": {"area": 12.5}},
                     "geometry": {"type": "Point", "coordinates": [3.0, 4.0]}},
                    {"properties": {"id": "R2", "extension": {"area": 7.0}},
                     "geometry": {"type": "Point", "coordinates": [5.0, 6.0]}},
                ],
            },
        },
    }


def test_matches_only_indoor_bundle():
    adapter = IndoorBundleArcGISAdapter()
    assert adapter.matches(_bundle()) is True
    assert adapter.matches({"schema": "something.else"}) is False
    assert adapter.matches({}) is False


def test_to_multilayer_builds_ordered_specs_with_match_and_renderers():
    adapter = IndoorBundleArcGISAdapter()
    art = adapter.to_multilayer(_bundle())

    assert isinstance(art, MultiLayerArtifact)
    # ordine descrittore preservato
    assert [s.layer_name for s in art.layers] == ["Localita", "Locale"]
    # match_column da identity[0]
    assert all(s.match_column == "id" for s in art.layers)
    # geometria default
    assert all(s.geometry_column == "geometry" for s in art.layers)
    # feature count per layer riflesso nel GeoDataFrame
    assert [len(s.df) for s in art.layers] == [1, 2]
    # renderer per nome layer, drawing_info passthrough
    assert set(art.renderers_by_layer_name) == {"Localita", "Locale"}
    assert art.renderers_by_layer_name["Locale"]["renderer"]["symbol"]["color"] == [4, 5, 6]


def test_layer_without_renderer_ref_has_no_renderer():
    bundle = _bundle()
    del bundle["manifest"]["schema_descriptor"]["layers"]["Locale"]["renderer_ref"]
    art = IndoorBundleArcGISAdapter().to_multilayer(bundle)
    assert "Locale" not in art.renderers_by_layer_name
    assert "Localita" in art.renderers_by_layer_name


def test_reachable_via_registry_after_composition_root_import():
    # Importare il composition root registra l'adapter IMDF (idempotente).
    import app.runtime.services  # noqa: F401

    art = resolve_multilayer_artifact(_bundle())
    assert art is not None
    assert [s.layer_name for s in art.layers] == ["Localita", "Locale"]
    # payload non riconosciuto -> nessun adapter
    assert resolve_multilayer_artifact({"schema": "nope"}) is None
