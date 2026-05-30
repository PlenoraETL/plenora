"""Contract test per l'assemblaggio GeoDataFrame dal plenora.indoor.bundle.

Verifica il ponte loader-puro -> boundary core/dataframes: colonne dai `fields`,
CRS dal descrittore, identity, drop-Z 3D->2D. Richiede geopandas (gira nel
container plenora-tests2-bind).
"""

from __future__ import annotations

import pytest

from app.core.imdf.bundle_geo import build_layer_geodataframes, layer_geodataframes_only
from app.core.imdf.bundle_loader import BUNDLE_SCHEMA, DESCRIPTOR_SCHEMA, parse_indoor_bundle


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


def _bundle(*, has_z: bool, coords) -> dict:
    return {
        "schema": BUNDLE_SCHEMA,
        "version": "1.0",
        "metadata": {"crs": "EPSG:4326", "site_ids": ["S1"]},
        "manifest": {
            "schema_descriptor": {
                "schema": DESCRIPTOR_SCHEMA,
                "version": "1.0",
                "crs": "EPSG:4326",
                "layers": {
                    "Locale": {
                        "geometry": {"type": "Polygon", "has_z": has_z},
                        "identity": ["id"],
                        "renderer_ref": "Locale",
                        "fields": [
                            {"name": "id", "source": "id", "type": "string", "nullable": False},
                            {"name": "name_it", "source": "name.it", "type": "string", "nullable": False},
                            {"name": "area", "source": "extension.area", "type": "float", "nullable": True},
                        ],
                    }
                },
            }
        },
        "layers": {
            "Locale": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": coords},
                        "properties": {"id": "L1", "name": {"it": "Stanza 1"}, "extension": {"area": "12.5"}},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": coords},
                        "properties": {"id": "L2", "name": {"it": "Stanza 2"}, "extension": {"area": None}},
                    },
                ],
            }
        },
    }


_RING_2D = [[[0, 0], [0, 1], [1, 1], [0, 0]]]
_RING_3D = [[[0, 0, 5], [0, 1, 5], [1, 1, 5], [0, 0, 5]]]


def test_build_layer_geodataframes_shape_and_types():
    payload = parse_indoor_bundle(_bundle(has_z=False, coords=_RING_2D))
    layers = build_layer_geodataframes(payload)

    assert set(layers) == {"Locale"}
    entry = layers["Locale"]
    assert entry["identity"] == ("id",)
    assert entry["renderer_ref"] == "Locale"

    gdf = entry["gdf"]
    assert len(gdf) == 2
    # colonne = fields dichiarati + geometry
    assert set(gdf.columns) == {"id", "name_it", "area", "geometry"}
    assert str(gdf.crs).upper().endswith("4326")
    assert gdf.geometry.iloc[0].geom_type == "Polygon"
    # tipi/flatten
    assert gdf["name_it"].iloc[0] == "Stanza 1"
    assert gdf["area"].iloc[0] == 12.5
    # nullable preservato
    assert gdf["area"].iloc[1] is None or gdf["area"].isna().iloc[1]


def test_drop_z_flattens_3d_geometry():
    payload = parse_indoor_bundle(_bundle(has_z=True, coords=_RING_3D))
    gdf = build_layer_geodataframes(payload, drop_z=True)["Locale"]["gdf"]
    assert gdf.geometry.iloc[0].has_z is False


def test_keep_z_when_drop_disabled():
    payload = parse_indoor_bundle(_bundle(has_z=True, coords=_RING_3D))
    gdf = build_layer_geodataframes(payload, drop_z=False)["Locale"]["gdf"]
    assert gdf.geometry.iloc[0].has_z is True


def test_layer_geodataframes_only_compat_shape():
    payload = parse_indoor_bundle(_bundle(has_z=False, coords=_RING_2D))
    layers = layer_geodataframes_only(payload)
    assert set(layers) == {"Locale"}
    # shape compat con load_imdf_layers: {name: GeoDataFrame}
    assert hasattr(layers["Locale"], "geometry")
