"""Contract test — app/core/eseguibili/storage.py (S3 multi-host).

Copre:
1. save() carica il file e lo cancella localmente
2. save() NON cancella il locale se l'upload fallisce (retry-friendly)
3. find_keys() usa il prefix corretto
4. open_stream() delega al backend
5. delete_by_task() e' idempotente (no error se niente)
6. Protocol conformance: l'API fa quello che il contract dice
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]


class _FakeStorage:
    """Stub del Protocol ObjectStorage per test."""
    def __init__(self):
        self.uploads = []
        self.deletes = []
        self.listed_prefix = None
        self.list_response = []
        self.stream_response = {"body": iter([b"chunk1"]), "size": 6, "content_type": "text/plain"}
        self.upload_should_fail = False

    def list_objects(self, prefix):
        self.listed_prefix = prefix
        return list(self.list_response)

    def upload(self, local_path, key):
        if self.upload_should_fail:
            raise RuntimeError("S3 upload failed")
        self.uploads.append((str(local_path), key))

    def upload_bytes(self, data, key):
        if self.upload_should_fail:
            raise RuntimeError("S3 upload failed")
        self.uploads.append((f"<bytes:{len(data)}>", key))

    def download(self, key, local_path):
        raise NotImplementedError

    def get_object_stream(self, key):
        return dict(self.stream_response)

    def delete(self, key):
        self.deletes.append(key)


@pytest.fixture
def fake_storage(monkeypatch):
    fake = _FakeStorage()
    from app.core.eseguibili import storage as storage_mod
    monkeypatch.setattr(storage_mod, "_storage", lambda: fake)
    return fake


# =============================================================================
# save()
# =============================================================================


def test_save_uploads_and_deletes_local(fake_storage, tmp_path):
    from app.core.eseguibili import storage as st

    local = tmp_path / "output.csv"
    local.write_text("col1,col2\n1,2\n")

    key = st.save("task-123", local)

    assert key == "eseguibili-outputs/task-123/output.csv"
    assert len(fake_storage.uploads) == 1
    assert fake_storage.uploads[0] == (str(local), key)
    # File locale cancellato
    assert not local.exists()


def test_save_cleans_empty_parent_dir(fake_storage, tmp_path):
    """La parent dir temp (es. urby_<pid>_<hash>) viene rimossa se vuota."""
    from app.core.eseguibili import storage as st

    parent = tmp_path / "urby_12345_abcd"
    parent.mkdir()
    local = parent / "output.csv"
    local.write_text("data")

    st.save("task-1", local)

    assert not local.exists()
    assert not parent.exists()  # dir vuota -> rimossa


def test_save_keeps_parent_if_not_empty(fake_storage, tmp_path):
    """Parent con altri file NON viene rimossa (side effect evitato)."""
    from app.core.eseguibili import storage as st

    parent = tmp_path / "urby_12345_abcd"
    parent.mkdir()
    local = parent / "output.csv"
    local.write_text("data")
    sibling = parent / "other.txt"
    sibling.write_text("keep")

    st.save("task-1", local)

    assert not local.exists()
    assert parent.exists()  # ancora li'
    assert sibling.exists()


def test_save_does_not_delete_local_if_upload_fails(fake_storage, tmp_path):
    """Se upload fallisce, il file locale resta per retry manuale."""
    from app.core.eseguibili import storage as st

    fake_storage.upload_should_fail = True
    local = tmp_path / "output.csv"
    local.write_text("data")

    with pytest.raises(RuntimeError, match="upload failed"):
        st.save("task-1", local)

    assert local.exists()  # preservato


def test_save_rejects_non_existent_file(fake_storage, tmp_path):
    from app.core.eseguibili import storage as st

    missing = tmp_path / "nope.csv"
    with pytest.raises(FileNotFoundError):
        st.save("task-1", missing)


def test_save_rejects_directory(fake_storage, tmp_path):
    from app.core.eseguibili import storage as st

    d = tmp_path / "adir"
    d.mkdir()
    with pytest.raises(ValueError, match="non e' un file"):
        st.save("task-1", d)


# =============================================================================
# find_keys()
# =============================================================================


def test_find_keys_uses_task_prefix(fake_storage):
    from app.core.eseguibili import storage as st

    fake_storage.list_response = [
        {"key": "eseguibili-outputs/task-42/foo.csv", "last_modified": None, "size": 100},
        {"key": "eseguibili-outputs/task-42/bar.xlsx", "last_modified": None, "size": 200},
    ]
    keys = st.find_keys("task-42")

    assert fake_storage.listed_prefix == "eseguibili-outputs/task-42/"
    assert keys == [
        "eseguibili-outputs/task-42/foo.csv",
        "eseguibili-outputs/task-42/bar.xlsx",
    ]


def test_find_keys_returns_empty_when_no_match(fake_storage):
    from app.core.eseguibili import storage as st

    fake_storage.list_response = []
    assert st.find_keys("task-unknown") == []


# =============================================================================
# open_stream()
# =============================================================================


def test_open_stream_delegates_to_backend(fake_storage):
    from app.core.eseguibili import storage as st

    fake_storage.stream_response = {
        "body": iter([b"abc", b"def"]),
        "size": 6,
        "content_type": "text/csv",
    }
    result = st.open_stream("eseguibili-outputs/task-1/output.csv")

    assert result["size"] == 6
    assert result["content_type"] == "text/csv"
    # body consumable
    chunks = list(result["body"])
    assert chunks == [b"abc", b"def"]


# =============================================================================
# delete_by_task()
# =============================================================================


def test_delete_by_task_removes_all_keys(fake_storage):
    from app.core.eseguibili import storage as st

    fake_storage.list_response = [
        {"key": "eseguibili-outputs/task-1/a.csv", "last_modified": None, "size": 0},
        {"key": "eseguibili-outputs/task-1/b.xlsx", "last_modified": None, "size": 0},
    ]
    count = st.delete_by_task("task-1")

    assert count == 2
    assert set(fake_storage.deletes) == {
        "eseguibili-outputs/task-1/a.csv",
        "eseguibili-outputs/task-1/b.xlsx",
    }


def test_delete_by_task_idempotent_when_empty(fake_storage):
    from app.core.eseguibili import storage as st

    fake_storage.list_response = []
    assert st.delete_by_task("task-missing") == 0
    assert fake_storage.deletes == []


def test_delete_by_task_continues_if_one_delete_fails(fake_storage):
    """Se un delete fallisce, gli altri procedono (best effort)."""
    from app.core.eseguibili import storage as st

    fake_storage.list_response = [
        {"key": "eseguibili-outputs/task-1/a.csv", "last_modified": None, "size": 0},
        {"key": "eseguibili-outputs/task-1/b.xlsx", "last_modified": None, "size": 0},
    ]

    real_delete = fake_storage.delete
    call_count = [0]

    def flaky_delete(key):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("S3 delete failed")
        real_delete(key)

    fake_storage.delete = flaky_delete
    count = st.delete_by_task("task-1")

    # count riflette le key trovate, non i delete riusciti (best-effort)
    assert count == 2
    # Il secondo delete e' comunque avvenuto
    assert len(fake_storage.deletes) == 1
