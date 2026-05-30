"""Contract test: scrittura ArcGIS Feature Service multi-layer (GENERICA).

Capability domain-neutral nel writer condiviso (`FeaturesService`/
`ArcGISFeatureWriter`): layer 0 crea il servizio, layer 1..N vengono aggiunti.
Nessuna conoscenza IMDF: il chiamante fornisce gli `ArcGISLayerSpec`.
"""

from __future__ import annotations

import pytest

from app.core.connections.arcgis_writer_boundary import ArcGISLayerSpec
from app.core.features.features_service import FeaturesService


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract]


class _FakeAddResult:
    def __init__(self, n: int):
        self.feature_count = n
        self.errors: tuple = ()


class _FakeWriter:
    """Registra le chiamate e ritorna risultati finti (nessun ArcGIS)."""

    def __init__(self, service_url: str = "https://arc.test/svc/FeatureServer"):
        self.service_url = service_url
        self.create_calls: list[str] = []
        self.add_calls: list[tuple[str, int]] = []

    def create_service_from_df(self, conn_info, *, service_name, layer_name, df,
                               geometry_column, source_srid, folder_id, column_mappings):
        self.create_calls.append(layer_name)
        return {
            "success": True, "service_url": self.service_url,
            "item_id": "item-1", "feature_count": len(df),
        }

    def add_layer_to_service(self, conn_info, service_url, df, layer_name, layer_id, srid):
        self.add_calls.append((layer_name, layer_id))
        return _FakeAddResult(len(df))


def _svc(writer) -> FeaturesService:
    svc = FeaturesService.__new__(FeaturesService)  # bypass __init__ (no provider)
    svc._writer = writer
    svc._resolve_write_connection = lambda connection_id: object()  # dummy conn
    return svc


def test_orchestrates_create_layer0_plus_add_rest():
    fake = _FakeWriter()
    svc = _svc(fake)
    layers = [
        ArcGISLayerSpec("A", [1, 2, 3]),
        ArcGISLayerSpec("B", [1, 2]),
        ArcGISLayerSpec("C", [1]),
    ]
    res = svc.create_service_with_layers("conn-1", "MySvc", layers)

    assert res["success"] is True
    assert res["service_url"] == "https://arc.test/svc/FeatureServer"
    assert res["item_id"] == "item-1"
    # layer 0 via create, 1..N via add (nell'ordine fornito)
    assert fake.create_calls == ["A"]
    assert fake.add_calls == [("B", 1), ("C", 2)]
    # id/url per-layer + somma feature
    assert [l["id"] for l in res["layers"]] == [0, 1, 2]
    assert [l["url"] for l in res["layers"]] == [
        "https://arc.test/svc/FeatureServer/0",
        "https://arc.test/svc/FeatureServer/1",
        "https://arc.test/svc/FeatureServer/2",
    ]
    assert res["feature_count"] == 6


def test_single_layer_uses_only_create():
    fake = _FakeWriter()
    res = _svc(fake).create_service_with_layers("c", "S", [ArcGISLayerSpec("Solo", [1, 1])])
    assert fake.create_calls == ["Solo"]
    assert fake.add_calls == []
    assert res["feature_count"] == 2


def test_empty_layers_raises():
    with pytest.raises(ValueError):
        _svc(_FakeWriter()).create_service_with_layers("c", "S", [])


def test_create_failure_returns_unsuccessful():
    class _NoUrlWriter(_FakeWriter):
        def create_service_from_df(self, conn_info, **kw):
            return {"success": False}  # niente service_url

    res = _svc(_NoUrlWriter()).create_service_with_layers(
        "c", "S", [ArcGISLayerSpec("A", [1])]
    )
    assert res["success"] is False
    assert res["layers"] == []


def test_add_layer_failure_is_isolated_per_layer():
    class _FlakyWriter(_FakeWriter):
        def add_layer_to_service(self, conn_info, service_url, df, layer_name, layer_id, srid):
            if layer_name == "B":
                raise RuntimeError("boom")
            return _FakeAddResult(len(df))

    res = _svc(_FlakyWriter()).create_service_with_layers(
        "c", "S",
        [ArcGISLayerSpec("A", [1]), ArcGISLayerSpec("B", [1]), ArcGISLayerSpec("C", [1])],
    )
    # il servizio + gli altri layer restano; l'errore del layer B e' isolato
    assert res["success"] is True
    b = next(l for l in res["layers"] if l["name"] == "B")
    assert b["feature_count"] == 0 and b["errors"]
    assert any("add_layer 'B'" in e for e in res["errors"])


# --- write_layers_to_existing_service (append/upsert/update) ---

class _FakeModeResult:
    def __init__(self, added=0, updated=0, deleted=0, skipped=0, errors=()):
        self.added_count = added
        self.updated_count = updated
        self.deleted_count = deleted
        self.skipped_count = skipped
        self.errors = list(errors)


class _FakeClientWithLayers:
    def __init__(self, layers, fail=False):
        self._layers = layers
        self._fail = fail

    def list_layers(self, conn, service_url):
        if self._fail:
            raise RuntimeError("list down")
        return self._layers


def _svc_existing(client, mode_results, monkeypatch):
    import app.core.features.features_service as fs
    svc = fs.FeaturesService.__new__(fs.FeaturesService)
    svc._client = client
    svc._resolve_write_connection = lambda connection_id: object()
    calls = []

    def _fake_mode(*, client, conn, layer_url, gdf, mode, match_col, target_srid):
        calls.append((layer_url, mode, match_col))
        return mode_results.pop(0)

    monkeypatch.setattr(fs, "publish_existing_layer_mode", _fake_mode)
    return svc, calls


def test_existing_append_matches_layers_by_name(monkeypatch):
    client = _FakeClientWithLayers([
        {"name": "A", "url": "https://x/A", "id": 0},
        {"name": "B", "url": "https://x/B", "id": 1},
    ])
    svc, calls = _svc_existing(
        client, [_FakeModeResult(added=3), _FakeModeResult(added=2)], monkeypatch,
    )
    res = svc.write_layers_to_existing_service(
        "c", "https://x", [ArcGISLayerSpec("A", [1]), ArcGISLayerSpec("B", [1])], "append",
    )
    assert res["success"] is True
    assert calls == [("https://x/A", "append", None), ("https://x/B", "append", None)]
    assert res["feature_count"] == 5


def test_existing_update_uses_updated_count_and_match_column(monkeypatch):
    client = _FakeClientWithLayers([{"name": "A", "url": "https://x/A", "id": 0}])
    svc, calls = _svc_existing(client, [_FakeModeResult(updated=4)], monkeypatch)
    res = svc.write_layers_to_existing_service(
        "c", "https://x", [ArcGISLayerSpec("A", [1], match_column="id")], "update",
    )
    assert calls == [("https://x/A", "update", "id")]
    assert res["layers"][0]["feature_count"] == 4  # updated, not added


def test_existing_unmatched_layer_is_error(monkeypatch):
    client = _FakeClientWithLayers([{"name": "A", "url": "https://x/A", "id": 0}])
    svc, _ = _svc_existing(client, [_FakeModeResult(added=1)], monkeypatch)
    res = svc.write_layers_to_existing_service(
        "c", "https://x",
        [ArcGISLayerSpec("A", [1]), ArcGISLayerSpec("GHOST", [1])], "append",
    )
    # A scritto (added=1) ma GHOST manca -> errore -> success False
    assert res["success"] is False
    assert any("GHOST" in e for e in res["errors"])


def test_existing_list_layers_failure_returns_unsuccessful(monkeypatch):
    svc, _ = _svc_existing(_FakeClientWithLayers([], fail=True), [], monkeypatch)
    res = svc.write_layers_to_existing_service(
        "c", "https://x", [ArcGISLayerSpec("A", [1])], "append",
    )
    assert res["success"] is False
    assert any("list_layers" in e for e in res["errors"])


# --- apply_layer_renderers ---

class _RendererClient:
    def __init__(self, fail_urls=()):
        self.calls = []
        self._fail = set(fail_urls)

    def update_layer_renderer(self, *, conn, layer_url, drawing_info):
        self.calls.append((layer_url, drawing_info))
        if layer_url in self._fail:
            raise RuntimeError("boom")
        return {"success": True}


def _svc_renderer(client):
    import app.core.features.features_service as fs
    svc = fs.FeaturesService.__new__(fs.FeaturesService)
    svc._client = client
    svc._resolve_write_connection = lambda connection_id: object()
    return svc


def test_apply_renderers_applies_each_and_counts():
    client = _RendererClient()
    res = _svc_renderer(client).apply_layer_renderers("c", {
        "https://x/0": {"type": "simple"},
        "https://x/1": {"type": "unique"},
        "https://x/2": None,   # skip
    })
    assert res["applied"] == 2
    assert res["errors"] == []
    assert [c[0] for c in client.calls] == ["https://x/0", "https://x/1"]


def test_apply_renderers_isolates_failures():
    client = _RendererClient(fail_urls={"https://x/1"})
    res = _svc_renderer(client).apply_layer_renderers("c", {
        "https://x/0": {"type": "simple"},
        "https://x/1": {"type": "simple"},
    })
    assert res["applied"] == 1
    assert any("https://x/1" in e for e in res["errors"])
