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


# ---------------------------------------------------------------------------
# materialize_run
# ---------------------------------------------------------------------------


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
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_result",
        lambda rid: {"id": rid, "project_id": "proj-1", "format": "csv"},
    )
    monkeypatch.setattr(db_service, "get_result_data", lambda rid: b"id\n1\n")

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


def test_materialize_run_rejects_cross_tenant_result_id(resolver, monkeypatch):
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_result",
        lambda rid: {"id": rid, "project_id": "proj-2", "format": "csv"},
    )
    monkeypatch.setattr(db_service, "get_result_data", lambda rid: b"x")

    ref = RunArtifactRef(
        project_id="proj-1",
        result_id="res-1",
        output_path="C:/tmp/fallback.csv",
    )
    with pytest.raises(ArtifactResolutionError, match="non appartiene"):
        resolver.materialize_run(ref)


def test_materialize_run_missing_result_id_is_hard_fail(resolver, monkeypatch):
    import app.db.db_service as db_service

    monkeypatch.setattr(db_service, "get_result", lambda rid: None)

    ref = RunArtifactRef(project_id="proj-1", result_id="res-1")
    with pytest.raises(ArtifactResolutionError, match="non trovato"):
        resolver.materialize_run(ref)


def test_materialize_run_missing_blob_is_hard_fail(resolver, monkeypatch):
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_result",
        lambda rid: {"id": rid, "project_id": "proj-1", "format": "csv"},
    )
    monkeypatch.setattr(db_service, "get_result_data", lambda rid: None)

    ref = RunArtifactRef(project_id="proj-1", result_id="res-1")
    with pytest.raises(ArtifactResolutionError, match="senza blob"):
        resolver.materialize_run(ref)


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


# ---------------------------------------------------------------------------
# materialize_latest
# ---------------------------------------------------------------------------


def test_materialize_latest_returns_none_when_no_result(resolver, monkeypatch):
    import app.db.db_service as db_service

    monkeypatch.setattr(db_service, "get_latest_result", lambda pid: None)

    ref = LatestProjectArtifactRef(project_id="proj-1")
    assert resolver.materialize_latest(ref) is None


def test_materialize_latest_writes_tempfile_with_format_suffix(
    resolver, monkeypatch
):
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_latest_result",
        lambda pid: {"id": "res-1", "project_id": pid, "format": "parquet"},
    )
    monkeypatch.setattr(db_service, "get_result_data", lambda rid: b"PAR1")

    ref = LatestProjectArtifactRef(project_id="proj-1")
    with resolver.materialize_latest(ref) as m:
        assert m is not None
        assert m.path.suffix == ".parquet"
        assert m.path.read_bytes() == b"PAR1"
        assert m.is_temporary is True


def test_materialize_latest_enforces_group_binding(resolver, monkeypatch):
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_project",
        lambda pid: {"id": pid, "group_id": "grp-other"},
    )
    monkeypatch.setattr(db_service, "get_latest_result", lambda pid: None)

    ref = LatestProjectArtifactRef(project_id="proj-1")
    with pytest.raises(ArtifactResolutionError, match="Cross-tenant"):
        resolver.materialize_latest(ref, expected_group_id="grp-mine")


# ---------------------------------------------------------------------------
# fetch_run_blob
# ---------------------------------------------------------------------------


def test_fetch_run_blob_returns_data_filename_format(resolver, monkeypatch):
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-1",
            "filename": "out.csv",
            "format": "csv",
        },
    )
    monkeypatch.setattr(db_service, "get_result_data", lambda rid: b"id\n1\n")

    blob = resolver.fetch_run_blob(result_id="res-1", expected_project_id="proj-1")
    assert blob.data == b"id\n1\n"
    assert blob.filename == "out.csv"
    assert blob.format == "csv"
    assert blob.project_id == "proj-1"


def test_fetch_run_blob_rejects_cross_tenant(resolver, monkeypatch):
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_result",
        lambda rid: {"id": rid, "project_id": "proj-2", "filename": "x", "format": "csv"},
    )
    with pytest.raises(ArtifactResolutionError, match="non appartiene"):
        resolver.fetch_run_blob(result_id="res-1", expected_project_id="proj-1")


def test_fetch_run_blob_missing_result_is_hard_fail(resolver, monkeypatch):
    import app.db.db_service as db_service

    monkeypatch.setattr(db_service, "get_result", lambda rid: None)
    with pytest.raises(ArtifactResolutionError, match="non trovato"):
        resolver.fetch_run_blob(result_id="res-1", expected_project_id="proj-1")


def test_materialize_latest_passes_when_group_matches(resolver, monkeypatch):
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_project",
        lambda pid: {"id": pid, "group_id": "grp-mine"},
    )
    monkeypatch.setattr(
        db_service,
        "get_latest_result",
        lambda pid: {"id": "res-1", "project_id": pid, "format": "csv"},
    )
    monkeypatch.setattr(db_service, "get_result_data", lambda rid: b"id\n1\n")

    ref = LatestProjectArtifactRef(project_id="proj-1")
    m = resolver.materialize_latest(ref, expected_group_id="grp-mine")
    try:
        assert m is not None
        assert m.path.suffix == ".csv"
    finally:
        m.cleanup()


def test_materialize_latest_superadmin_bypass_cross_tenant(resolver, monkeypatch):
    """Regression guard: il bypass `is_superadmin=True` deve permettere
    a un superadmin di leggere il latest output di un progetto
    appartenente a un altro gruppo. Prima del fix, il check stretto
    `actual_group != expected_group_id` faceva fallire `validate_enrichment`
    su progetti visibili (allineato al policy di assert_project_visible)
    con 'Errori di validazione' user-visible."""
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_project",
        lambda pid: {"id": pid, "group_id": "gruppo_ania"},
    )
    monkeypatch.setattr(
        db_service,
        "get_latest_result",
        lambda pid: {"id": "res-1", "project_id": pid, "format": "csv"},
    )
    monkeypatch.setattr(db_service, "get_result_data", lambda rid: b"id\n1\n")

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


def test_materialize_latest_non_superadmin_blocks_cross_tenant(resolver, monkeypatch):
    """Mirror del test sopra: senza `is_superadmin=True` il check
    cross-tenant deve scattare. Il default e' fail-closed."""
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service,
        "get_project",
        lambda pid: {"id": pid, "group_id": "gruppo_ania"},
    )
    monkeypatch.setattr(db_service, "get_latest_result", lambda pid: None)

    ref = LatestProjectArtifactRef(project_id="proj-cross")
    with pytest.raises(ArtifactResolutionError, match="Cross-tenant"):
        resolver.materialize_latest(
            ref,
            expected_group_id="default",
            is_superadmin=False,
        )
