"""
Tests the result store behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils.result_store import (
    PipelineResultSave,
    ResultSaveError,
    save_pipeline_result,
    save_pipeline_result_outcome,
    save_result_blob,
    save_result_blob_from_bytes,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_save_result_blob_removes_temp_file_only_after_success(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    saved = {}

    import app.db.config.projects as projects_db
    import app.core.results.db as results_db
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        result_store,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-1"),
    )
    monkeypatch.setattr(projects_db, "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )
    monkeypatch.setattr(results_db, "save_result_row",
        lambda **kwargs: saved.update(kwargs),
    )
    monkeypatch.setattr(
        result_store,
        "_apply_results_retention",
        lambda project_id: None,
    )
    monkeypatch.setattr(
        result_store,
        "settings",
        None,
        raising=False,
    )

    result_id = save_result_blob("proj-1", result_file, "csv", rows_count=1)

    assert result_id == "res-1"
    assert saved["project_id"] == "proj-1"
    assert saved["group_id"] == "grp-1"
    assert not result_file.exists()
    assert not temp_dir.exists()


def test_save_result_blob_keeps_temp_file_when_db_save_fails(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    import app.db.config.projects as projects_db
    import app.core.results.db as results_db
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        result_store,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-1"),
    )
    monkeypatch.setattr(projects_db, "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )
    monkeypatch.setattr(results_db, "save_result_row",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    with pytest.raises(ResultSaveError, match="Salvataggio risultato nel DB fallito"):
        save_result_blob("proj-1", result_file, "csv", rows_count=1)

    assert result_file.exists()
    assert temp_dir.exists()


def test_apply_results_retention_uses_typed_result_rows(monkeypatch):
    import app.core.projects.db as projects_db
    import app.core.results.cleanup as results_cleanup
    import app.core.results.db as results_db
    import app.utils.result_store as result_store

    deleted: list[str] = []
    rows = [
        results_db.ResultMetadataRow(id="res-new"),
        results_db.ResultMetadataRow(id="res-keep"),
        results_db.ResultMetadataRow(id="res-old"),
    ]

    monkeypatch.setattr(projects_db, "get_project_retention_n", lambda pid: 2)
    monkeypatch.setattr(results_db, "list_result_rows", lambda pid: rows)
    monkeypatch.setattr(
        results_db,
        "list_results",
        lambda pid: (_ for _ in ()).throw(
            AssertionError("legacy result dict wrapper should not be used")
        ),
    )
    monkeypatch.setattr(results_cleanup, "delete_result", deleted.append)

    result_store._apply_results_retention("proj-1")

    assert deleted == ["res-old"]


def test_save_pipeline_result_outcome_is_typed_internal_boundary(monkeypatch):
    import app.utils.result_store as result_store

    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "app.utils.dataframe.save_dataframe_to_bytes",
        lambda df, fmt, geom_column=None, crs="EPSG:4326": b"id\n1\n",
    )

    def _save_from_bytes(**kwargs):
        calls.update(kwargs)
        return "res-typed"

    monkeypatch.setattr(
        result_store,
        "save_result_blob_from_bytes",
        _save_from_bytes,
    )

    outcome = save_pipeline_result_outcome(
        project_id="proj-1",
        df=object(),
        output_format="csv",
        filename_stem="typed-output",
        rows_count=1,
        group_id="grp-1",
    )

    assert isinstance(outcome, PipelineResultSave)
    assert outcome.result_id == "res-typed"
    assert outcome.output_filename == "typed-output.csv"
    assert calls["filename"] == "typed-output.csv"


def test_save_pipeline_result_public_wrapper_is_explicit_legacy_dict(monkeypatch):
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        result_store,
        "save_pipeline_result_outcome",
        lambda **kwargs: PipelineResultSave(
            result_id="res-compat",
            output_filename="compat.csv",
        ),
    )

    assert save_pipeline_result(
        project_id="proj-1",
        df=object(),
        output_format="csv",
        filename_stem="compat",
    ) == {"result_id": "res-compat", "output_filename": "compat.csv"}


# RB.2 (2026-04-27): AWS credentials expired -> ResultSaveError con messaggio
# operativo, no fallback silenzioso a DB se policy/threshold dice S3.


def _force_object_storage(monkeypatch, tmp_path):
    """Setup minimo per portare save_result_blob nel ramo object_storage."""
    import app.db.config.projects as projects_db
    import app.utils.result_store as result_store
    from types import SimpleNamespace

    monkeypatch.setattr(
        result_store,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-rb2"),
    )
    monkeypatch.setattr(projects_db, "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )
    # Forza policy=object_storage cosi' upload S3 e' obbligatorio.
    monkeypatch.setattr(result_store, "_storage_policy", lambda: "object_storage")


def test_save_result_blob_aws_credentials_expired_raises_operational_message(
    monkeypatch, tmp_path
):
    """Quando lo storage solleva LoginRefreshRequired (SSO scaduto),
    save_result_blob deve far fallire il job con un messaggio che dica
    all'operatore di rinnovare le credenziali. Niente fallback a DB."""
    from botocore.exceptions import LoginRefreshRequired

    import app.utils.result_store as result_store

    _force_object_storage(monkeypatch, tmp_path)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    class _ExpiredStorage:
        def upload(self, path, key):
            raise LoginRefreshRequired()

    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        lambda: _ExpiredStorage(),
    )

    with pytest.raises(ResultSaveError) as excinfo:
        save_result_blob("proj-1", result_file, "csv", rows_count=1)

    msg = str(excinfo.value)
    assert "AWS credentials expired" in msg
    assert "aws sso login" in msg
    # Il file temp resta sul disco per recovery manuale (no _saved=True).
    assert result_file.exists()
    # __cause__ preserva la classe botocore originale per debugging.
    assert isinstance(excinfo.value.__cause__, LoginRefreshRequired)


def test_save_result_blob_no_credentials_error_raises_operational_message(
    monkeypatch, tmp_path
):
    """Stesso pattern per NoCredentialsError (~/.aws/credentials assente)."""
    from botocore.exceptions import NoCredentialsError

    _force_object_storage(monkeypatch, tmp_path)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    class _NoCredsStorage:
        def upload(self, path, key):
            raise NoCredentialsError()

    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        lambda: _NoCredsStorage(),
    )

    with pytest.raises(ResultSaveError, match="AWS credentials"):
        save_result_blob("proj-1", result_file, "csv", rows_count=1)


def test_save_result_blob_generic_storage_error_keeps_old_message(
    monkeypatch, tmp_path
):
    """Errore S3 NON-credenziale (es. timeout rete) deve mantenere il
    messaggio storico 'Upload object storage fallito' per non perdere
    granularita'. Il routing operativo passa per credentials expired,
    non sostituisce tutti gli upload error."""
    _force_object_storage(monkeypatch, tmp_path)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    class _NetworkErrorStorage:
        def upload(self, path, key):
            raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        lambda: _NetworkErrorStorage(),
    )

    with pytest.raises(ResultSaveError) as excinfo:
        save_result_blob("proj-1", result_file, "csv", rows_count=1)

    msg = str(excinfo.value)
    assert "Upload object storage fallito" in msg
    assert "AWS credentials" not in msg  # NON deve essere mappato erroneamente


# A1 batch — STORAGE_POLICY=object_storage: capability off => fail-closed.
# Niente fallback BYTEA per i nuovi result quando la policy chiede object
# storage e la capability runtime e' disabilitata.


class _RecordingStorage:
    """Fake runtime object storage che registra upload per assertion.

    Implementa la slice di ObjectStorage usata dai writer: `upload(path, key)`
    (path-based) e `upload_bytes(data, key)` (in-memory). Niente S3/MinIO.
    """

    def __init__(self):
        self.uploads_path: list[tuple[str, str]] = []
        self.uploads_bytes: list[tuple[bytes, str]] = []

    def upload(self, local_path, key):
        self.uploads_path.append((str(local_path), key))

    def upload_bytes(self, data, key):
        self.uploads_bytes.append((data, key))


def _wire_object_storage_writer(monkeypatch, *, fake_storage, saved):
    """Setup minimo simmetrico a `_force_object_storage`, ma per i test
    "happy path" che vogliono verificare il routing object_storage.
    Forza policy=object_storage, mock di db.save_result con cattura kwargs,
    e installa un fake runtime object storage che registra gli upload."""
    import app.db.config.projects as projects_db
    import app.core.results.db as results_db
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        result_store,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-os-1"),
    )
    monkeypatch.setattr(projects_db, "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )
    monkeypatch.setattr(results_db, "save_result_row",
        lambda **kwargs: saved.update(kwargs),
    )
    monkeypatch.setattr(result_store, "_apply_results_retention", lambda pid: None)
    # policy=object_storage: dispatcher forza sempre object storage
    monkeypatch.setattr(result_store, "_storage_policy", lambda: "object_storage")
    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        lambda: fake_storage,
    )


# A1 batch — CASO 1 + 2: policy=object_storage forza l'upload runtime ANCHE
# per payload sotto threshold. Contratto centrale del batch: niente fallback
# BYTEA per i nuovi result quando la policy chiede object storage.


def test_save_result_blob_object_storage_small_file_goes_to_runtime(
    monkeypatch, tmp_path
):
    """save_result_blob (path-based): policy=object_storage + file molto
    piccolo (sotto qualsiasi threshold ragionevole) deve comunque
    uploadare su runtime object storage, NON scrivere BYTEA."""
    saved: dict = {}
    fake = _RecordingStorage()
    _wire_object_storage_writer(monkeypatch, fake_storage=fake, saved=saved)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "tiny.csv"
    # 6 byte: ben sotto qualsiasi RESULT_S3_THRESHOLD_BYTES configurabile.
    payload = b"id\n1\n"
    result_file.write_bytes(payload)
    expected_size = result_file.stat().st_size

    rid = save_result_blob("proj-1", result_file, "csv", rows_count=1)

    # Upload runtime eseguito (niente BYTEA in DB).
    assert rid == "res-os-1"
    assert len(fake.uploads_path) == 1
    upload_path, upload_key = fake.uploads_path[0]
    assert upload_key == "results/proj-1/res-os-1.bin"
    # Persistenza DB: solo metadata + storage_key, data=NULL.
    assert saved["data"] is None
    assert saved["storage_key"] == "results/proj-1/res-os-1.bin"
    assert saved["size_bytes"] == expected_size
    # File temp pulito post-success (path-based usa _saved=True nel finally).
    assert not result_file.exists()


def test_save_result_blob_from_bytes_object_storage_small_payload_goes_to_runtime(
    monkeypatch, tmp_path
):
    """save_result_blob_from_bytes (in-memory): policy=object_storage +
    payload piccolo deve comunque uploadare su runtime, NON scrivere BYTEA."""
    saved: dict = {}
    fake = _RecordingStorage()
    _wire_object_storage_writer(monkeypatch, fake_storage=fake, saved=saved)

    payload = b"id,name\n1,foo\n"  # 14 byte: sotto qualsiasi threshold

    rid = save_result_blob_from_bytes(
        project_id="proj-1",
        data=payload,
        filename="tiny.csv",
        fmt="csv",
        rows_count=1,
    )

    assert rid == "res-os-1"
    # In-memory writer usa upload_bytes (no path).
    assert fake.uploads_bytes == [(payload, "results/proj-1/res-os-1.bin")]
    assert fake.uploads_path == []
    assert saved["data"] is None
    assert saved["storage_key"] == "results/proj-1/res-os-1.bin"
    assert saved["size_bytes"] == len(payload)


def test_save_result_blob_object_storage_capability_off_raises(
    monkeypatch, tmp_path
):
    """Con STORAGE_POLICY=object_storage e get_runtime_object_storage()
    che solleva ObjectStorageDisabledError (capability off / bucket
    mancante), il writer deve fallire esplicitamente. Nessun degrado
    silenzioso a BYTEA: il result non viene salvato e il file temp
    resta sul disco per recovery."""
    from app.shared.cloud_backends import ObjectStorageDisabledError

    _force_object_storage(monkeypatch, tmp_path)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    def _disabled_factory():
        raise ObjectStorageDisabledError(
            "Object storage disabilitato in questo deployment"
        )

    monkeypatch.setattr(
        "app.shared.cloud_backends.get_runtime_object_storage",
        _disabled_factory,
    )

    with pytest.raises(ResultSaveError) as excinfo:
        save_result_blob("proj-1", result_file, "csv", rows_count=1)

    # Il messaggio deve far capire l'origine (object storage upload),
    # non un generico "DB save fallito".
    assert "Upload object storage fallito" in str(excinfo.value)
    # Il file temp NON viene cancellato: _saved=False fino a salvataggio
    # confermato. Coerente con il pattern di recovery manuale.
    assert result_file.exists()
    # __cause__ preserva la classe originale per debugging/observability.
    assert isinstance(excinfo.value.__cause__, ObjectStorageDisabledError)
