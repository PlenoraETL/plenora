"""
test_imdf_validate_publish_schema_contract.py

Contract test per la schema-compat logic di validate_publish:
"""
import pytest

# Pre-importa i moduli che leggono `settings` al top-level. Se lasciamo
# che siano importati dopo il monkeypatch di settings (SimpleNamespace),
# l'init-time read fallisce con AttributeError (es. LOG_DB_FILE).
import app.utils.logger  # noqa: F401
import app.core.features.features_service  # noqa: F401
import app.core.features.arcgis_client  # noqa: F401
import app.core.imdf.loader  # noqa: F401
import app.core.imdf.arcgis_uploader  # noqa: F401
import app.core.imdf.validator  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


class _FakeSeries:
    def __init__(self, dtype_str):
        self.dtype = dtype_str
    def isna(self):
        return self
    def sum(self):
        return 0
    def dropna(self):
        return self
    def tolist(self):
        return []
    def duplicated(self, keep=False):
        return self
    def unique(self):
        return self
    def __len__(self):
        return 0
    def __iter__(self):
        return iter([])


class _FakeGDFWithCols:
    """Mock GeoDataFrame con columns dichiarate + dtype mock."""
    def __init__(self, columns_types: dict, n: int = 3):
        self._n = n
        self._types = columns_types
        self.columns = list(columns_types.keys())

    def __len__(self):
        return self._n

    def __contains__(self, col):
        return col in self._types

    def __getitem__(self, col):
        return _FakeSeries(self._types.get(col, "object"))


class _FakeConnInfo:
    username = "tester"
    server_url = "https://example.com"


@pytest.fixture
def fake_bundle(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "task-xyz"
    bundle_dir.mkdir()
    (bundle_dir / "fake.gdb").mkdir()

    from types import SimpleNamespace
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "settings", SimpleNamespace(
        IMDF_BUNDLES_DIR=tmp_path,
        IMDF_BUNDLE_TTL_HOURS=24,
    ))
    return bundle_dir


@pytest.fixture
def stub_externals_with_fs_schema(monkeypatch):
    """Stub con FS esistente + schema esteso per testare la logica
    schema-compat."""
    import app.core.imdf.loader as loader_mod
    import app.core.connections.arcgis_connection_resolver as adapter_mod
    import app.core.imdf.arcgis_uploader as uploader_mod

    # Bundle con un layer 'Locale' che ha alcuni campi
    fake_gdf = _FakeGDFWithCols({
        "SITE_ID":  "object",   # string
        "QUOTA":    "float64",  # float
        "COMUNE":   "object",   # string
        "geometry": "geometry",
    })
    monkeypatch.setattr(
        loader_mod, "load_imdf_layers",
        lambda gdb: {"Locale": fake_gdf},
    )

    class _FakeArcgisClient:
        def list_layers(self, conn, url):
            return [{"name": "Locale", "url": "https://x/Locale", "id": 0,
                     "feature_count": 10}]

        def get_layer_schema(self, conn, url):
            # Simula lo schema ritornato dall'FS:
            #  - SITE_ID è esriFieldTypeString (match)
            #  - QUOTA è esriFieldTypeDouble (bundle=float, FS=float → match)
            #  - COMUNE è type che _coarse_type_from_esri non riconosce → 'other'
            #  - Shape__Area, Shape__Length (SYS, da ignorare)
            #  - OBJECTID (SYS)
            return {
                "fields": [
                    {"name": "SITE_ID",      "type": "esriFieldTypeString"},
                    {"name": "QUOTA",        "type": "esriFieldTypeCustomUnknown"},
                    {"name": "COMUNE",       "type": "esriFieldTypeCustomUnknown"},
                    {"name": "Shape__Area",  "type": "esriFieldTypeDouble"},
                    {"name": "Shape__Length","type": "esriFieldTypeDouble"},
                    {"name": "OBJECTID",     "type": "esriFieldTypeOID"},
                ],
            }

    fake_client = _FakeArcgisClient()
    monkeypatch.setattr(
        adapter_mod,
        "resolve_arcgis_connection",
        lambda connection_id: (_FakeConnInfo(), fake_client),
    )

    monkeypatch.setattr(
        uploader_mod, "_find_existing_service",
        lambda *args, **kwargs: (
            "https://example.com/services/MyFS/FeatureServer", "item-1", None,
        ),
    )
    monkeypatch.setattr(
        uploader_mod, "_is_service_name_available",
        lambda client, conn, fs_name: (True, None),
    )
    monkeypatch.setattr(
        uploader_mod, "_list_user_folders",
        lambda client, conn: [],
        raising=False,
    )

    return uploader_mod


def _layer_issue_codes(report, layer_name="Locale"):
    for l in report.get("layers", []):
        if l.get("name") == layer_name:
            return [i.get("code") for i in l.get("schema_issues", [])]
    return []


def test_shape_double_underscore_fields_not_reported(fake_bundle, stub_externals_with_fs_schema):
    """Shape__Area / Shape__Length devono essere ignorati come system field
    (oggi sono SYS con singolo underscore e quindi non matchano)."""
    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-xyz",
        connection_id="c",
        fs_name="MyFS",
        mode="append",
    )
    codes = _layer_issue_codes(report)
    # Cerchiamo i messaggi specifici di Shape__*
    layer = next(l for l in report["layers"] if l["name"] == "Locale")
    shape_issues = [
        i for i in layer["schema_issues"]
        if "Shape__" in i.get("message", "")
    ]
    assert shape_issues == [], (
        f"Shape__Area / Shape__Length devono essere filtrati. Trovati: {shape_issues}"
    )


def test_objectid_not_reported_in_schema(fake_bundle, stub_externals_with_fs_schema):
    """OBJECTID è già gestito, non deve apparire."""
    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-xyz",
        connection_id="c",
        fs_name="MyFS",
        mode="append",
    )
    layer = next(l for l in report["layers"] if l["name"] == "Locale")
    oid_issues = [
        i for i in layer["schema_issues"]
        if "OBJECTID" in i.get("message", "")
    ]
    assert oid_issues == [], f"OBJECTID non deve essere segnalato. Trovati: {oid_issues}"


def test_type_mismatch_other_non_match_col_silenced(fake_bundle, stub_externals_with_fs_schema):
    """Quando il FS ritorna un type non classificato ('other') per un campo
    che NON è match_column, il validator NON deve emettere TYPE_MISMATCH
    (rumore senza valore per l'utente)."""
    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-xyz",
        connection_id="c",
        fs_name="MyFS",
        mode="append",
    )
    layer = next(l for l in report["layers"] if l["name"] == "Locale")
    type_mm = [
        i for i in layer["schema_issues"]
        if i.get("code") == "TYPE_MISMATCH" and "QUOTA" in i.get("message", "")
    ]
    assert type_mm == [], (
        f"TYPE_MISMATCH su campo non-match-col con 'other' deve essere silenziato. "
        f"Trovati: {type_mm}"
    )
    type_mm_comune = [
        i for i in layer["schema_issues"]
        if i.get("code") == "TYPE_MISMATCH" and "COMUNE" in i.get("message", "")
    ]
    assert type_mm_comune == [], (
        f"TYPE_MISMATCH su COMUNE ('other') deve essere silenziato. "
        f"Trovati: {type_mm_comune}"
    )


def test_append_detects_duplicate_business_keys(fake_bundle, monkeypatch):
    """Append in ArcGIS non de-duplica: se il bundle contiene chiavi
    business che gia' esistono sul FS, dopo il publish ci sono feature
    duplicate. Il validator deve emettere un warning quantitativo."""
    import app.core.imdf.loader as loader_mod
    import app.core.connections.arcgis_connection_resolver as adapter_mod
    import app.core.imdf.arcgis_uploader as uploader_mod

    # Bundle con colonna UNIT_ID (default match_col per 'Locale') e 3 valori
    class _UnitIdSeries:
        def __init__(self, values):
            self._values = values
            self.dtype = "object"
        def isna(self): return self
        def sum(self): return 0
        def dropna(self): return self
        def unique(self): return self
        def tolist(self): return list(self._values)
        def duplicated(self, keep=False): return self
        def __iter__(self): return iter(self._values)
        def __len__(self): return len(self._values)

    class _GDFAppend:
        def __init__(self):
            self._n = 3
            self._unit_ids = ["U-1", "U-2", "U-3"]
            self.columns = ["UNIT_ID", "geometry"]
        def __len__(self): return self._n
        def __contains__(self, col): return col in self.columns
        def __getitem__(self, col):
            if col == "UNIT_ID":
                return _UnitIdSeries(self._unit_ids)
            return _FakeSeries("geometry")

    monkeypatch.setattr(
        loader_mod, "load_imdf_layers",
        lambda gdb: {"Locale": _GDFAppend()},
    )

    class _FakeArcgisClient:
        def list_layers(self, conn, url):
            return [{"name": "Locale", "url": "https://x/Locale", "id": 0, "feature_count": 100}]
        def get_layer_schema(self, conn, url):
            return {"fields": [
                {"name": "UNIT_ID", "type": "esriFieldTypeString"},
            ]}

    fake_client = _FakeArcgisClient()
    monkeypatch.setattr(
        adapter_mod,
        "resolve_arcgis_connection",
        lambda connection_id: (_FakeConnInfo(), fake_client),
    )
    # Il validator ora usa _count_features_where_via_post (POST /query)
    import app.core.imdf.validator as validator_mod
    monkeypatch.setattr(
        validator_mod, "_count_features_where_via_post",
        lambda client, conn, layer_url, where: 2,  # Simula 2 chiavi esistenti
    )
    monkeypatch.setattr(
        uploader_mod, "_find_existing_service",
        lambda *args, **kwargs: ("https://example.com/svc", "item-1", None),
    )
    monkeypatch.setattr(
        uploader_mod, "_is_service_name_available",
        lambda c, co, fn: (True, None),
    )
    monkeypatch.setattr(
        uploader_mod, "_list_user_folders",
        lambda c, co: [], raising=False,
    )

    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-xyz",
        connection_id="c",
        fs_name="MyFS",
        mode="append",
    )

    layer = next(l for l in report["layers"] if l["name"] == "Locale")
    dup_issues = [
        i for i in layer.get("data_issues", [])
        if i.get("code") == "APPEND_DUPLICATE_KEYS"
    ]
    assert len(dup_issues) == 1, (
        f"Atteso APPEND_DUPLICATE_KEYS. data_issues: {layer.get('data_issues')}"
    )
    msg = dup_issues[0]["message"]
    # Il messaggio deve menzionare il count dei duplicati (2 su 3)
    assert "2" in msg and "3" in msg, (
        f"Il messaggio deve riportare i conteggi. msg={msg}"
    )
    assert dup_issues[0]["severity"] == "warning"


def test_append_no_duplicates_no_warning(fake_bundle, monkeypatch):
    """Se tutte le chiavi bundle sono nuove (nessun overlap con FS),
    append non deve emettere warning duplicati."""
    import app.core.imdf.loader as loader_mod
    import app.core.connections.arcgis_connection_resolver as adapter_mod
    import app.core.imdf.arcgis_uploader as uploader_mod

    class _UnitIdSeries:
        def __init__(self, values):
            self._values = values
            self.dtype = "object"
        def isna(self): return self
        def sum(self): return 0
        def dropna(self): return self
        def unique(self): return self
        def tolist(self): return list(self._values)
        def duplicated(self, keep=False): return self
        def __iter__(self): return iter(self._values)
        def __len__(self): return len(self._values)

    class _GDFFresh:
        def __init__(self):
            self._n = 2
            self._unit_ids = ["NEW-1", "NEW-2"]
            self.columns = ["UNIT_ID", "geometry"]
        def __len__(self): return self._n
        def __contains__(self, col): return col in self.columns
        def __getitem__(self, col):
            if col == "UNIT_ID":
                return _UnitIdSeries(self._unit_ids)
            return _FakeSeries("geometry")

    monkeypatch.setattr(loader_mod, "load_imdf_layers", lambda gdb: {"Locale": _GDFFresh()})

    class _FakeArcgisClient:
        def list_layers(self, conn, url):
            return [{"name": "Locale", "url": "https://x/Locale", "id": 0, "feature_count": 10}]
        def get_layer_schema(self, conn, url):
            return {"fields": [{"name": "UNIT_ID", "type": "esriFieldTypeString"}]}

    fake_client = _FakeArcgisClient()
    monkeypatch.setattr(
        adapter_mod,
        "resolve_arcgis_connection",
        lambda connection_id: (_FakeConnInfo(), fake_client),
    )
    import app.core.imdf.validator as validator_mod
    monkeypatch.setattr(
        validator_mod, "_count_features_where_via_post",
        lambda client, conn, layer_url, where: 0,
    )
    monkeypatch.setattr(
        uploader_mod, "_find_existing_service",
        lambda *args, **kwargs: ("https://example.com/svc", "item-1", None),
    )
    monkeypatch.setattr(
        uploader_mod, "_is_service_name_available",
        lambda c, co, fn: (True, None),
    )
    monkeypatch.setattr(
        uploader_mod, "_list_user_folders",
        lambda c, co: [], raising=False,
    )

    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-xyz",
        connection_id="c",
        fs_name="MyFS",
        mode="append",
    )
    layer = next(l for l in report["layers"] if l["name"] == "Locale")
    dup = [i for i in layer.get("data_issues", []) if i.get("code") == "APPEND_DUPLICATE_KEYS"]
    assert dup == [], f"Nessun duplicato atteso ma trovato: {dup}"


def test_type_mismatch_other_on_match_col_still_error(fake_bundle, stub_externals_with_fs_schema, monkeypatch):
    """Ma se il campo 'other' è proprio la match_column, il rischio è
    concreto (chiave di confronto non classificabile) → resta error."""
    # Uso mode=upsert così `match_column` viene usato; DEFAULT_MATCH_COLUMNS
    # per 'Locale' è 'UNIT_ID'. Forzo un match_columns override verso QUOTA.
    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-xyz",
        connection_id="c",
        fs_name="MyFS",
        mode="upsert",
        match_columns={"Locale": "QUOTA"},
    )
    layer = next(l for l in report["layers"] if l["name"] == "Locale")
    errs = [
        i for i in layer["schema_issues"]
        if i.get("code") == "TYPE_MISMATCH"
        and i.get("severity") == "error"
        and "QUOTA" in i.get("message", "")
    ]
    assert len(errs) >= 1, (
        f"Quando 'other' è sulla match_column, TYPE_MISMATCH deve restare error. "
        f"Trovati: {layer['schema_issues']}"
    )
