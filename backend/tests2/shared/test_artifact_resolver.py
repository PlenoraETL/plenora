"""
Tests the artifact resolver behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.artifacts.resolver import ArtifactResolver
from app.core.artifacts.types import (
    ArtifactResolutionError,
    LatestProjectArtifactRef,
    RunArtifactRef,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def resolver():
    return ArtifactResolver()


# Helpers for the BE-GO-READY-RESOURCE-ENGINE-019 registry boundary tests.
# The resolver's `_materialize_from_result_id` now delegates blob fetch +
# tempfile materialization to `default_registry().get(ref, mode='path')`.
# Tests that previously mocked `db_service.get_result_data` now mock the
# registry's `get(ref, mode)` surface AS IMPORTED INTO the resolver
# module so the patch lands at the canonical boundary.


def _fake_registry_returning_blob(payload: bytes, *, suffix: str = "") -> tuple[list, object]:
    """Build a fake registry whose `get(ref, mode)` materializes
    ``payload`` to a fresh tempfile (mirroring the loader's path-mode
    contract: prefix `plenora-result-blob-`, returns a Resource with
    ``path`` set, ``is_temporary=True``).

    Returns ``(captured_calls, fake_registry)``. The caller asserts
    on the captured `(ref, mode)` tuples to verify the resolver's
    delegation contract; the fake_registry is patched onto
    ``app.core.artifacts.resolver.default_registry``.
    """
    from app.core.resources import Resource

    captured: list = []

    def _get(ref, *, mode):
        captured.append((ref, mode))
        tf = tempfile.NamedTemporaryFile(
            delete=False,
            prefix="plenora-result-blob-",
            suffix=suffix,
        )
        path = Path(tf.name)
        try:
            tf.write(payload)
        finally:
            tf.close()
        return Resource(
            kind="result_blob",
            size_bytes=len(payload),
            path=path,
            is_temporary=True,
        )

    return captured, SimpleNamespace(get=_get)


def _fake_registry_raising(exc: BaseException) -> object:
    """Build a fake registry whose `get(ref, mode)` raises ``exc``.
    Used to exercise the resolver's typed-error translation path."""

    def _get(ref, *, mode):  # noqa: ARG001
        raise exc

    return SimpleNamespace(get=_get)


# materialize_run


def test_materialize_run_inline_df_takes_priority(resolver):
    sentinel = object()
    ref = RunArtifactRef(
        project_id="proj-1",
        inline_df=sentinel,
        result_id="res-1",
        output_path="/tmp/legacy.csv",
    )
    m = resolver.materialize_run(ref)
    assert m.df is sentinel
    assert m.path is None
    assert m.is_temporary is False


def test_materialize_run_result_id_beats_output_path(resolver, monkeypatch):
    """BE-GO-READY-RESOURCE-ENGINE-019: the resolver delegates the
    blob fetch + tempfile materialization to
    ``default_registry().get(ref, mode='path')``. The row metadata
    (format, group_id, project_id) is still fetched via
    ``results_db_service.get_result``; only the BYTES branch was migrated.
    """
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-1",
            "group_id": "grp-1",
            "format": "csv",
        },
    )
    captured, fake_registry = _fake_registry_returning_blob(
        b"id\n1\n", suffix=".csv",
    )
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    ref = RunArtifactRef(
        project_id="proj-1",
        result_id="res-1",
        output_path="C:/tmp/legacy.csv",
    )
    with resolver.materialize_run(ref) as m:
        assert m.path is not None
        assert m.path.exists()
        assert m.path.suffix == ".csv"
        assert m.format == "csv"
        assert m.is_temporary is True
        assert m.path.read_bytes() == b"id\n1\n"
    # cleanup automatico via context manager
    assert not m.path.exists()
    # Delegation contract: exactly one registry call with the
    # canonical typed ResourceRef + path mode.
    assert len(captured) == 1
    ref_passed, mode_passed = captured[0]
    assert ref_passed.kind == "result_blob"
    assert ref_passed.result_id == "res-1"
    assert ref_passed.expected_group_id == "grp-1"
    assert ref_passed.expected_project_id == "proj-1"
    assert mode_passed == "path"


def test_materialize_run_rejects_cross_tenant_result_id(resolver, monkeypatch):
    """The resolver-level project_id check fires BEFORE the registry
    call; the loader is never invoked. Tripwire mock on the
    registry would AssertionError if the resolver delegated past
    the cross-tenant gate."""
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-2",
            "group_id": "grp-1",
            "format": "csv",
        },
    )

    def _tripwire():
        raise AssertionError(
            "default_registry() must NOT be invoked when the "
            "resolver-level cross-tenant check has already failed"
        )

    monkeypatch.setattr(resolver_mod, "default_registry", _tripwire)

    ref = RunArtifactRef(
        project_id="proj-1",
        result_id="res-1",
        output_path="C:/tmp/fallback.csv",
    )
    with pytest.raises(ArtifactResolutionError, match="non appartiene"):
        resolver.materialize_run(ref)


def test_materialize_run_missing_result_id_is_hard_fail(resolver, monkeypatch):
    import app.db.results_db_service as _rdb

    monkeypatch.setattr(_rdb, "get_result", lambda rid: None)

    ref = RunArtifactRef(project_id="proj-1", result_id="res-1")
    with pytest.raises(ArtifactResolutionError, match="non trovato"):
        resolver.materialize_run(ref)


def test_materialize_run_missing_blob_is_hard_fail(resolver, monkeypatch):
    """BE-GO-READY-RESOURCE-ENGINE-019: a missing blob now arrives
    as ``FileNotFoundError`` from the registry's `get(...)` call.
    The resolver translates it to ``ArtifactResolutionError`` with
    the canonical Italian operator string -- preserved verbatim
    from the pre-019 surface so log greps / tests downstream do
    not regress."""
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-1",
            "group_id": "grp-1",
            "format": "csv",
        },
    )
    fake_registry = _fake_registry_raising(
        FileNotFoundError(
            "ResultBlobLoader.load: result_id='res-1' not found"
        )
    )
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    ref = RunArtifactRef(project_id="proj-1", result_id="res-1")
    with pytest.raises(ArtifactResolutionError, match="senza blob"):
        resolver.materialize_run(ref)


def test_materialize_run_missing_group_id_is_hard_fail(resolver, monkeypatch):
    """BE-GO-READY-RESOURCE-ENGINE-019: a row whose ``group_id`` is
    None / empty is data corruption (every production result row
    has it). The resolver fail-closes BEFORE constructing a
    ResourceRef -- the engine's typed contract requires a non-empty
    ``expected_group_id``, and the resolver gives a clearer
    operator message than the raw ResourceRef ValueError."""
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-1",
            "group_id": None,  # corruption / pre-tenant-binding row
            "format": "csv",
        },
    )

    def _tripwire():
        raise AssertionError(
            "default_registry() must NOT be invoked when the row "
            "has no group_id (resolver fail-closes upstream)"
        )

    monkeypatch.setattr(resolver_mod, "default_registry", _tripwire)

    ref = RunArtifactRef(project_id="proj-1", result_id="res-1")
    with pytest.raises(ArtifactResolutionError, match="senza group_id"):
        resolver.materialize_run(ref)


def test_materialize_run_propagates_loader_oversize_unchanged(
    resolver, monkeypatch,
):
    """The loader's ``MemoryLimitExceededError`` (BE-GO-READY-
    RESOURCE-ENGINE-010 / 013) is NOT a 'missing blob' signal --
    it is a typed cap failure that must propagate to the FastAPI
    413 handler, NOT be translated into the generic resolver
    ``ArtifactResolutionError(senza blob dati)``. This test
    pins that distinction."""
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod
    from app.shared.exceptions import MemoryLimitExceededError

    monkeypatch.setattr(
        _rdb,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-1",
            "group_id": "grp-1",
            "format": "csv",
        },
    )
    fake_registry = _fake_registry_raising(
        MemoryLimitExceededError(
            observed_bytes=999_999_999,
            limit_bytes=200 * 1024 * 1024,
            path_basename="result_id=res-1",
            stage="result_blob_oversize",
        )
    )
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    ref = RunArtifactRef(project_id="proj-1", result_id="res-1")
    # Resolver has a dedicated ``except MemoryLimitExceededError:
    # raise`` clause that propagates the typed cap signal raw to
    # the FastAPI 413 boundary handler. The cap signal MUST NOT
    # be wrapped in ArtifactResolutionError ("senza blob dati" or
    # "lettura blob ... fallita") -- those would obscure the
    # operator path the 413 handler depends on.
    with pytest.raises(MemoryLimitExceededError) as exc_info:
        resolver.materialize_run(ref)
    assert exc_info.value.stage == "result_blob_oversize"
    assert exc_info.value.observed_bytes == 999_999_999


def test_materialize_run_output_path_must_be_inside_project_results(
    resolver, monkeypatch, tmp_path
):
    from app import config as _cfg

    results_dir = tmp_path / "results"
    project_dir = results_dir / "proj-1"
    project_dir.mkdir(parents=True)
    target = project_dir / "out.csv"
    target.write_text("id\n1\n", encoding="utf-8")

    fake_settings = SimpleNamespace(
        RESULTS_DIR=results_dir,
        TEMP_DIR=None,
        DATA_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    ref = RunArtifactRef(project_id="proj-1", output_path=str(target))
    m = resolver.materialize_run(ref)
    assert m.path == target.resolve()
    assert m.is_temporary is False


def test_materialize_run_accepts_output_path_in_data_temp_dir(
    resolver, monkeypatch, tmp_path
):
    """output_path nei tempfile fresh (pre-blob-save) deve essere accettato:
    il path ufficiale RESULTS_DIR esiste solo dopo save_result_blob."""
    from app import config as _cfg

    results_dir = tmp_path / "results"
    data_dir = tmp_path / "data"
    temp_dir = data_dir / "temp"
    temp_dir.mkdir(parents=True)
    target = temp_dir / "fresh_output.csv"
    target.write_text("id\n1\n", encoding="utf-8")

    fake_settings = SimpleNamespace(
        RESULTS_DIR=results_dir,
        DATA_DIR=data_dir,
        TEMP_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    ref = RunArtifactRef(project_id="proj-1", output_path=str(target))
    m = resolver.materialize_run(ref)
    assert m.path == target.resolve()


def test_materialize_run_accepts_output_path_in_temp_dir(
    resolver, monkeypatch, tmp_path
):
    from app import config as _cfg

    results_dir = tmp_path / "results"
    temp_dir = tmp_path / "winttemp"
    temp_dir.mkdir(parents=True)
    target = temp_dir / "x.csv"
    target.write_text("id\n1\n", encoding="utf-8")

    fake_settings = SimpleNamespace(
        RESULTS_DIR=results_dir,
        TEMP_DIR=temp_dir,
        DATA_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    ref = RunArtifactRef(project_id="proj-1", output_path=str(target))
    m = resolver.materialize_run(ref)
    assert m.path == target.resolve()


def test_materialize_run_rejects_output_path_outside_all_trusted_roots(
    resolver, monkeypatch, tmp_path
):
    from app import config as _cfg

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = elsewhere / "evil.csv"
    target.write_text("id\n1\n", encoding="utf-8")

    fake_settings = SimpleNamespace(
        RESULTS_DIR=results_dir,
        DATA_DIR=tmp_path / "data",
        TEMP_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    ref = RunArtifactRef(project_id="proj-1", output_path=str(target))
    with pytest.raises(ArtifactResolutionError, match="fuori dai root consentiti"):
        resolver.materialize_run(ref)


def test_materialize_run_rejects_output_path_in_other_project_results_dir(
    resolver, monkeypatch, tmp_path
):
    """Altri project_id sotto RESULTS_DIR non sono trusted root per
    questo project_id: nessun cross-tenant via output_path."""
    from app import config as _cfg

    results_dir = tmp_path / "results"
    other_dir = results_dir / "proj-2"
    other_dir.mkdir(parents=True)
    target = other_dir / "out.csv"
    target.write_text("id\n1\n", encoding="utf-8")

    fake_settings = SimpleNamespace(
        RESULTS_DIR=results_dir,
        DATA_DIR=None,
        TEMP_DIR=None,
    )
    monkeypatch.setattr(_cfg, "settings", fake_settings, raising=False)

    ref = RunArtifactRef(project_id="proj-1", output_path=str(target))
    with pytest.raises(ArtifactResolutionError, match="fuori dai root"):
        resolver.materialize_run(ref)


def test_materialize_run_rejects_unc_output_path(resolver):
    ref = RunArtifactRef(project_id="proj-1", output_path="\\\\evil\\share\\out.csv")
    with pytest.raises(ArtifactResolutionError, match="UNC"):
        resolver.materialize_run(ref)


# materialize_latest


def test_materialize_latest_returns_none_when_no_result(resolver, monkeypatch):
    import app.db.results_db_service as _rdb

    monkeypatch.setattr(_rdb, "get_latest_result", lambda pid: None)

    ref = LatestProjectArtifactRef(project_id="proj-1")
    assert resolver.materialize_latest(ref) is None


def test_materialize_latest_writes_tempfile_with_format_suffix(
    resolver, monkeypatch
):
    """BE-GO-READY-RESOURCE-ENGINE-021 (R-019.1 closure):
    `materialize_latest` now delegates blob fetch + tempfile
    materialization to ``default_registry().get(ref, mode='path')``.
    Row metadata (group_id, format) still comes from
    ``results_db_service.get_latest_result``."""
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_latest_result",
        lambda pid: {
            "id": "res-1",
            "project_id": pid,
            "group_id": "grp-1",
            "format": "parquet",
        },
    )
    captured, fake_registry = _fake_registry_returning_blob(
        b"PAR1", suffix=".parquet",
    )
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    ref = LatestProjectArtifactRef(project_id="proj-1")
    with resolver.materialize_latest(ref) as m:
        assert m is not None
        assert m.path.suffix == ".parquet"
        assert m.path.read_bytes() == b"PAR1"
        assert m.is_temporary is True
    # Delegation contract: exactly one registry call with the
    # latest-row's group_id + path mode.
    assert len(captured) == 1
    ref_passed, mode_passed = captured[0]
    assert ref_passed.kind == "result_blob"
    assert ref_passed.result_id == "res-1"
    assert ref_passed.expected_group_id == "grp-1"
    assert ref_passed.expected_project_id == "proj-1"
    assert mode_passed == "path"


def test_materialize_latest_enforces_group_binding(resolver, monkeypatch):
    import app.db.results_db_service as _rdb
    import app.db.config.projects as _projects_leaf

    def _tenant_scoped_project(pid, group_id=None):
        assert pid == "proj-1"
        assert group_id == "grp-mine"
        return None

    monkeypatch.setattr(
        _projects_leaf,
        "get_project",
        _tenant_scoped_project,
    )
    monkeypatch.setattr(_rdb, "get_latest_result", lambda pid: None)

    ref = LatestProjectArtifactRef(project_id="proj-1")
    with pytest.raises(ArtifactResolutionError, match="Progetto proj-1 non trovato"):
        resolver.materialize_latest(ref, expected_group_id="grp-mine")


# fetch_run_blob


def _fake_registry_returning_bytes(payload: bytes) -> tuple[list, object]:
    """Build a fake registry whose `get(ref, mode='bytes')` returns
    a `Resource` with `data=payload`. Used for the
    `fetch_run_blob` migration tests (BE-GO-READY-RESOURCE-ENGINE-022).
    """
    from app.core.resources import Resource

    captured: list = []

    def _get(ref, *, mode):
        captured.append((ref, mode))
        return Resource(
            kind="result_blob",
            size_bytes=len(payload),
            data=payload,
            is_temporary=False,
        )

    return captured, SimpleNamespace(get=_get)


def test_fetch_run_blob_returns_data_filename_format(resolver, monkeypatch):
    """BE-GO-READY-RESOURCE-ENGINE-022 (R-019.2 closure): the
    HTTP-download bytes path now uses
    ``default_registry().get(ref, mode='bytes')`` instead of
    ``db_service.get_result_data``. Row metadata (filename / format /
    project_id / group_id) still comes from ``results_db_service.get_result``.
    """
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-1",
            "group_id": "grp-1",
            "filename": "out.csv",
            "format": "csv",
        },
    )
    captured, fake_registry = _fake_registry_returning_bytes(b"id\n1\n")
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    blob = resolver.fetch_run_blob(result_id="res-1", expected_project_id="proj-1")
    assert blob.data == b"id\n1\n"
    assert blob.filename == "out.csv"
    assert blob.format == "csv"
    assert blob.project_id == "proj-1"
    # Verify the resolver delegated to the registry with bytes mode
    # (no path/temp materialization for the HTTP download path).
    assert len(captured) == 1
    _ref, _mode = captured[0]
    assert _mode == "bytes"
    assert _ref.kind == "result_blob"
    assert _ref.result_id == "res-1"
    assert _ref.expected_group_id == "grp-1"
    assert _ref.expected_project_id == "proj-1"


def test_fetch_run_blob_rejects_cross_tenant(resolver, monkeypatch):
    import app.db.results_db_service as _rdb

    monkeypatch.setattr(
        _rdb,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-2",
            "group_id": "grp-1",
            "filename": "x",
            "format": "csv",
        },
    )
    with pytest.raises(ArtifactResolutionError, match="non appartiene"):
        resolver.fetch_run_blob(result_id="res-1", expected_project_id="proj-1")


def test_fetch_run_blob_missing_result_is_hard_fail(resolver, monkeypatch):
    import app.db.results_db_service as _rdb

    monkeypatch.setattr(_rdb, "get_result", lambda rid: None)
    with pytest.raises(ArtifactResolutionError, match="non trovato"):
        resolver.fetch_run_blob(result_id="res-1", expected_project_id="proj-1")


def test_fetch_run_blob_translates_loader_filenotfound_to_typed_error(resolver, monkeypatch):
    """If the loader raises FileNotFoundError (row missing /
    cross-tenant SQL filter / blob unavailable) the resolver
    translates it to the canonical Italian message used by
    existing operator log greps."""
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-1",
            "group_id": "grp-1",
            "filename": "out.csv",
            "format": "csv",
        },
    )

    def _get(ref, *, mode):  # noqa: ARG001
        raise FileNotFoundError("loader fail-closed")

    monkeypatch.setattr(
        resolver_mod, "default_registry",
        lambda: SimpleNamespace(get=_get),
    )

    with pytest.raises(ArtifactResolutionError, match="senza blob dati"):
        resolver.fetch_run_blob(result_id="res-1", expected_project_id="proj-1")


def test_fetch_run_blob_row_without_group_id_is_hard_fail(resolver, monkeypatch):
    """A row without `group_id` is data corruption (or a
    pre-tenant-binding row); fail-closed before any registry call."""
    import app.db.results_db_service as _rdb

    monkeypatch.setattr(
        _rdb,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-1",
            "group_id": None,
            "filename": "out.csv",
            "format": "csv",
        },
    )
    with pytest.raises(ArtifactResolutionError, match="senza group_id"):
        resolver.fetch_run_blob(result_id="res-1", expected_project_id="proj-1")


def test_materialize_latest_passes_when_group_matches(resolver, monkeypatch):
    """BE-GO-READY-RESOURCE-ENGINE-021: when the upstream project
    group check passes, the resolver delegates to the registry
    with ``is_superadmin=False`` and the latest-row's group_id."""
    import app.db.results_db_service as _rdb
    import app.db.config.projects as _projects_leaf
    import app.core.artifacts.resolver as resolver_mod

    def _tenant_scoped_project(pid, group_id=None):
        assert pid == "proj-1"
        assert group_id == "grp-mine"
        return {"id": pid, "group_id": "grp-mine"}

    monkeypatch.setattr(
        _projects_leaf,
        "get_project",
        _tenant_scoped_project,
    )
    monkeypatch.setattr(
        _rdb,
        "get_latest_result",
        lambda pid: {
            "id": "res-1",
            "project_id": pid,
            "group_id": "grp-mine",
            "format": "csv",
        },
    )
    captured, fake_registry = _fake_registry_returning_blob(
        b"id\n1\n", suffix=".csv",
    )
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    ref = LatestProjectArtifactRef(project_id="proj-1")
    m = resolver.materialize_latest(ref, expected_group_id="grp-mine")
    try:
        assert m is not None
        assert m.path.suffix == ".csv"
    finally:
        m.cleanup()
    # Non-superadmin path: ResourceRef carries is_superadmin=False
    assert len(captured) == 1
    ref_passed, _mode = captured[0]
    assert ref_passed.is_superadmin is False
    assert ref_passed.expected_group_id == "grp-mine"


def test_materialize_latest_superadmin_bypass_cross_tenant(resolver, monkeypatch):
    """Regression guard: il bypass `is_superadmin=True` deve permettere
    a un superadmin di leggere il latest output di un progetto
    appartenente a un altro gruppo. Prima del fix, il check stretto
    `actual_group != expected_group_id` faceva fallire `validate_enrichment`
    su progetti visibili (allineato al policy di assert_project_visible)
    con 'Errori di validazione' user-visible.

    BE-GO-READY-RESOURCE-ENGINE-021: post-migration, the resolver
    threads ``is_superadmin=True`` through to the ResourceRef so
    the loader's audit-bypass log entry mirrors the upstream
    project bypass."""
    import app.db.results_db_service as _rdb
    import app.db.config.projects as _projects_leaf
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _projects_leaf,
        "get_project",
        lambda pid: {"id": pid, "group_id": "gruppo_ania"},
    )
    monkeypatch.setattr(
        _rdb,
        "get_latest_result",
        lambda pid: {
            "id": "res-1",
            "project_id": pid,
            "group_id": "gruppo_ania",
            "format": "csv",
        },
    )
    captured, fake_registry = _fake_registry_returning_blob(
        b"id\n1\n", suffix=".csv",
    )
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    ref = LatestProjectArtifactRef(project_id="proj-cross")
    m = resolver.materialize_latest(
        ref,
        expected_group_id="default",  # gruppo diverso da gruppo_ania
        is_superadmin=True,
    )
    try:
        assert m is not None, "superadmin deve poter leggere cross-tenant"
        assert m.path.suffix == ".csv"
    finally:
        m.cleanup()
    # Superadmin path: ResourceRef carries is_superadmin=True so
    # the loader records the audit-bypass log entry symmetrically
    # with the resolver-level project bypass.
    assert len(captured) == 1
    ref_passed, _mode = captured[0]
    assert ref_passed.is_superadmin is True
    assert ref_passed.expected_group_id == "gruppo_ania"


def test_materialize_latest_non_superadmin_blocks_cross_tenant(resolver, monkeypatch):
    """Mirror del test sopra: senza `is_superadmin=True` il check
    cross-tenant deve scattare senza rivelare il tenant reale. Il
    default e' fail-closed."""
    import app.db.results_db_service as _rdb
    import app.db.config.projects as _projects_leaf

    def _tenant_scoped_project(pid, group_id=None):
        assert pid == "proj-cross"
        assert group_id == "default"
        return None

    monkeypatch.setattr(
        _projects_leaf,
        "get_project",
        _tenant_scoped_project,
    )
    monkeypatch.setattr(_rdb, "get_latest_result", lambda pid: None)

    ref = LatestProjectArtifactRef(project_id="proj-cross")
    with pytest.raises(
        ArtifactResolutionError,
        match="Progetto proj-cross non trovato",
    ):
        resolver.materialize_latest(
            ref,
            expected_group_id="default",
            is_superadmin=False,
        )


# BE-GO-READY-RESOURCE-ENGINE-021: materialize_latest registry boundary


def test_materialize_latest_returns_none_on_loader_filenotfound(
    resolver, monkeypatch,
):
    """021: a `FileNotFoundError` from the registry (row exists in
    `get_latest_result` but the loader cannot resolve the blob --
    object storage outage / stale row / mid-flight delete race)
    is preserved as the legacy ``return None`` semantic. The
    router/UI treats None as "no latest result"; raising would
    500 the request."""
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_latest_result",
        lambda pid: {
            "id": "res-1",
            "project_id": pid,
            "group_id": "grp-1",
            "format": "csv",
        },
    )
    fake_registry = _fake_registry_raising(
        FileNotFoundError(
            "ResultBlobLoader.load: result_id='res-1' blob unavailable"
        )
    )
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    ref = LatestProjectArtifactRef(project_id="proj-1")
    assert resolver.materialize_latest(ref) is None


def test_materialize_latest_missing_group_id_is_hard_fail(
    resolver, monkeypatch,
):
    """021: a latest-row with no `group_id` (data corruption /
    pre-tenant-binding row) cannot satisfy `ResourceRef.
    expected_group_id` (REQUIRED non-empty by `__post_init__`).
    Resolver fail-closes BEFORE constructing the ref so the
    operator sees a clearer message than the raw ResourceRef
    ValueError."""
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_latest_result",
        lambda pid: {
            "id": "res-1",
            "project_id": pid,
            "group_id": None,  # corruption / pre-tenant-binding row
            "format": "csv",
        },
    )

    def _tripwire():
        raise AssertionError(
            "default_registry() must NOT be invoked when the latest "
            "row has no group_id (resolver fail-closes upstream)"
        )

    monkeypatch.setattr(resolver_mod, "default_registry", _tripwire)

    ref = LatestProjectArtifactRef(project_id="proj-1")
    with pytest.raises(ArtifactResolutionError, match="without group_id"):
        resolver.materialize_latest(ref)


def test_materialize_latest_propagates_loader_oversize_unchanged(
    resolver, monkeypatch,
):
    """021: the loader's ``MemoryLimitExceededError`` (BE-GO-READY-
    RESOURCE-ENGINE-010 / 013) MUST propagate through
    `materialize_latest` to the FastAPI 413 boundary. NOT silently
    ``return None`` (which would hide the resource cap signal),
    NOT translated into the generic "Cross-tenant" /
    "ArtifactResolutionError" path. Pinned because the legacy
    `if not blob: return None` pattern would have masked oversize
    as `None`; the registry migration must NOT regress to that."""
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod
    from app.shared.exceptions import MemoryLimitExceededError

    monkeypatch.setattr(
        _rdb,
        "get_latest_result",
        lambda pid: {
            "id": "res-1",
            "project_id": pid,
            "group_id": "grp-1",
            "format": "csv",
        },
    )
    fake_registry = _fake_registry_raising(
        MemoryLimitExceededError(
            observed_bytes=999_999_999,
            limit_bytes=200 * 1024 * 1024,
            path_basename="result_id=res-1",
            stage="result_blob_oversize",
        )
    )
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    ref = LatestProjectArtifactRef(project_id="proj-1")
    with pytest.raises(MemoryLimitExceededError) as exc_info:
        resolver.materialize_latest(ref)
    # Critical regression cover: this must NOT be silently None and
    # must NOT be wrapped in ArtifactResolutionError, otherwise the
    # FastAPI MemoryLimitExceededError handler would not produce 413.
    assert exc_info.value.stage == "result_blob_oversize"
    assert exc_info.value.limit_bytes == 200 * 1024 * 1024


def test_materialize_latest_translates_other_loader_faults(
    resolver, monkeypatch, caplog,
):
    """021: non-FileNotFoundError / non-MemoryLimitExceededError
    loader faults (PG connection drop, driver bug, etc.) are
    wrapped in ArtifactResolutionError("lettura blob latest ...
    fallita") with [GESTITO] log + chained cause. Mirrors the
    019 broad-except contract on `_materialize_from_result_id`."""
    import logging
    import app.db.results_db_service as _rdb
    import app.core.artifacts.resolver as resolver_mod

    monkeypatch.setattr(
        _rdb,
        "get_latest_result",
        lambda pid: {
            "id": "res-1",
            "project_id": pid,
            "group_id": "grp-1",
            "format": "csv",
        },
    )

    class _PgConnectionDrop(RuntimeError):
        pass

    fake_registry = _fake_registry_raising(
        _PgConnectionDrop("psycopg2.OperationalError: server closed")
    )
    monkeypatch.setattr(
        resolver_mod, "default_registry", lambda: fake_registry,
    )

    # Capture the resolver's logger directly (StructuredLogger has
    # propagate=False).
    handler_messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            handler_messages.append(record.getMessage())

    logger = logging.getLogger("app.core.artifacts.resolver")
    h = _Capture()
    logger.addHandler(h)
    try:
        ref = LatestProjectArtifactRef(project_id="proj-1")
        with pytest.raises(ArtifactResolutionError) as exc_info:
            resolver.materialize_latest(ref)
    finally:
        logger.removeHandler(h)

    # Wrapped error message + chain preserved.
    assert "lettura blob latest project_id 'proj-1' fallita" in str(
        exc_info.value
    )
    assert isinstance(exc_info.value.__cause__, _PgConnectionDrop)
    # [GESTITO] log emitted before raise.
    gestito = [m for m in handler_messages if "[GESTITO]" in m]
    assert gestito, (
        "broad-except path must emit a [GESTITO] log before raise"
    )
