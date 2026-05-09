"""
Contract test — fail-closed su tutti i 32 transform di spatial.py

(D1 + D2 + D3).
"""

from __future__ import annotations

from unittest.mock import patch

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from app.core.manipola.transforms.spatial import (
    op_geo_bounds_extractor,
    op_geo_buffer,
    op_geo_count_points_in_polygons,
    op_geo_dissolve,
    op_geo_line_builder,
    op_geo_overlay,
    op_geo_polygon_builder,
    op_geo_reproject,
    op_geo_vertex_count,
    op_sjoin,
)
from app.core.manipola.plugin_executor import PluginExecutor
from app.core.projects.canvas_models import Node, Position


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


# 1. Contratto fail-closed sulle 3 transform rappresentative

@pytest.mark.parametrize(
    "transform, kwargs",
    [
        (op_geo_buffer,    {"geom_column": "geometry", "distance": 100}),
        (op_geo_reproject, {"geom_column": "geometry", "target_crs": "EPSG:3857"}),
        (op_geo_dissolve,  {"geom_column": "geometry"}),
    ],
    ids=["buffer", "reproject", "dissolve"],
)
def test_single_input_transform_reraises_on_internal_failure(transform, kwargs):
    """Se un'API interna della transform solleva, la transform non deve
    ingoiare l'errore: deve propagarlo al caller (fail-closed)."""
    df = pd.DataFrame({"geometry": [None], "id": [1]})

    # Patch del primo punto di contatto comune ai 3 transform
    # (conversione DataFrame -> GeoDataFrame). Tutti e 3 passano da qui
    # quando l'input non e' gia' un GeoDataFrame.
    with patch(
        "app.core.manipola.transforms.spatial.to_geodataframe",
        side_effect=RuntimeError("mock-internal-failure"),
    ):
        with pytest.raises(RuntimeError, match="mock-internal-failure"):
            transform(df, **kwargs)


# 2. Integrazione con PluginExecutor (L4)

def test_plugin_executor_propagates_transform_error():
    """PluginExecutor.execute deve propagare un errore sollevato dal
    transform. Pre-fix D1, il transform silenziava; post-fix, l'errore
    risale a PluginExecutor che logga "[CRASH] Errore esecuzione" e
    rilancia. Qui verifichiamo solo il fail-closed: l'errore arriva
    al chiamante dell'executor."""
    df = pd.DataFrame({"geometry": [None], "id": [1]})
    node = Node(
        id="test_node",
        type="transform",
        position=Position(x=0.0, y=0.0),
        transform_op="geo_buffer",
        config={"geom_column": "geometry", "distance": 100},
    )

    executor = PluginExecutor()

    with patch(
        "app.core.manipola.transforms.spatial.to_geodataframe",
        side_effect=RuntimeError("mock-executor-propagation"),
    ):
        with pytest.raises(RuntimeError, match="mock-executor-propagation"):
            executor.execute("geo_buffer", [df], node)


# 3. Smoke regression sull'happy path

def test_buffer_happy_path_unchanged():
    """Il fix D1 tocca solo il ramo except: l'happy path deve restare
    identico. Un GDF valido produce un GDF con la colonna buffer."""
    gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(11.34, 44.49)],
        crs="EPSG:4326",
    )
    result = op_geo_buffer(gdf, "geometry", 100, "buf")
    assert "buf" in result.columns
    assert result["buf"].iloc[0] is not None


# 4. Contratto fail-closed sulle 3 multi-input rappresentative (D2)

@pytest.mark.parametrize(
    "transform, args, kwargs",
    [
        (op_sjoin,                         ("geometry", "geometry"), {}),
        (op_geo_overlay,                   ("geometry", "geometry"), {}),
        (op_geo_count_points_in_polygons,  ("geometry", "geometry"), {}),
    ],
    ids=["sjoin", "overlay", "count_points_in_polygons"],
)
def test_multi_input_transform_reraises_on_internal_failure(transform, args, kwargs):
    """Se un'API interna di un transform multi-input solleva, la
    transform non deve ingoiare l'errore: deve propagarlo al caller
    (fail-closed). Stesso contratto di D1, verificato su multi-input."""
    df1 = pd.DataFrame({"geometry": [None], "id": [1]})
    df2 = pd.DataFrame({"geometry": [None], "zone": ["A"]})

    # Patch del primo punto di contatto comune ai transform multi-input:
    # la conversione DataFrame -> GeoDataFrame, chiamata su inputs[0] e
    # inputs[1] prima di qualsiasi operazione geometrica.
    with patch(
        "app.core.manipola.transforms.spatial.to_geodataframe",
        side_effect=RuntimeError("mock-multi-input-failure"),
    ):
        with pytest.raises(RuntimeError, match="mock-multi-input-failure"):
            transform([df1, df2], *args, **kwargs)


# 5. Smoke regression sull'happy path multi-input (D2)

def test_sjoin_happy_path_unchanged():
    """Il fix D2 tocca solo il ramo except: l'happy path multi-input
    deve restare identico. sjoin con predicate=within produce un GDF
    con le colonne del layer destro sui punti contenuti."""
    gdf_point = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(11.34, 44.49)],
        crs="EPSG:4326",
    )
    lon, lat, d = 11.34, 44.49, 0.01
    poly = Polygon([(lon - d, lat - d), (lon + d, lat - d),
                    (lon + d, lat + d), (lon - d, lat + d)])
    gdf_poly = gpd.GeoDataFrame(
        {"zone": ["A"]},
        geometry=[poly],
        crs="EPSG:4326",
    )
    result = op_sjoin([gdf_point, gdf_poly], "geometry", "geometry",
                      predicate="within")
    assert "zone" in result.columns
    assert result["zone"].iloc[0] == "A"


# 6. Contratto fail-closed sui 4 builder/extractor (D3) + chiusura S6

@pytest.mark.parametrize(
    "transform, args, kwargs",
    [
        (op_geo_bounds_extractor, ("geometry",), {}),
        (op_geo_vertex_count,     ("geometry",), {}),
        (op_geo_line_builder,     ("geometry",), {"group_column": "grp"}),
        (op_geo_polygon_builder,  ("geometry",), {"group_column": "grp"}),
    ],
    ids=["bounds_extractor", "vertex_count", "line_builder", "polygon_builder"],
)
def test_builder_extractor_reraises_and_closes_s6(transform, args, kwargs):
    """I 4 builder/extractor devono fail-closed come D1/D2. In piu', il
    ramo except NON deve re-invocare `to_geodataframe` (smell S6 pre-fix:
    un fallback rischioso che poteva a sua volta sollevare, mascherando
    l'errore originale).

    Verifica combinata:
    - pytest.raises con il messaggio originale (non quello del fallback)
    - mock.call_count == 1 (to_geodataframe chiamata solo nel try, mai
      nell'except — smell S6 chiuso)."""
    df = pd.DataFrame({"geometry": [None], "id": [1], "grp": ["A"]})

    with patch(
        f"{transform.__module__}.to_geodataframe",
        side_effect=RuntimeError("mock-builder-failure"),
    ) as mock_tgdf:
        with pytest.raises(RuntimeError, match="mock-builder-failure"):
            transform(df, *args, **kwargs)

    # Pre-fix D3: il ramo except tentava di nuovo to_geodataframe,
    # call_count sarebbe stato 2. Post-fix: solo 1 (la chiamata nel try).
    assert mock_tgdf.call_count == 1, (
        f"smell S6 non chiuso: to_geodataframe invocata {mock_tgdf.call_count} "
        f"volte (attese: 1). Il ramo except sta ancora tentando il fallback."
    )


# 7. Smoke regression sull'happy path builder (D3)

def test_bounds_extractor_happy_path_unchanged():
    """Il fix D3 tocca solo il ramo except: l'happy path deve restare
    identico. bounds_extractor su un GDF valido produce le 4 colonne
    min_x, min_y, max_x, max_y."""
    gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(11.34, 44.49)],
        crs="EPSG:4326",
    )
    result = op_geo_bounds_extractor(gdf, "geometry")
    for col in ("min_x", "min_y", "max_x", "max_y"):
        assert col in result.columns
