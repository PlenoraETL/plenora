"""
Tests the result store behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.results.blob_writer import (
    ResultSaveError,
    save_result_blob_from_bytes,
)
from app.core.results.pipeline_result import (
    PipelineResultSave,
    save_pipeline_result,
    save_pipeline_result_outcome,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]

legacy_path_result_blob = pytest.mark.skip(
    reason="save_result_blob(path) tombstonato post QUERY-1; usare save_result_blob_from_bytes"
)


@legacy_path_result_blob
def test_save_result_blob_removes_temp_file_only_after_success(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    saved = {}

    import app.core.results.store as results_store
    import app.core.results.blob_writer as result_blob_writer

    monkeypatch.setattr(
        result_blob_writer,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-1"),
    )
    monkeypatch.setattr(
        results_store,
        "resolve_group_id_for_result",
        lambda project_id, group_id=None: group_id or "grp-1",
    )
    monkeypatch.setattr(results_store, "save_result_row",
        lambda **kwargs: saved.update(kwargs),
    )
    monkeypatch.setattr(
        result_blob_writer,
        "apply_results_retention_after_save",
        lambda project_id: None,
    )
    monkeypatch.setattr(
        result_blob_writer,
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


@legacy_path_result_blob
def test_save_result_blob_keeps_temp_file_when_db_save_fails(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    import app.core.results.store as results_store
    import app.core.results.blob_writer as result_blob_writer

    monkeypatch.setattr(
        result_blob_writer,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-1"),
    )
    monkeypatch.setattr(
        results_store,
        "resolve_group_id_for_result",
        lambda project_id, group_id=None: group_id or "grp-1",
    )
    monkeypatch.setattr(results_store, "save_result_row",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    with pytest.raises(ResultSaveError, match="Salvataggio risultato nel DB fallito"):
        save_result_blob("proj-1", result_file, "csv", rows_count=1)

    assert result_file.exists()
    assert temp_dir.exists()


def test_apply_results_retention_uses_typed_result_rows(monkeypatch):
    import app.core.projects.runtime_context as project_runtime_context
    import app.core.results.cleanup as results_cleanup
    import app.core.results.db as results_db
    import app.core.results.retention as results_retention
    import app.core.results.store as results_store

    deleted: list[str] = []
    rows = [
        results_db.ResultMetadataRow(id="res-new"),
        results_db.ResultMetadataRow(id="res-keep"),
        results_db.ResultMetadataRow(id="res-old"),
    ]

    monkeypatch.setattr(
        project_runtime_context,
        "resolve_project_runtime_retention_n",
        lambda pid, *, default: 2,
    )
    monkeypatch.setattr(results_store, "list_result_rows", lambda pid: rows)
    # Sentinel residuo: il legacy `list_results` (dict wrapper) e' stato
    # rimosso dal modulo - se qualcuno lo reintroducesse, questo assert
    # fallirebbe perche' la lookup tornerebbe truthy.
    assert not hasattr(results_db, "list_results"), (
        "legacy result dict wrapper should stay removed"
    )
    monkeypatch.setattr(results_cleanup, "delete_result", deleted.append)

    results_retention.apply_results_retention("proj-1")

    assert deleted == ["res-old"]


def test_blob_writer_retention_wrapper_delegates_to_results_domain(monkeypatch):
    import app.core.results.blob_writer as result_blob_writer

    called: list[str] = []
    monkeypatch.setattr(
        result_blob_writer,
        "apply_results_retention",
        called.append,
    )

    result_blob_writer.apply_results_retention_after_save("proj-1")

    assert called == ["proj-1"]


def test_result_store_compat_facade_is_tombstoned():
    backend_root = Path(__file__).resolve().parents[2]
    assert not (backend_root / "app" / "core" / "results" / "result_store.py").exists()


def test_production_does_not_import_result_store_compat_facade():
    """New production code must use the concrete results modules directly."""
    backend_root = Path(__file__).resolve().parents[2]
    prod_roots = (backend_root / "app", backend_root / "workers")
    facade = "app.core.results.result_store"
    violations: list[str] = []

    for root in prod_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(backend_root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(alias.name == facade for alias in node.names):
                        violations.append(f"{rel}:{node.lineno} -> {facade}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module == facade:
                        violations.append(f"{rel}:{node.lineno} -> {facade}")
                    elif (
                        node.module == "app.core.results"
                        and any(alias.name == "result_store" for alias in node.names)
                    ):
                        violations.append(f"{rel}:{node.lineno} -> {facade}")

    assert violations == [], (
        "Production code must import concrete results modules "
        "(`blob_writer`, `pipeline_result`, `result_reader`, `temp_paths`) "
        f"instead of result_store facade. Violations: {violations}"
    )


def test_result_store_slice_has_no_row_owned_to_legacy_dict():
    """Result-store dict compatibility belongs at explicit public edges."""
    backend_root = Path(__file__).resolve().parents[2]
    files = [
        *sorted((backend_root / "app" / "core" / "results").glob("*.py")),
    ]

    class_methods: list[str] = []
    call_sites: list[str] = []

    for path in files:
        rel = path.relative_to(backend_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == "to_legacy_dict"
                    ):
                        class_methods.append(f"{rel}:{node.name}.{item.name}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "to_legacy_dict"
            ):
                call_sites.append(f"{rel}:{node.lineno}")

    assert class_methods == []
    assert call_sites == []


def test_save_pipeline_result_outcome_is_typed_internal_boundary(monkeypatch):
    calls: dict[str, object] = {}

    def _save_streaming(**kwargs):
        calls.update(kwargs)
        return "res-typed"

    monkeypatch.setattr(
        "app.core.results.streaming_blob_writer.save_dataframe_to_result_blob_streaming",
        _save_streaming,
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
    assert calls["output_format"] == "csv"


def test_save_pipeline_result_public_wrapper_is_explicit_legacy_dict(monkeypatch):
    import app.core.results.pipeline_result as pipeline_result

    monkeypatch.setattr(
        pipeline_result,
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
    import app.core.results.store as results_store
    import app.core.results.blob_writer as result_blob_writer
    from types import SimpleNamespace

    monkeypatch.setattr(
        result_blob_writer,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-rb2"),
    )
    monkeypatch.setattr(
        results_store,
        "resolve_group_id_for_result",
        lambda project_id, group_id=None: group_id or "grp-1",
    )
    # Forza policy=object_storage cosi' upload S3 e' obbligatorio.
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")


@legacy_path_result_blob
def test_save_result_blob_aws_credentials_expired_raises_operational_message(
    monkeypatch, tmp_path
):
    """Quando lo storage solleva LoginRefreshRequired (SSO scaduto),
    save_result_blob deve far fallire il job con un messaggio che dica
    all'operatore di rinnovare le credenziali. Niente fallback a DB."""
    from botocore.exceptions import LoginRefreshRequired


    _force_object_storage(monkeypatch, tmp_path)

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    result_file = temp_dir / "result.csv"
    result_file.write_text("id\n1\n", encoding="utf-8")

    class _ExpiredStorage:
        def upload(self, path, key):
            raise LoginRefreshRequired()

    monkeypatch.setattr(
        "app.core.io.object_storage.get_runtime_object_storage",
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


@legacy_path_result_blob
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
        "app.core.io.object_storage.get_runtime_object_storage",
        lambda: _NoCredsStorage(),
    )

    with pytest.raises(ResultSaveError, match="AWS credentials"):
        save_result_blob("proj-1", result_file, "csv", rows_count=1)


@legacy_path_result_blob
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
        "app.core.io.object_storage.get_runtime_object_storage",
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
    import app.core.results.store as results_store
    import app.core.results.blob_writer as result_blob_writer

    monkeypatch.setattr(
        result_blob_writer,
        "uuid",
        SimpleNamespace(uuid4=lambda: "res-os-1"),
    )
    monkeypatch.setattr(
        results_store,
        "resolve_group_id_for_result",
        lambda project_id, group_id=None: group_id or "grp-1",
    )
    monkeypatch.setattr(results_store, "save_result_row",
        lambda **kwargs: saved.update(kwargs),
    )
    monkeypatch.setattr(result_blob_writer, "apply_results_retention_after_save", lambda pid: None)
    # policy=object_storage: dispatcher forza sempre object storage
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setattr(
        "app.core.io.object_storage.get_runtime_object_storage",
        lambda: fake_storage,
    )


# A1 batch — CASO 1 + 2: policy=object_storage forza l'upload runtime ANCHE
# per payload sotto threshold. Contratto centrale del batch: niente fallback
# BYTEA per i nuovi result quando la policy chiede object storage.


@legacy_path_result_blob
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


@legacy_path_result_blob
def test_save_result_blob_object_storage_capability_off_raises(
    monkeypatch, tmp_path
):
    """Con STORAGE_POLICY=object_storage e get_runtime_object_storage()
    che solleva ObjectStorageDisabledError (capability off / bucket
    mancante), il writer deve fallire esplicitamente. Nessun degrado
    silenzioso a BYTEA: il result non viene salvato e il file temp
    resta sul disco per recovery."""
    from app.infrastructure.storage.cloud_backends import ObjectStorageDisabledError

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
        "app.core.io.object_storage.get_runtime_object_storage",
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
