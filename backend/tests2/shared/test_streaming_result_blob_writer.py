"""
Contract test — `app.core.results.streaming_blob_writer.StreamingResultBlobWriter`.

QUERY-1: writer file-like che streama result blob direttamente verso:
  - S3 multipart upload (`create_multipart_upload` + `upload_part` + `complete`)
  - DB BYTEA buffer (cap RESULT_DB_HARD_CAP_BYTES enforced per-write)

Coverage:
  - DB mode: write/finalize → save_result_row con data=bytes corretti.
  - DB mode: cap enforcement mid-stream → ResultSaveError.
  - S3 mode: multipart lifecycle (create → parts → complete).
  - S3 mode: empty result → abort + put_object empty.
  - S3 mode: write error → abort_multipart_upload.
  - S3 mode: abort idempotente.
  - rows_count update propaga in save_result_row.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# ── DB MODE ──────────────────────────────────────────────────────────


@pytest.fixture
def db_mode_env(monkeypatch):
    """Patch storage_policy=db + store.save_result_row + intent helpers."""
    from app.core.results import streaming_blob_writer as mod

    monkeypatch.setattr(mod, "storage_policy", lambda: "db")
    monkeypatch.setattr(mod, "result_db_hard_cap", lambda: 10_000)

    save_calls: list[dict] = []

    def _fake_save(**kwargs):
        save_calls.append(kwargs)

    monkeypatch.setattr(mod.store, "save_result_row", _fake_save)
    monkeypatch.setattr(
        mod.store, "resolve_group_id_for_result",
        lambda pid, gid: gid or "grp-default",
    )
    monkeypatch.setattr(mod, "apply_results_retention", lambda pid: None)
    return save_calls


def test_db_mode_write_finalize_persists_buffer(db_mode_env):
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    w = StreamingResultBlobWriter(
        project_id="proj-1", filename="out.csv", fmt="csv", group_id="grp-1",
    )
    w.write(b"id,name\n")
    w.write(b"1,roma\n")
    w.write(b"2,milano\n")
    w.update_rows_count(2)
    rid = w.finalize()

    assert rid == w.result_id
    assert len(db_mode_env) == 1
    saved = db_mode_env[0]
    assert saved["project_id"] == "proj-1"
    assert saved["group_id"] == "grp-1"
    assert saved["filename"] == "out.csv"
    assert saved["fmt"] == "csv"
    assert saved["data"] == b"id,name\n1,roma\n2,milano\n"
    assert saved["rows_count"] == 2


def test_db_mode_cap_exceeded_mid_stream_raises(db_mode_env, monkeypatch):
    """Write oltre RESULT_DB_HARD_CAP_BYTES -> ResultSaveError, niente save."""
    from app.core.results.blob_writer import ResultSaveError
    from app.core.results import streaming_blob_writer as mod
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    monkeypatch.setattr(mod, "result_db_hard_cap", lambda: 20)

    w = StreamingResultBlobWriter(
        project_id="p", filename="big.csv", fmt="csv",
    )
    w.write(b"x" * 15)
    with pytest.raises(ResultSaveError, match="RESULT_DB_HARD_CAP_BYTES"):
        w.write(b"y" * 10)
    # finalize NON deve essere chiamato post-error; nessun save_result_row.
    assert db_mode_env == []


def test_db_mode_abort_does_not_save(db_mode_env):
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    w = StreamingResultBlobWriter(project_id="p", filename="x.csv", fmt="csv")
    w.write(b"some bytes")
    w.abort()
    # abort idempotente + nessun save.
    w.abort()
    assert db_mode_env == []


def test_db_mode_tell_tracks_total_bytes(db_mode_env):
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    w = StreamingResultBlobWriter(project_id="p", filename="x.csv", fmt="csv")
    assert w.tell() == 0
    w.write(b"abc")
    assert w.tell() == 3
    w.write(b"defg")
    assert w.tell() == 7


def test_db_mode_double_finalize_raises(db_mode_env):
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    w = StreamingResultBlobWriter(project_id="p", filename="x.csv", fmt="csv")
    w.write(b"data")
    w.finalize()
    with pytest.raises(RuntimeError, match="finalizzato|closed"):
        w.finalize()


def test_db_mode_context_manager_aborts_on_exception(db_mode_env):
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    with pytest.raises(ValueError, match="consumer error"):
        with StreamingResultBlobWriter(
            project_id="p", filename="x.csv", fmt="csv",
        ) as w:
            w.write(b"partial data")
            raise ValueError("consumer error")
    # No save_result_row chiamato.
    assert db_mode_env == []


# ── S3 MODE ──────────────────────────────────────────────────────────


class _FakeS3Client:
    def __init__(self):
        self.calls: list[tuple] = []
        self.upload_id = "fake-upload-id"
        self._part_etag = 1

    def create_multipart_upload(self, **kw):
        self.calls.append(("create_multipart_upload", kw))
        return {"UploadId": self.upload_id}

    def upload_part(self, **kw):
        self.calls.append(("upload_part", kw))
        etag = f'"etag-{self._part_etag}"'
        self._part_etag += 1
        return {"ETag": etag}

    def complete_multipart_upload(self, **kw):
        self.calls.append(("complete_multipart_upload", kw))
        return {}

    def abort_multipart_upload(self, **kw):
        self.calls.append(("abort_multipart_upload", kw))
        return {}

    def put_object(self, **kw):
        self.calls.append(("put_object", kw))
        return {}


class _FakeS3Backend:
    def __init__(self, client: _FakeS3Client):
        self.bucket = "test-bucket"
        self._fake_client = client

    def _client(self):
        return self._fake_client

    def _extra_args(self):
        return {}


@pytest.fixture
def s3_mode_env(monkeypatch):
    """Patch storage_policy=object_storage + fake boto3 client + store/intent."""
    from app.core.results import streaming_blob_writer as mod

    monkeypatch.setattr(mod, "storage_policy", lambda: "object_storage")

    save_calls: list[dict] = []

    def _fake_save(**kwargs):
        save_calls.append(kwargs)

    monkeypatch.setattr(mod.store, "save_result_row", _fake_save)
    monkeypatch.setattr(
        mod.store, "resolve_group_id_for_result",
        lambda pid, gid: gid or "grp-default",
    )
    monkeypatch.setattr(mod, "apply_results_retention", lambda pid: None)
    monkeypatch.setattr(mod, "record_result_upload_intent", lambda *a, **kw: "intent-id-1")
    monkeypatch.setattr(mod, "close_result_upload_intent", lambda iid: None)
    monkeypatch.setattr(mod, "close_failed_result_upload_intent", lambda *a, **kw: None)

    client = _FakeS3Client()
    backend = _FakeS3Backend(client)
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: backend,
    )
    return client, save_calls


def test_s3_mode_writes_create_then_parts_then_complete(s3_mode_env, monkeypatch):
    """Stream con piu' part: create_multipart_upload + N upload_part +
    complete_multipart_upload con ETags raccolti."""
    from app.core.results import streaming_blob_writer as mod
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    # PART_SIZE piccolo per il test, per non scrivere MB di fake data.
    monkeypatch.setattr(mod, "PART_SIZE", 10)

    client, save_calls = s3_mode_env
    w = StreamingResultBlobWriter(
        project_id="proj-1", filename="r.parquet", fmt="parquet", group_id="g1",
    )
    # 25 bytes -> 2 part complete (10+10) + 1 final (5).
    w.write(b"a" * 25)
    w.update_rows_count(100)
    w.finalize()

    op_sequence = [op for op, _ in client.calls]
    assert op_sequence == [
        "create_multipart_upload",
        "upload_part",
        "upload_part",
        "upload_part",
        "complete_multipart_upload",
    ]
    # Parts collezionati in ordine.
    complete_kw = client.calls[-1][1]
    parts = complete_kw["MultipartUpload"]["Parts"]
    assert [p["PartNumber"] for p in parts] == [1, 2, 3]
    assert [p["ETag"] for p in parts] == ['"etag-1"', '"etag-2"', '"etag-3"']
    # DB row scritta con storage_key + size_bytes.
    assert len(save_calls) == 1
    saved = save_calls[0]
    assert saved["data"] is None
    assert saved["storage_key"].endswith(f"{w.result_id}.bin")
    assert saved["size_bytes"] == 25
    assert saved["rows_count"] == 100


def test_s3_mode_empty_stream_uses_put_object_empty(s3_mode_env):
    """Zero parts (es. query con 0 righe + zero CSV bytes) -> abort
    multipart + put_object(Body=b"")."""
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    client, save_calls = s3_mode_env
    w = StreamingResultBlobWriter(project_id="p", filename="empty.csv", fmt="csv")
    w.finalize()

    ops = [op for op, _ in client.calls]
    assert "abort_multipart_upload" in ops
    assert "put_object" in ops
    assert "complete_multipart_upload" not in ops
    assert len(save_calls) == 1
    assert save_calls[0]["size_bytes"] == 0


def test_s3_mode_abort_aborts_multipart(s3_mode_env, monkeypatch):
    """abort() -> abort_multipart_upload + niente complete + niente save."""
    from app.core.results import streaming_blob_writer as mod
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    monkeypatch.setattr(mod, "PART_SIZE", 10)
    client, save_calls = s3_mode_env

    w = StreamingResultBlobWriter(project_id="p", filename="x.csv", fmt="csv")
    w.write(b"a" * 15)  # 1 part flushed.
    w.abort()

    ops = [op for op, _ in client.calls]
    assert ops == ["create_multipart_upload", "upload_part", "abort_multipart_upload"]
    assert save_calls == []
    # Abort idempotente.
    w.abort()
    ops_after = [op for op, _ in client.calls]
    assert ops_after == ops  # niente operazioni nuove


def test_s3_mode_write_part_error_aborts_multipart(s3_mode_env, monkeypatch):
    """upload_part fallisce -> abort multipart + propaga exception."""
    from app.core.results import streaming_blob_writer as mod
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter
    from app.core.results.blob_writer import ResultSaveError

    monkeypatch.setattr(mod, "PART_SIZE", 10)
    client, save_calls = s3_mode_env

    call_count = {"n": 0}
    orig_upload = client.upload_part

    def _flaky_upload(**kw):
        call_count["n"] += 1
        if call_count["n"] >= 1:
            raise RuntimeError("S3 part upload failed")
        return orig_upload(**kw)

    client.upload_part = _flaky_upload

    w = StreamingResultBlobWriter(project_id="p", filename="x.csv", fmt="csv")
    with pytest.raises(ResultSaveError, match="Upload object storage fallito"):
        w.write(b"x" * 15)  # triggera upload_part che fallisce.

    ops = [op for op, _ in client.calls]
    assert "create_multipart_upload" in ops
    assert "abort_multipart_upload" in ops
    assert "complete_multipart_upload" not in ops
    assert save_calls == []


def test_s3_mode_context_manager_aborts_on_exception(s3_mode_env, monkeypatch):
    from app.core.results import streaming_blob_writer as mod
    from app.core.results.streaming_blob_writer import StreamingResultBlobWriter

    monkeypatch.setattr(mod, "PART_SIZE", 10)
    client, save_calls = s3_mode_env

    with pytest.raises(ValueError):
        with StreamingResultBlobWriter(
            project_id="p", filename="x.csv", fmt="csv",
        ) as w:
            w.write(b"x" * 15)
            raise ValueError("consumer error")

    ops = [op for op, _ in client.calls]
    assert "abort_multipart_upload" in ops
    assert "complete_multipart_upload" not in ops
    assert save_calls == []
