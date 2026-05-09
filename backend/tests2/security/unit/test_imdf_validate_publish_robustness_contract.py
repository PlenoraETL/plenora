"""
test_imdf_validate_publish_robustness_contract.py

Contract test per i check "robustezza pre-publish" aggiunti a
"""
import importlib

import pytest

importlib.import_module("app.utils.logger")
importlib.import_module("app.core.features.features_service")
importlib.import_module("app.core.features.arcgis_client")
importlib.import_module("app.core.imdf.loader")

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


class _FakeConnInfo:
    username = "tester"
    server_url = "https://example.com"


# Mock CRS che espone to_epsg() come pyproj CRS
class _FakeCrs:
    def __init__(self, epsg): self._epsg = epsg
    def to_epsg(self): return self._epsg


class _FakeGDF:
    """Wrapper minimale attorno a un pandas DataFrame. GeoDataFrame-like."""
    def __init__(self, df, crs_epsg=4326):
        self._df = df
        self.crs = _FakeCrs(crs_epsg) if crs_epsg else None
    @property
    def columns(self):
        return list(self._df.columns)
    def __len__(self):
        return len(self._df)
    def __contains__(self, col):
        return col in self._df.columns
    def __getitem__(self, col):
        return self._df[col]


@pytest.fixture
def fake_bundle(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "task-robust"
    bundle_dir.mkdir()
    (bundle_dir / "fake.gdb").mkdir()
    from types import SimpleNamespace
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "settings", SimpleNamespace(
        IMDF_BUNDLES_DIR=tmp_path,
        IMDF_BUNDLE_TTL_HOURS=24,
    ))
    return bundle_dir


def _install_stubs(monkeypatch, gdf, get_layer_schema_ret, capabilities="Query,Create,Update,Delete"):
    import app.core.imdf.loader as loader_mod
    import app.core.connections.arcgis_connection_resolver as adapter_mod
    import app.core.imdf.arcgis.service_discovery as service_discovery_mod

    monkeypatch.setattr(loader_mod, "load_imdf_layers", lambda gdb: {"Locale": gdf})

    class _FakeArcgisClient:
        def list_layers(self, conn, url):
            return [{"name": "Locale", "url": "https://x/Locale", "id": 0, "feature_count": 10}]
        def get_layer_schema(self, conn, url):
            ret = dict(get_layer_schema_ret)
            ret.setdefault("capabilities", capabilities)
            return ret
        def query_features(self, conn, layer_url, where, out_fields, return_geometry):
            return [], ["OBJECTID"]

    fake_client = _FakeArcgisClient()
    monkeypatch.setattr(
        adapter_mod,
        "resolve_arcgis_connection",
        lambda connection_id: (_FakeConnInfo(), fake_client),
    )
    monkeypatch.setattr(
        service_discovery_mod, "_find_existing_service",
        lambda *a, **kw: ("https://example.com/svc", "item-1", None),
    )
    monkeypatch.setattr(
        service_discovery_mod, "_is_service_name_available",
        lambda c, co, fn: (True, None),
    )
    monkeypatch.setattr(
        service_discovery_mod, "_list_user_folders",
        lambda c, co: [], raising=False,
    )


def _layer(report, name="Locale"):
    return next(l for l in report["layers"] if l["name"] == name)


# ── 1. STRING_TRUNCATION ─────────────────────────────────────────────
def test_string_truncation_warning_when_bundle_exceeds_fs_length(fake_bundle, monkeypatch):
    import pandas as pd
    df = pd.DataFrame({
        "UNIT_ID": ["U-1", "U-2"],
        "NOTE":    ["x" * 60, "short"],  # max 60 chars
        "geometry": [None, None],
    })
    gdf = _FakeGDF(df)
    _install_stubs(monkeypatch, gdf, {
        "fields": [
            {"name": "UNIT_ID", "field_type": "esriFieldTypeString", "length": 50, "nullable": True},
            {"name": "NOTE",    "field_type": "esriFieldTypeString", "length": 30, "nullable": True},  # < 60
        ],
        "spatial_reference": {"wkid": 4326},
    })

    from app.core.imdf.validator import validate_publish
    report = validate_publish(bundle_task_id="task-robust", connection_id="c",
                              fs_name="MyFS", mode="append")
    layer = _layer(report)
    trunc = [i for i in layer["data_issues"] if i.get("code") == "STRING_TRUNCATION"]
    assert len(trunc) == 1, f"Atteso 1 STRING_TRUNCATION, trovato: {layer['data_issues']}"
    assert "NOTE" in trunc[0]["message"]
    assert "60" in trunc[0]["message"] and "30" in trunc[0]["message"]


def test_string_truncation_no_warning_when_fits(fake_bundle, monkeypatch):
    import pandas as pd
    df = pd.DataFrame({"UNIT_ID": ["U-1"], "NOTE": ["short"], "geometry": [None]})
    gdf = _FakeGDF(df)
    _install_stubs(monkeypatch, gdf, {
        "fields": [
            {"name": "UNIT_ID", "field_type": "esriFieldTypeString", "length": 50, "nullable": True},
            {"name": "NOTE",    "field_type": "esriFieldTypeString", "length": 255, "nullable": True},
        ],
        "spatial_reference": {"wkid": 4326},
    })
    from app.core.imdf.validator import validate_publish
    report = validate_publish(bundle_task_id="task-robust", connection_id="c",
                              fs_name="MyFS", mode="append")
    layer = _layer(report)
    trunc = [i for i in layer["data_issues"] if i.get("code") == "STRING_TRUNCATION"]
    assert trunc == []


# ── 2. NULL_ON_NON_NULLABLE ──────────────────────────────────────────
def test_null_on_non_nullable_error_in_append(fake_bundle, monkeypatch):
    import pandas as pd
    df = pd.DataFrame({
        "UNIT_ID":  ["U-1", "U-2", "U-3"],
        "CODE":     ["A", None, "C"],  # 1 null
        "geometry": [None, None, None],
    })
    _install_stubs(monkeypatch, _FakeGDF(df), {
        "fields": [
            {"name": "UNIT_ID", "field_type": "esriFieldTypeString", "length": 50, "nullable": True},
            {"name": "CODE",    "field_type": "esriFieldTypeString", "length": 10, "nullable": False},
        ],
        "spatial_reference": {"wkid": 4326},
    })
    from app.core.imdf.validator import validate_publish
    report = validate_publish(bundle_task_id="task-robust", connection_id="c",
                              fs_name="MyFS", mode="append")
    layer = _layer(report)
    nn = [i for i in layer["data_issues"] if i.get("code") == "NULL_ON_NON_NULLABLE"]
    assert len(nn) == 1
    assert nn[0]["severity"] == "error"
    assert "CODE" in nn[0]["message"]
    assert "1" in nn[0]["message"]


def test_null_on_non_nullable_warning_in_replace(fake_bundle, monkeypatch):
    """In replace il FS viene ricreato dal bundle → eventuali NOT NULL
    sul FS esistente non contano piu'. Declassare a warning."""
    import pandas as pd
    df = pd.DataFrame({
        "UNIT_ID": ["U-1", "U-2"],
        "CODE":    [None, "B"],
        "geometry": [None, None],
    })
    _install_stubs(monkeypatch, _FakeGDF(df), {
        "fields": [
            {"name": "UNIT_ID", "field_type": "esriFieldTypeString", "length": 50, "nullable": True},
            {"name": "CODE",    "field_type": "esriFieldTypeString", "length": 10, "nullable": False},
        ],
        "spatial_reference": {"wkid": 4326},
    })
    from app.core.imdf.validator import validate_publish
    report = validate_publish(bundle_task_id="task-robust", connection_id="c",
                              fs_name="MyFS", mode="replace")
    layer = _layer(report)
    nn = [i for i in layer["data_issues"] if i.get("code") == "NULL_ON_NON_NULLABLE"]
    assert len(nn) == 1
    assert nn[0]["severity"] == "warning"


# ── 3. CRS_MISMATCH ──────────────────────────────────────────────────
def test_crs_mismatch_warning(fake_bundle, monkeypatch):
    import pandas as pd
    df = pd.DataFrame({"UNIT_ID": ["U-1"], "geometry": [None]})
    gdf = _FakeGDF(df, crs_epsg=4326)
    _install_stubs(monkeypatch, gdf, {
        "fields": [
            {"name": "UNIT_ID", "field_type": "esriFieldTypeString", "length": 50, "nullable": True},
        ],
        "spatial_reference": {"wkid": 3857},  # diverso
    })
    from app.core.imdf.validator import validate_publish
    report = validate_publish(bundle_task_id="task-robust", connection_id="c",
                              fs_name="MyFS", mode="append")
    codes = [i.get("code") for i in report.get("top_issues", [])]
    assert "CRS_MISMATCH" in codes
    msg = next(i["message"] for i in report["top_issues"] if i["code"] == "CRS_MISMATCH")
    assert "4326" in msg and "3857" in msg


def test_crs_match_no_warning(fake_bundle, monkeypatch):
    import pandas as pd
    df = pd.DataFrame({"UNIT_ID": ["U-1"], "geometry": [None]})
    gdf = _FakeGDF(df, crs_epsg=4326)
    _install_stubs(monkeypatch, gdf, {
        "fields": [
            {"name": "UNIT_ID", "field_type": "esriFieldTypeString", "length": 50, "nullable": True},
        ],
        "spatial_reference": {"wkid": 4326},
    })
    from app.core.imdf.validator import validate_publish
    report = validate_publish(bundle_task_id="task-robust", connection_id="c",
                              fs_name="MyFS", mode="append")
    codes = [i.get("code") for i in report.get("top_issues", [])]
    assert "CRS_MISMATCH" not in codes


# ── 4. MISSING_CAPABILITIES ──────────────────────────────────────────
def test_missing_capabilities_error_on_append_without_create(fake_bundle, monkeypatch):
    import pandas as pd
    df = pd.DataFrame({"UNIT_ID": ["U-1"], "geometry": [None]})
    _install_stubs(monkeypatch, _FakeGDF(df), {
        "fields": [{"name": "UNIT_ID", "field_type": "esriFieldTypeString", "length": 50, "nullable": True}],
        "spatial_reference": {"wkid": 4326},
    }, capabilities="Query")  # solo Query → no Create

    from app.core.imdf.validator import validate_publish
    report = validate_publish(bundle_task_id="task-robust", connection_id="c",
                              fs_name="MyFS", mode="append")
    codes = [i.get("code") for i in report.get("top_issues", [])]
    assert "MISSING_CAPABILITIES" in codes
    issue = next(i for i in report["top_issues"] if i["code"] == "MISSING_CAPABILITIES")
    assert issue["severity"] == "error"
    assert "create" in issue["message"].lower()


def test_capabilities_ok_no_error(fake_bundle, monkeypatch):
    import pandas as pd
    df = pd.DataFrame({"UNIT_ID": ["U-1"], "geometry": [None]})
    _install_stubs(monkeypatch, _FakeGDF(df), {
        "fields": [{"name": "UNIT_ID", "field_type": "esriFieldTypeString", "length": 50, "nullable": True}],
        "spatial_reference": {"wkid": 4326},
    }, capabilities="Query,Create,Update,Delete")

    from app.core.imdf.validator import validate_publish
    report = validate_publish(bundle_task_id="task-robust", connection_id="c",
                              fs_name="MyFS", mode="upsert")
    codes = [i.get("code") for i in report.get("top_issues", [])]
    assert "MISSING_CAPABILITIES" not in codes


# ── 5. Case-insensitive field matching ───────────────────────────────
def test_field_name_case_insensitive_no_false_positives(fake_bundle, monkeypatch):
    """Il bundle ha 'Site_Id', il FS ha 'SITE_ID'. ArcGIS è case-insensitive
    sui nomi campo, quindi NON deve generare FIELD_MISSING_*."""
    import pandas as pd
    df = pd.DataFrame({
        "Site_Id": ["S-1"],
        "geometry": [None],
    })
    _install_stubs(monkeypatch, _FakeGDF(df), {
        "fields": [
            {"name": "SITE_ID", "field_type": "esriFieldTypeString", "length": 50, "nullable": True},
        ],
        "spatial_reference": {"wkid": 4326},
    })
    from app.core.imdf.validator import validate_publish
    report = validate_publish(bundle_task_id="task-robust", connection_id="c",
                              fs_name="MyFS", mode="append")
    layer = _layer(report)
    miss_fs = [i for i in layer["schema_issues"] if i.get("code") == "FIELD_MISSING_ON_FS"]
    miss_bundle = [i for i in layer["schema_issues"] if i.get("code") == "FIELD_MISSING_ON_BUNDLE"]
    assert miss_fs == [], f"Atteso nessun FIELD_MISSING_ON_FS, trovato: {miss_fs}"
    assert miss_bundle == [], f"Atteso nessun FIELD_MISSING_ON_BUNDLE, trovato: {miss_bundle}"
