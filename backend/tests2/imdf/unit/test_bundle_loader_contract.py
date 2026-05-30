"""Contract test per il consumer `plenora.indoor.bundle` (bundle_loader).

Pinna il contratto self-describing del convertitore: versione, `source`-path
per campo, flatten generico (top-level / `name.it` / `extension.*`), identity
per layer e coercion di tipo. Nessun layer/campo hardcoded: il loader itera cio'
che il `schema_descriptor` dichiara.
"""

from __future__ import annotations

import pytest

from app.core.imdf.bundle_loader import (
    BUNDLE_SCHEMA,
    DESCRIPTOR_SCHEMA,
    IndoorBundleError,
    iter_layer_records,
    parse_indoor_bundle,
)


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


def _fixture_bundle() -> dict:
    """Bundle minimale conforme a plenora.indoor.bundle v1.0 (1 layer, 2 feat).

    Copre i tre pattern di `source`: top-level (`id`), nested i18n (`name.it`)
    ed extension sollevata (`extension.area`, `extension.in_uso`).
    """
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
                        "geometry": {"type": "Polygon", "has_z": False},
                        "identity": ["id"],
                        "renderer_ref": "Locale",
                        "fields": [
                            {"name": "id", "source": "id", "type": "string", "nullable": False},
                            {"name": "name_it", "source": "name.it", "type": "string", "nullable": False},
                            {"name": "area", "source": "extension.area", "type": "float", "nullable": True},
                            {"name": "in_uso", "source": "extension.in_uso", "type": "bool", "nullable": True},
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
                        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
                        "properties": {
                            "id": "L1",
                            "name": {"it": "Stanza 1"},
                            "extension": {"area": "12.5", "in_uso": True},
                        },
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 2], [2, 2], [0, 0]]]},
                        "properties": {
                            "id": "L2",
                            "name": {"it": "Stanza 2"},
                            "extension": {"area": None},  # nullable -> None
                        },
                    },
                ],
            }
        },
    }


def test_parse_accepts_valid_bundle():
    payload = parse_indoor_bundle(_fixture_bundle())
    assert payload["schema"] == BUNDLE_SCHEMA


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update({"schema": "something.else"}),
        lambda b: b.update({"version": "2.0"}),
        lambda b: b["manifest"].pop("schema_descriptor"),
        lambda b: b["manifest"]["schema_descriptor"].update({"version": "9.9"}),
    ],
)
def test_parse_rejects_non_conforming(mutate):
    bundle = _fixture_bundle()
    mutate(bundle)
    with pytest.raises(IndoorBundleError):
        parse_indoor_bundle(bundle)


def test_iter_layer_records_is_descriptor_driven():
    payload = parse_indoor_bundle(_fixture_bundle())
    layers = list(iter_layer_records(payload))

    assert [lr.name for lr in layers] == ["Locale"]
    locale = layers[0]
    assert locale.identity == ("id",)
    assert locale.geometry_type == "Polygon"
    assert locale.has_z is False
    assert locale.renderer_ref == "Locale"
    assert [f.name for f in locale.fields] == ["id", "name_it", "area", "in_uso"]


def test_flatten_resolves_source_paths_and_types():
    payload = parse_indoor_bundle(_fixture_bundle())
    locale = next(iter_layer_records(payload))

    r0, r1 = locale.records
    # top-level source
    assert r0["id"] == "L1"
    # nested i18n source name.it
    assert r0["name_it"] == "Stanza 1"
    # extension.* sollevata + coercion float (da stringa "12.5")
    assert r0["area"] == 12.5 and isinstance(r0["area"], float)
    # bool coerced
    assert r0["in_uso"] is True
    # nullable -> None preservato
    assert r1["area"] is None
    # geometrie allineate per indice
    assert locale.geometries[0]["type"] == "Polygon"
    assert len(locale.geometries) == len(locale.records) == 2


def test_declared_layer_absent_from_data_is_skipped():
    bundle = _fixture_bundle()
    bundle["layers"].pop("Locale")  # dichiarato nel descrittore, assente nei dati
    payload = parse_indoor_bundle(bundle)
    assert list(iter_layer_records(payload)) == []
