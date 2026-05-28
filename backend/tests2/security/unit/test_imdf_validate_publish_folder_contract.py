"""
test_imdf_validate_publish_folder_contract.py

Contract test per il supporto del parametro `folder` in validate_publish.
"""
from contextlib import contextmanager

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_imdf_validate_request_accepts_optional_folder():
    from app.core.imdf.router import ImdfValidateRequest

    # Senza folder → default None
    r = ImdfValidateRequest(
        bundle_task_id="task-1",
        connection_id="conn-1",
        fs_name="MyFS",
    )
    assert getattr(r, "folder", None) is None

    # Con folder
    r = ImdfValidateRequest(
        bundle_task_id="task-1",
        connection_id="conn-1",
        fs_name="MyFS",
        folder="MyFolder",
    )
    assert r.folder == "MyFolder"


class _FakeGDF:
    """Mock minimo di un GeoDataFrame per far passare il per-layer loop in
    mode=replace (non serve columns, solo __len__)."""
    def __init__(self, n: int):
        self._n = n
        self.columns = []

    def __len__(self):
        return self._n


class _FakeConnInfo:
    username = "tester"
    server_url = "https://example.com"


@pytest.fixture
def fake_bundle(tmp_path, monkeypatch):
    """Crea una cartella bundle finta con .gdb e patcha IMDF_BUNDLES_DIR
    sostituendo l'intero oggetto settings in app.config (IMDF_BUNDLES_DIR
    nell'oggetto reale è una @property read-only)."""
    bundle_dir = tmp_path / "task-abc"
    bundle_dir.mkdir()
    (bundle_dir / "fake.gdb").mkdir()

    from types import SimpleNamespace
    import app.config as config_mod
    fake_settings = SimpleNamespace(
        IMDF_BUNDLES_DIR=tmp_path,
        IMDF_BUNDLE_TTL_HOURS=24,
    )
    monkeypatch.setattr(config_mod, "settings", fake_settings)
    import app.core.imdf.storage as storage_mod

    monkeypatch.setattr(
        storage_mod,
        "get_meta_only",
        lambda task_id: {"site_id": "fake", "project_id": "proj-1"}
        if task_id == "task-abc"
        else None,
    )

    @contextmanager
    def _fake_vsimem(task_id, site_id):
        yield str(bundle_dir / "fake.gdb")

    monkeypatch.setattr(storage_mod, "open_gdb_in_vsimem", _fake_vsimem)
    return bundle_dir


@pytest.fixture
def stub_externals(monkeypatch):
    """Monkeypatcha tutte le dipendenze esterne di validate_publish:
    loader, resolve_arcgis_connection, _find_existing_service,
    _is_service_name_available."""
    import app.core.imdf.loader as loader_mod
    import app.core.connections.arcgis_connection_resolver as adapter_mod
    import app.core.imdf.arcgis.service_discovery as service_discovery_mod

    # Layer finti (solo Locale per avere un layer presente)
    monkeypatch.setattr(
        loader_mod, "load_imdf_layers",
        lambda gdb: {"Locale": _FakeGDF(3)},
    )

    class _FakeArcgisClient:
        def list_layers(self, conn, url):
            return []

        def get_layer_schema(self, conn, url):
            return {"fields": []}

    fake_client = _FakeArcgisClient()
    monkeypatch.setattr(
        adapter_mod,
        "resolve_arcgis_connection",
        lambda connection_id: (_FakeConnInfo(), fake_client),
    )

    # Default: nessun service esistente (override nei test che servono)
    monkeypatch.setattr(
        service_discovery_mod, "_find_existing_service",
        lambda *args, **kwargs: (None, None, None),
    )
    monkeypatch.setattr(
        service_discovery_mod, "_is_service_name_available",
        lambda client, conn, fs_name: (True, None),
    )

    return {"service_discovery": service_discovery_mod}


@pytest.mark.usefixtures("stub_externals")
def test_validate_publish_without_folder_has_no_folder_mismatch(fake_bundle):
    """Comportamento baseline: senza folder nel request, mai warning
    FOLDER_MISMATCH nel report (retrocompat)."""
    from app.core.imdf.validator import validate_publish

    report = validate_publish(
        bundle_task_id="task-abc",
        connection_id="conn-1",
        fs_name="MyFS",
        mode="replace",
    )

    codes = [i.get("code") for i in report.get("top_issues", [])]
    assert "FOLDER_MISMATCH" not in codes


@pytest.mark.usefixtures("stub_externals")
def test_validate_publish_folder_no_existing_fs_no_mismatch(fake_bundle):
    from app.core.imdf.validator import validate_publish

    report = validate_publish(
        bundle_task_id="task-abc",
        connection_id="conn-1",
        fs_name="MyFS",
        mode="replace",
        folder="MyFolder",
    )
    codes = [i.get("code") for i in report.get("top_issues", [])]
    assert "FOLDER_MISMATCH" not in codes


@pytest.mark.usefixtures("stub_externals")
def test_validate_publish_folder_matches_existing_no_warning(fake_bundle, monkeypatch):
    import app.core.imdf.arcgis.service_discovery as service_discovery_mod

    # FS esistente con ownerFolder == "MyFolder"
    monkeypatch.setattr(
        service_discovery_mod, "_find_existing_service",
        lambda *args, **kwargs: ("https://example.com/services/MyFS/FeatureServer", "item-1", "MyFolder"),
    )

    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-abc",
        connection_id="conn-1",
        fs_name="MyFS",
        mode="append",
        folder="MyFolder",
    )
    codes = [i.get("code") for i in report.get("top_issues", [])]
    assert "FOLDER_MISMATCH" not in codes
    assert report["fs_exists"] is True


@pytest.mark.usefixtures("stub_externals")
def test_validate_publish_folder_mismatch_emits_warning(fake_bundle, monkeypatch):
    import app.core.imdf.arcgis.service_discovery as service_discovery_mod

    # FS esistente nella folder "OtherFolder", utente chiede "MyFolder"
    monkeypatch.setattr(
        service_discovery_mod, "_find_existing_service",
        lambda *args, **kwargs: ("https://example.com/services/MyFS/FeatureServer", "item-1", "OtherFolder"),
    )

    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-abc",
        connection_id="conn-1",
        fs_name="MyFS",
        mode="append",
        folder="MyFolder",
    )

    codes = [i.get("code") for i in report.get("top_issues", [])]
    assert "FOLDER_MISMATCH" in codes, (
        f"Atteso FOLDER_MISMATCH in top_issues, trovato: {codes}"
    )
    # Severità warning, non error: il publish funzionerà comunque
    folder_issues = [i for i in report["top_issues"] if i.get("code") == "FOLDER_MISMATCH"]
    assert folder_issues[0].get("severity") == "warning"


@pytest.mark.usefixtures("stub_externals")
def test_validate_publish_folder_id_resolves_to_title_no_warning(fake_bundle, monkeypatch):
    """L'API ArcGIS restituisce ownerFolder come folder ID (hash), mentre
    l'utente sceglie una folder per TITLE. Il validator deve risolvere
    l'ID → title via _list_user_folders prima di confrontare. Se la folder
    con ID='hash-abc' ha title='MyFolder' e l'utente ha scelto 'MyFolder',
    NON deve esserci warning."""
    import app.core.imdf.arcgis.service_discovery as service_discovery_mod

    monkeypatch.setattr(
        service_discovery_mod, "_find_existing_service",
        lambda *args, **kwargs: ("https://example.com/services/MyFS/FeatureServer", "item-1", "hash-abc"),
    )
    # _list_user_folders ritorna la lista con id/title
    monkeypatch.setattr(
        service_discovery_mod, "_list_user_folders",
        lambda client, conn: [{"id": "hash-abc", "title": "MyFolder"}],
        raising=False,
    )

    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-abc",
        connection_id="conn-1",
        fs_name="MyFS",
        mode="append",
        folder="MyFolder",
    )
    codes = [i.get("code") for i in report.get("top_issues", [])]
    assert "FOLDER_MISMATCH" not in codes, (
        f"Con id→title resolution non deve esserci mismatch. top_issues: {report.get('top_issues')}"
    )


@pytest.mark.usefixtures("stub_externals")
def test_validate_publish_folder_id_mismatch_uses_title_in_message(fake_bundle, monkeypatch):
    """Quando il mismatch è reale, il messaggio di warning deve mostrare
    il title risolto, non l'ID hash."""
    import app.core.imdf.arcgis.service_discovery as service_discovery_mod

    monkeypatch.setattr(
        service_discovery_mod, "_find_existing_service",
        lambda *args, **kwargs: ("https://example.com/services/MyFS/FeatureServer", "item-1", "hash-abc"),
    )
    monkeypatch.setattr(
        service_discovery_mod, "_list_user_folders",
        lambda client, conn: [
            {"id": "hash-abc", "title": "RealOwnerFolder"},
            {"id": "hash-xyz", "title": "UserSelected"},
        ],
        raising=False,
    )

    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-abc",
        connection_id="conn-1",
        fs_name="MyFS",
        mode="replace",
        folder="UserSelected",
    )
    mm = [i for i in report["top_issues"] if i.get("code") == "FOLDER_MISMATCH"]
    assert len(mm) == 1, f"Atteso 1 FOLDER_MISMATCH, trovato: {report['top_issues']}"
    msg = mm[0]["message"]
    assert "RealOwnerFolder" in msg, f"Messaggio deve contenere il title risolto, non l'id. msg={msg}"
    assert "hash-abc" not in msg, f"Messaggio NON deve contenere l'id raw. msg={msg}"


@pytest.mark.usefixtures("stub_externals")
def test_validate_publish_folder_mismatch_replace_still_ok(fake_bundle, monkeypatch):
    """Anche con folder diverso, replace resta 'ok' (è un warning, non error)."""
    import app.core.imdf.arcgis.service_discovery as service_discovery_mod

    monkeypatch.setattr(
        service_discovery_mod, "_find_existing_service",
        lambda *args, **kwargs: ("https://example.com/services/MyFS/FeatureServer", "item-1", "OtherFolder"),
    )

    from app.core.imdf.validator import validate_publish
    report = validate_publish(
        bundle_task_id="task-abc",
        connection_id="conn-1",
        fs_name="MyFS",
        mode="replace",
        folder="MyFolder",
    )

    assert report["summary"]["errors"] == 0
    assert report["ok"] is True
    assert report["summary"]["warnings"] >= 1
