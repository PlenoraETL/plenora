"""Contract test BE-IMDF-007 — coverage delle categorie di issue
emesse da `core/imdf/validator.validate_publish`.

Obiettivo (audit BE-IMDF-BOUNDARY-001 §4): prima di splittare la
funzione 700+ LOC in 4 fasi (bundle_load / fs_inspect / schema_compat
/ assemble), ogni codice di issue deve avere un test isolato che
asserisce comportamento e shape report.

Categorie coperte qui:

- TYPE_MISMATCH       (schema_issues, bundle vs FS field type diff)
- NULL_MATCH_KEY      (data_issues, NULL su business key)
- DUPLICATE_MATCH_KEY (data_issues, duplicati su business key)
- STRING_TRUNCATION   (data_issues, bundle valore > FS length)
- NULL_ON_NON_NULLABLE (data_issues, NULL su campo NOT NULL FS)
- MISSING_CAPABILITIES (top_issues, FS senza capability per mode)
- FOLDER_MISMATCH     (top_issues, request folder != FS folder)
- CRS_MISMATCH        (top_issues, bundle SRS != FS SRS)

Strategia mocking (no rete, no ArcGIS reale):
- bundle dir reale in tmp_path con un dir `*.gdb` vuoto (il validator
  lo trova via `bundle_dir.rglob("*.gdb")`);
- `load_imdf_layers` patchato a livello module-source per ritornare
  GeoDataFrame craftate per il caso;
- `FeaturesService.get_connection` + `_to_connection_info` patchati
  con stub minimi;
- `arcgis_client.create_client` patchato per ritornare un MagicMock con
  `list_layers` / `get_layer_schema` craftati;
- `_find_existing_service` patchato per controllare presenza/folder FS;
- `_count_features_where_via_post` patchato per evitare HTTP veri.

NB: il validator fa lazy import dei collaboratori dentro la funzione,
quindi tutti i patch sono sui SOURCE MODULE (es.
`app.core.imdf.loader.load_imdf_layers`, NON
`app.core.imdf.validator.load_imdf_layers`).

Report shape: oggi `validate_publish(...) -> Dict[str, Any]` opaco.
Vedi sezione "Future typed contract" nel report BE-IMDF-007 per la
proposta `ValidatePublishReport` Pydantic.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

# Pre-import che il validator e i collaborator usano a runtime, cosi'
# eventuali side-effect di import sono noti prima dei monkeypatch.
import app.core.imdf.loader  # noqa: F401
import app.core.imdf.arcgis_uploader  # noqa: F401
import app.core.features.features_service  # noqa: F401
import app.core.features.arcgis_client  # noqa: F401
import app.core.imdf.validator as validator_mod


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixtures: bundle dir + settings + collaborator stubs


@pytest.fixture
def imdf_bundle_dir(tmp_path, monkeypatch):
    """Crea una bundle dir reale con un `.gdb` subdir vuoto e patcha
    `settings.IMDF_BUNDLES_DIR` perche' punti a tmp_path."""
    root = tmp_path / "imdf_bundles"
    root.mkdir()
    bundle_id = "task-test-001"
    bundle_dir = root / bundle_id
    bundle_dir.mkdir()
    (bundle_dir / "fake.gdb").mkdir()  # validator richiede un *.gdb dir

    fake_settings = SimpleNamespace(
        IMDF_BUNDLES_DIR=root,
        IMDF_BUNDLE_TTL_HOURS=24,
    )
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "settings", fake_settings)
    return bundle_id


@pytest.fixture
def patch_collaborators(monkeypatch):
    """Patcha tutti i collaboratori del validator a livello SOURCE
    MODULE (i lazy import nel validator risolvono per nome).

    Ritorna una `Knobs` namespace per personalizzare per-test.
    """
    knobs = SimpleNamespace(
        # Layer dict {name: GeoDataFrame|DataFrame}
        bundle_layers={},
        # FS state
        fs_url=None,
        fs_item_id=None,
        fs_folder=None,
        # Layer info ritornata da client.list_layers
        list_layers_return=None,
        # {layer_name: {fields:[...], capabilities:str, spatial_reference:{wkid|latestWkid}}}
        layer_schema_return={},
        # POST /query returnCountOnly
        count_features_return=0,
        # Folders: list[{id, title}]
        user_folders=[],
        # Service availability check
        service_available=True,
    )

    # FeaturesService stub
    fake_conn_pyd = SimpleNamespace(id="conn-1", server_url="https://arc.test")
    fake_conn_info = SimpleNamespace(server_url="https://arc.test", username="u")

    class FakeFeaturesService:
        def get_connection(self, conn_id):
            return fake_conn_pyd

        def _to_connection_info(self, conn):
            return fake_conn_info

    monkeypatch.setattr(
        "app.core.features.features_service.FeaturesService",
        FakeFeaturesService,
    )

    # arcgis_client.create_client stub
    fake_client = MagicMock(name="ArcGISClient")
    fake_client.list_layers.side_effect = (
        lambda conn, url: knobs.list_layers_return or []
    )
    fake_client.get_layer_schema.side_effect = (
        lambda conn, url: knobs.layer_schema_return.get(_layer_name_from_url(url), {})
    )
    monkeypatch.setattr(
        "app.core.features.arcgis_client.create_client",
        lambda *a, **kw: fake_client,
    )

    # loader.load_imdf_layers stub
    monkeypatch.setattr(
        "app.core.imdf.loader.load_imdf_layers",
        lambda gdb_path, **_kw: knobs.bundle_layers,
    )

    # arcgis_uploader helpers (private, lazy-imported by validator)
    monkeypatch.setattr(
        "app.core.imdf.arcgis_uploader._find_existing_service",
        lambda client, conn, fs_name: (
            knobs.fs_url, knobs.fs_item_id, knobs.fs_folder,
        ),
    )
    monkeypatch.setattr(
        "app.core.imdf.arcgis_uploader._is_service_name_available",
        lambda client, conn, fs_name: (knobs.service_available, None),
    )
    monkeypatch.setattr(
        "app.core.imdf.arcgis_uploader._list_user_folders",
        lambda client, conn: knobs.user_folders,
    )

    # _count_features_where_via_post (private, definito in validator)
    monkeypatch.setattr(
        validator_mod,
        "_count_features_where_via_post",
        lambda client, conn, layer_url, where: knobs.count_features_return,
    )

    return knobs


def _layer_name_from_url(url: str) -> str:
    """Convenzione del fixture: layer URL = `https://arc.test/<LayerName>`.
    Estrae `<LayerName>` per il dispatch di `get_layer_schema`."""
    if not url:
        return ""
    return url.rstrip("/").rsplit("/", 1)[-1]


# Helpers per costruire i layer dict


def _make_layer_info(name: str, layer_id: int = 0, feature_count: Optional[int] = 0) -> dict:
    """Shape ritornata da `client.list_layers`: `{id, name, url, feature_count}`."""
    return {
        "id": layer_id,
        "name": name,
        "url": f"https://arc.test/{name}",
        "feature_count": feature_count,
    }


def _make_fs_schema(
    fields: List[Dict[str, Any]],
    capabilities: str = "Query,Create,Update,Delete",
    wkid: Optional[int] = 4326,
) -> dict:
    """Shape ritornata da `client.get_layer_schema`."""
    return {
        "fields": fields,
        "capabilities": capabilities,
        "spatial_reference": {"latestWkid": wkid, "wkid": wkid},
    }


def _make_bundle_gdf(rows: List[Dict[str, Any]], crs_epsg: Optional[int] = 4326):
    """Crea un GeoDataFrame minimale con CRS impostato (`crs.to_epsg()`
    e' letto dal CRS check del validator). Se `crs_epsg=None` ritorna
    un DataFrame senza CRS."""
    import pandas as pd
    if crs_epsg is None:
        return pd.DataFrame(rows)
    import geopandas as gpd
    from shapely.geometry import Point
    geoms = [Point(0, 0) for _ in rows]
    return gpd.GeoDataFrame(rows, geometry=geoms, crs=f"EPSG:{crs_epsg}")


def _find_layer(report: dict, name: str) -> Optional[dict]:
    for layer in report.get("layers", []):
        if layer["name"] == name:
            return layer
    return None


def _find_issue(items: list, code: str) -> Optional[dict]:
    for it in items:
        if it.get("code") == code:
            return it
    return None


def _has_top_issue(report: dict, code: str) -> bool:
    return _find_issue(report.get("top_issues", []), code) is not None


# Sanity: no-op happy-ish path (zero issue su FS senza problemi)


def test_validate_publish_returns_dict_with_canonical_shape(
    imdf_bundle_dir, patch_collaborators,
):
    """Smoke: shape report invariante (sentinel keys presenti)."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([{"SITE_ID": "S1"}]),
    }
    knobs.fs_url = None  # FS non esiste, mode=replace OK

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )

    # Top-level keys (contratto frontend)
    for key in (
        "ok", "fs_exists", "fs_url", "fs_item_id", "fs_folder",
        "requested_folder", "mode", "summary", "layers", "top_issues",
    ):
        assert key in report, f"missing top-level key {key!r}"
    # summary keys
    for key in ("errors", "warnings", "layers_count", "total_features"):
        assert key in report["summary"], f"missing summary.{key}"


# 1. TYPE_MISMATCH (schema_issues)


def test_type_mismatch_emits_warning_in_replace(
    imdf_bundle_dir, patch_collaborators,
):
    """Bundle con `SITE_ID` int, FS con `SITE_ID` string → warning
    TYPE_MISMATCH (replace mode tollera, non blocca)."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": 1, "NAME": "x"},
            {"SITE_ID": 2, "NAME": "y"},
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=10)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
            {"name": "NAME", "field_type": "esriFieldTypeString",
             "length": 200, "nullable": True},
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )

    layer = _find_layer(report, "Localita")
    assert layer is not None
    issue = _find_issue(layer["schema_issues"], "TYPE_MISMATCH")
    assert issue is not None, f"TYPE_MISMATCH non emesso; layer={layer}"
    assert issue["severity"] == "error"  # SITE_ID e' la match column
    # Anche in replace, TYPE_MISMATCH sul match column e' error.


def test_type_mismatch_on_non_match_column_is_warning(
    imdf_bundle_dir, patch_collaborators,
):
    """TYPE_MISMATCH su un campo non-match column in mode=replace e'
    warning (non error)."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S1", "score": 1.5},  # float bundle
            {"SITE_ID": "S2", "score": 2.5},
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=2)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
            {"name": "score", "field_type": "esriFieldTypeString",  # str FS
             "length": 50, "nullable": True},
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )
    layer = _find_layer(report, "Localita")
    issues = [i for i in layer["schema_issues"] if i["code"] == "TYPE_MISMATCH"]
    score_issue = next((i for i in issues if "score" in i["message"]), None)
    assert score_issue is not None
    assert score_issue["severity"] == "warning"


# 2. NULL_MATCH_KEY (data_issues)


def test_null_match_key_warning_in_replace(imdf_bundle_dir, patch_collaborators):
    """Replace tollera NULL su match column come warning."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S1"},
            {"SITE_ID": None},
            {"SITE_ID": "S3"},
        ]),
    }
    knobs.fs_url = None  # nuovo FS in replace

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )
    layer = _find_layer(report, "Localita")
    issue = _find_issue(layer["data_issues"], "NULL_MATCH_KEY")
    assert issue is not None
    assert issue["severity"] == "warning"
    assert "1" in issue["message"]  # 1 NULL


def test_null_match_key_error_in_upsert(imdf_bundle_dir, patch_collaborators):
    """Upsert NON tollera NULL: error."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S1"},
            {"SITE_ID": None},
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=10)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="upsert",
    )
    layer = _find_layer(report, "Localita")
    issue = _find_issue(layer["data_issues"], "NULL_MATCH_KEY")
    assert issue is not None
    assert issue["severity"] == "error"


# 3. DUPLICATE_MATCH_KEY (data_issues)


def test_duplicate_match_key_warning_in_upsert(imdf_bundle_dir, patch_collaborators):
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S1"},
            {"SITE_ID": "S1"},  # duplicato
            {"SITE_ID": "S2"},
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=5)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="upsert",
    )
    layer = _find_layer(report, "Localita")
    issue = _find_issue(layer["data_issues"], "DUPLICATE_MATCH_KEY")
    assert issue is not None
    assert issue["severity"] == "warning"


def test_duplicate_match_key_error_in_update(imdf_bundle_dir, patch_collaborators):
    """Update strict: duplicati su match key → error."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S1"},
            {"SITE_ID": "S1"},  # duplicato
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=2)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(
            fields=[
                {"name": "SITE_ID", "field_type": "esriFieldTypeString",
                 "length": 50, "nullable": True},
            ],
            # update richiede capability "update"
            capabilities="Query,Update",
        ),
    }
    knobs.count_features_return = 2  # match exists

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="update",
    )
    layer = _find_layer(report, "Localita")
    issue = _find_issue(layer["data_issues"], "DUPLICATE_MATCH_KEY")
    assert issue is not None
    assert issue["severity"] == "error"


# 4. STRING_TRUNCATION (data_issues)


def test_string_truncation_warning_for_normal_field(
    imdf_bundle_dir, patch_collaborators,
):
    """Bundle ha valori piu' lunghi del FS length su un campo
    non-match → warning STRING_TRUNCATION."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S1", "DESCR": "x" * 200},
            {"SITE_ID": "S2", "DESCR": "y" * 50},
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=2)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
            {"name": "DESCR", "field_type": "esriFieldTypeString",
             "length": 100, "nullable": True},  # bundle ha 200 > 100
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )
    layer = _find_layer(report, "Localita")
    issue = _find_issue(layer["data_issues"], "STRING_TRUNCATION")
    assert issue is not None
    assert issue["severity"] == "warning"
    assert "DESCR" in issue["message"]


def test_string_truncation_error_for_match_column(
    imdf_bundle_dir, patch_collaborators,
):
    """Truncation su match column → error (rischio key mismatch)."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S" + ("X" * 100)},  # 101 chars
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=1)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )
    layer = _find_layer(report, "Localita")
    issue = _find_issue(layer["data_issues"], "STRING_TRUNCATION")
    assert issue is not None
    assert issue["severity"] == "error"  # match column


# 5. NULL_ON_NON_NULLABLE (data_issues)


def test_null_on_non_nullable_warning_in_replace(
    imdf_bundle_dir, patch_collaborators,
):
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S1", "REQUIRED": "v"},
            {"SITE_ID": "S2", "REQUIRED": None},
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=2)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
            {"name": "REQUIRED", "field_type": "esriFieldTypeString",
             "length": 100, "nullable": False},  # NOT NULL
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )
    layer = _find_layer(report, "Localita")
    issue = _find_issue(layer["data_issues"], "NULL_ON_NON_NULLABLE")
    assert issue is not None
    assert issue["severity"] == "warning"


def test_null_on_non_nullable_error_in_append(
    imdf_bundle_dir, patch_collaborators,
):
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S1", "REQUIRED": None},
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=2)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(
            fields=[
                {"name": "SITE_ID", "field_type": "esriFieldTypeString",
                 "length": 50, "nullable": True},
                {"name": "REQUIRED", "field_type": "esriFieldTypeString",
                 "length": 100, "nullable": False},
            ],
            capabilities="Query,Create",  # append richiede 'create'
        ),
    }
    knobs.count_features_return = 0

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="append",
    )
    layer = _find_layer(report, "Localita")
    issue = _find_issue(layer["data_issues"], "NULL_ON_NON_NULLABLE")
    assert issue is not None
    assert issue["severity"] == "error"


# 6. MISSING_CAPABILITIES (top_issues)


def test_missing_capabilities_for_append(imdf_bundle_dir, patch_collaborators):
    """Append richiede capability 'create'. Senza → error
    MISSING_CAPABILITIES."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([{"SITE_ID": "S1"}]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=10)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(
            fields=[
                {"name": "SITE_ID", "field_type": "esriFieldTypeString",
                 "length": 50, "nullable": True},
            ],
            capabilities="Query",  # NO Create
        ),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="append",
    )
    issue = _find_issue(report["top_issues"], "MISSING_CAPABILITIES")
    assert issue is not None
    assert issue["severity"] == "error"
    assert "create" in issue["message"].lower()


def test_missing_capabilities_for_upsert(imdf_bundle_dir, patch_collaborators):
    """Upsert richiede 'create' E 'delete'."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([{"SITE_ID": "S1"}]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=10)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(
            fields=[
                {"name": "SITE_ID", "field_type": "esriFieldTypeString",
                 "length": 50, "nullable": True},
            ],
            capabilities="Query,Create",  # missing delete
        ),
    }
    knobs.count_features_return = 1

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="upsert",
    )
    issue = _find_issue(report["top_issues"], "MISSING_CAPABILITIES")
    assert issue is not None
    assert issue["severity"] == "error"
    assert "delete" in issue["message"].lower()


# 7. FOLDER_MISMATCH (top_issues)


def test_folder_mismatch_warning_when_request_differs(
    imdf_bundle_dir, patch_collaborators,
):
    """User chiede folder='target_folder', FS esistente vive in
    'other_folder' → warning FOLDER_MISMATCH."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([{"SITE_ID": "S1"}]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.fs_folder = "id-other"  # folder id sul FS esistente
    knobs.user_folders = [
        {"id": "id-other", "title": "other_folder"},
        {"id": "id-target", "title": "target_folder"},
    ]
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=1)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
        folder="target_folder",
    )
    issue = _find_issue(report["top_issues"], "FOLDER_MISMATCH")
    assert issue is not None
    assert issue["severity"] == "warning"


def test_folder_match_no_warning(imdf_bundle_dir, patch_collaborators):
    """Folder coincide (via title resolve) → nessun FOLDER_MISMATCH."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([{"SITE_ID": "S1"}]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.fs_folder = "id-target"
    knobs.user_folders = [
        {"id": "id-target", "title": "target_folder"},
    ]
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=1)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
        folder="target_folder",
    )
    assert not _has_top_issue(report, "FOLDER_MISMATCH")


# 8. CRS_MISMATCH (top_issues)


def test_crs_mismatch_warning_when_bundle_and_fs_differ(
    imdf_bundle_dir, patch_collaborators,
):
    """Bundle EPSG:4326, FS EPSG:3857 → warning CRS_MISMATCH."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf(
            [{"SITE_ID": "S1"}],
            crs_epsg=4326,
        ),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=1)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(
            fields=[
                {"name": "SITE_ID", "field_type": "esriFieldTypeString",
                 "length": 50, "nullable": True},
            ],
            wkid=3857,  # different SRS
        ),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )
    issue = _find_issue(report["top_issues"], "CRS_MISMATCH")
    assert issue is not None
    assert issue["severity"] == "warning"
    assert "4326" in issue["message"]
    assert "3857" in issue["message"]


def test_crs_match_no_warning(imdf_bundle_dir, patch_collaborators):
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf(
            [{"SITE_ID": "S1"}],
            crs_epsg=4326,
        ),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=1)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(
            fields=[
                {"name": "SITE_ID", "field_type": "esriFieldTypeString",
                 "length": 50, "nullable": True},
            ],
            wkid=4326,
        ),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )
    assert not _has_top_issue(report, "CRS_MISMATCH")


# Sentinel: report-level invariants comuni a tutti i test


def test_summary_counters_match_severities(
    imdf_bundle_dir, patch_collaborators,
):
    """`summary.errors`/`warnings` devono coincidere col conteggio reale
    di severity nei top_issues + per-layer issues. Contratto stabile su
    cui si potra' costruire `ValidatePublishReport` Pydantic."""
    knobs = patch_collaborators
    knobs.bundle_layers = {
        "Localita": _make_bundle_gdf([
            {"SITE_ID": "S1", "DESCR": "x" * 200},
            {"SITE_ID": None, "DESCR": "y"},  # NULL match key
        ]),
    }
    knobs.fs_url = "https://arc.test"
    knobs.list_layers_return = [_make_layer_info("Localita", feature_count=10)]
    knobs.layer_schema_return = {
        "Localita": _make_fs_schema(fields=[
            {"name": "SITE_ID", "field_type": "esriFieldTypeString",
             "length": 50, "nullable": True},
            {"name": "DESCR", "field_type": "esriFieldTypeString",
             "length": 100, "nullable": True},
        ]),
    }

    report = validator_mod.validate_publish(
        bundle_task_id=imdf_bundle_dir,
        connection_id="conn-1",
        fs_name="LO_TEST",
        mode="replace",
    )

    # Conta severity manuali su top_issues + layer issues
    expected_errors = sum(
        1 for it in report["top_issues"] if it["severity"] == "error"
    )
    expected_warnings = sum(
        1 for it in report["top_issues"] if it["severity"] == "warning"
    )
    for layer in report["layers"]:
        for col in ("schema_issues", "data_issues"):
            for it in layer.get(col, []):
                if it["severity"] == "error":
                    expected_errors += 1
                elif it["severity"] == "warning":
                    expected_warnings += 1

    assert report["summary"]["errors"] == expected_errors
    assert report["summary"]["warnings"] == expected_warnings
    # `ok` riflette presenza di errori
    assert report["ok"] == (expected_errors == 0)
