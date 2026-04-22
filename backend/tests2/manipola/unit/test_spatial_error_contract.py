"""Contract test — fail-closed sui transform di spatial.py (D1 + D2).

Verifica che un errore interno dei 28 transform (17 single-input di D1 +
11 multi-input di D2) di `spatial.py` venga propagato al caller invece
di essere silenziato. Pre-fix, ogni transform catturava qualunque
Exception, loggava "[CRASH] Errore X" e ritornava l'input originale
(fail-open illegittimo — cfr. audit Batch 4). Post-fix, il catch si
limita a loggare e rilanciare bare.

Il test copre:
1. Contratto fail-closed locale sulle 3 transform single-input
   rappresentative di D1 (buffer, reproject, dissolve).
2. Integrazione con PluginExecutor (L4): l'errore risale al caller
   senza essere assorbito.
3. Smoke regression sull'happy path single-input (op_geo_buffer).
4. Contratto fail-closed locale sulle 3 transform multi-input
   rappresentative di D2 (sjoin, overlay, count_points_in_polygons).
5. Smoke regression sull'happy path multi-input (op_sjoin).

Il focus e' il contratto fail-closed. Il dettaglio del log (tag [CRASH],
exc_info) non e' parte dell'asserzione principale.
"""

from __future__ import annotations

from unittest.mock import patch

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from app.core.manipola.transforms.spatial import (
    op_geo_buffer,
    op_geo_count_points_in_polygons,
    op_geo_dissolve,
    op_geo_overlay,
    op_geo_reproject,
    op_sjoin,
)
from app.core.manipola.plugin_executor import PluginExecutor
from app.shared.models import Node, Position


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


# =============================================================================
# 1. Contratto fail-closed sulle 3 transform rappresentative
# =============================================================================

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


# =============================================================================
# 2. Integrazione con PluginExecutor (L4)
# =============================================================================

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


# =============================================================================
# 3. Smoke regression sull'happy path
# =============================================================================

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


# =============================================================================
# 4. Contratto fail-closed sulle 3 multi-input rappresentative (D2)
# =============================================================================

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


# =============================================================================
# 5. Smoke regression sull'happy path multi-input (D2)
# =============================================================================

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
