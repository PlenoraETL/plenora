from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils.result_store import ResultSaveError, save_result_blob


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_save_result_blob_removes_temp_file_only_after_success(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    saved = {}

    import app.db.db_service as db_service
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        result_store,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-1"),
    )
    monkeypatch.setattr(
        db_service,
        "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )
    monkeypatch.setattr(
        db_service,
        "save_result",
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

    import app.db.db_service as db_service
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        result_store,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-1"),
    )
    monkeypatch.setattr(
        db_service,
        "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )
    monkeypatch.setattr(
        db_service,
        "save_result",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    with pytest.raises(ResultSaveError, match="Salvataggio risultato nel DB fallito"):
        save_result_blob("proj-1", result_file, "csv", rows_count=1)

    assert result_file.exists()
    assert temp_dir.exists()


# =============================================================================
# RB.2 (2026-04-27): AWS credentials expired -> ResultSaveError con messaggio
# operativo, no fallback silenzioso a DB se policy/threshold dice S3.
# =============================================================================


def _force_object_storage(monkeypatch, tmp_path):
    """Setup minimo per portare save_result_blob nel ramo object_storage."""
    import app.db.db_service as db_service
    import app.utils.result_store as result_store
    from types import SimpleNamespace

    monkeypatch.setattr(
        result_store,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-rb2"),
    )
    monkeypatch.setattr(
        db_service,
        "get_project",
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
