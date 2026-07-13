from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.regression]


def test_non_join_transform_reserves_output_headroom(monkeypatch):
    from app.config import settings
    from app.core.manipola import transform_memory

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 1_050)
    monkeypatch.setitem(settings.DEFAULTS, "MANIPOLA_TRANSFORM_HEADROOM_FACTOR", 1.0)
    monkeypatch.setattr(transform_memory, "get_current_rss_bytes", lambda: 1_000)

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        transform_memory.guard_transform_headroom(
            [pd.DataFrame({"a": range(10), "b": range(10)})],
            node_id="formula-1",
        )

    assert exc_info.value.stage == transform_memory.TRANSFORM_MEMORY_STAGE
    assert exc_info.value.path_basename == "transform_formula-1"


def test_non_join_transform_guard_can_be_disabled(monkeypatch):
    from app.config import settings
    from app.core.manipola import transform_memory

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 0)
    transform_memory.guard_transform_headroom(
        [pd.DataFrame({"a": range(10)})],
        node_id="noop",
    )


def test_effective_limit_uses_stricter_child_cap(monkeypatch):
    from app.config import settings
    from app.core.manipola.transform_memory import (
        configured_data_plane_memory_limit_bytes,
    )

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 2_000)
    monkeypatch.setitem(settings.DEFAULTS, "DATA_PLANE_EXECUTION_MODE", "subprocess")
    monkeypatch.setitem(
        settings.DEFAULTS,
        "DATA_PLANE_CHILD_RSS_MAX_BYTES",
        1_500,
    )

    assert configured_data_plane_memory_limit_bytes() == 1_500


def test_pivot_estimate_accounts_for_dense_output_shape():
    from app.core.manipola.transform_memory import estimate_pivot_memory

    frame = pd.DataFrame(
        {
            "entity": ["a", "a", "b", "b"],
            "attribute": ["x", "y", "x", "y"],
            "value": [1, 2, 3, 4],
        }
    )

    estimate = estimate_pivot_memory(
        frame,
        index_columns=["entity"],
        pivot_column="attribute",
        value_column="value",
        worker_limit_bytes=1_000_000,
        current_rss_bytes=10_000,
    )

    assert estimate.output_rows == 2
    assert estimate.output_columns == 3
    assert estimate.output_payload_bytes == (
        estimate.output_rows * estimate.output_row_width_bytes
    )
    assert estimate.scratch_bytes > 0
    assert estimate.projected_peak_bytes > estimate.current_rss_bytes


def test_pivot_cardinality_sample_handles_periodic_keys():
    from app.core.manipola.transform_memory import estimate_pivot_memory

    rows = 100_000
    frame = pd.DataFrame(
        {
            "entity": [index % 20_000 for index in range(rows)],
            "attribute": [index % 20 for index in range(rows)],
            "value": range(rows),
        }
    )
    estimate = estimate_pivot_memory(
        frame,
        index_columns=["entity"],
        pivot_column="attribute",
        value_column="value",
        worker_limit_bytes=10_000_000_000,
        current_rss_bytes=10_000,
    )

    assert 20_000 <= estimate.output_rows <= 30_000
    assert 21 <= estimate.output_columns <= 25


def test_pivot_guard_blocks_before_dense_materialization(monkeypatch):
    from app.config import settings
    from app.core.manipola import transform_memory
    from app.core.manipola.transforms import reshape

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 2_000)
    monkeypatch.setitem(
        settings.DEFAULTS,
        "DATA_PLANE_CHILD_RSS_MAX_BYTES",
        2_000,
    )
    monkeypatch.setattr(transform_memory, "get_current_rss_bytes", lambda: 1_000)
    monkeypatch.setattr(
        reshape,
        "pivot_table_dataframe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pivot materializzato nonostante il preflight")
        ),
    )
    frame = pd.DataFrame(
        {
            "entity": [f"e-{index}" for index in range(100)],
            "attribute": [f"a-{index}" for index in range(100)],
            "value": range(100),
        }
    )

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        reshape.op_pivot(frame, "entity", "attribute", "value")

    assert exc_info.value.stage == transform_memory.PIVOT_MEMORY_STAGE
    assert exc_info.value.path_basename == "pivot_output"


def test_melt_estimate_uses_exact_row_expansion():
    from app.core.manipola.transform_memory import estimate_melt_memory

    frame = pd.DataFrame(
        {"id": range(10), **{f"v{index}": range(10) for index in range(5)}}
    )
    estimate = estimate_melt_memory(
        frame,
        id_columns=["id"],
        value_columns=[f"v{index}" for index in range(5)],
        worker_limit_bytes=1_000_000,
        current_rss_bytes=10_000,
    )

    assert estimate.output_rows == 50
    assert estimate.output_columns == 3


def test_melt_guard_blocks_before_row_explosion(monkeypatch):
    from app.config import settings
    from app.core.manipola import transform_memory
    from app.core.manipola.transforms import reshape

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 2_000)
    monkeypatch.setitem(
        settings.DEFAULTS,
        "DATA_PLANE_CHILD_RSS_MAX_BYTES",
        2_000,
    )
    monkeypatch.setattr(transform_memory, "get_current_rss_bytes", lambda: 1_000)
    monkeypatch.setattr(
        reshape,
        "melt_dataframe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("melt materializzato nonostante il preflight")
        ),
    )
    frame = pd.DataFrame(
        {"id": range(100), **{f"v{index}": range(100) for index in range(20)}}
    )

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        reshape.op_melt(frame, ["id"], [f"v{index}" for index in range(20)])

    assert exc_info.value.stage == transform_memory.MELT_MEMORY_STAGE
    assert exc_info.value.path_basename == "melt_output"


def test_geometry_width_includes_native_wkb_payload():
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Polygon

    from app.core.manipola.transform_memory import estimate_frame_row_width_bytes

    geometry = Polygon(
        [(float(index), float(index % 3)) for index in range(100)]
    )
    frame = geopandas.GeoDataFrame({"id": [1]}, geometry=[geometry])
    pandas_reported = int(frame.memory_usage(index=False, deep=True).sum())

    assert estimate_frame_row_width_bytes(frame) > pandas_reported


def test_spatial_explode_counts_geometry_parts_exactly():
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import MultiPoint, Point

    from app.core.manipola.transform_memory import estimate_spatial_explode_memory

    frame = geopandas.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[
            MultiPoint([Point(index, index) for index in range(5)]),
            Point(10, 10),
        ],
    )
    estimate = estimate_spatial_explode_memory(
        frame,
        worker_limit_bytes=1_000_000,
        current_rss_bytes=10_000,
    )

    assert estimate.output_rows == 6


def test_spatial_guard_blocks_before_candidate_explosion(monkeypatch):
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point, Polygon

    from app.config import settings
    from app.core.manipola import transform_memory
    from app.core.manipola.transforms import spatial_overlay

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 2_000)
    monkeypatch.setitem(
        settings.DEFAULTS,
        "DATA_PLANE_CHILD_RSS_MAX_BYTES",
        2_000,
    )
    monkeypatch.setattr(transform_memory, "get_current_rss_bytes", lambda: 1_000)
    monkeypatch.setattr(
        spatial_overlay,
        "geodataframe_sjoin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("sjoin materializzato nonostante il preflight")
        ),
    )
    points = geopandas.GeoDataFrame(
        {"id": range(20)},
        geometry=[Point(0.5, 0.5) for _ in range(20)],
        crs="EPSG:4326",
    )
    polygons = geopandas.GeoDataFrame(
        {"zone": range(20)},
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
            for _ in range(20)
        ],
        crs="EPSG:4326",
    )

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        spatial_overlay.op_sjoin(
            [points, polygons],
            "geometry",
            "geometry",
            predicate="within",
        )

    assert exc_info.value.stage == transform_memory.SPATIAL_MEMORY_STAGE
    assert exc_info.value.path_basename == "spatial_join_output"


def test_app_factory_maps_specialized_transform_stages_to_actionable_error():
    source = (
        Path(__file__).resolve().parents[3] / "app" / "app_factory.py"
    ).read_text(encoding="utf-8")

    assert '"manipola_pivot_memory"' in source
    assert '"manipola_melt_memory"' in source
    assert '"manipola_spatial_memory"' in source
    assert (
        'suggested_action = "reduce_transform_input_or_worker_concurrency"'
        in source
    )
