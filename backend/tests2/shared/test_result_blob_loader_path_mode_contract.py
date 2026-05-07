"""
Path-mode contract for ``ResultBlobLoader``.

BE-GO-READY-RESOURCE-ENGINE-004-RESULT-BLOB-PATH-MODE.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# BE-GO-READY-TEST-INFRA-003: shared helpers extracted to a sibling
# module. ResourceRef construction and Resource path cleanup are
# now deduplicated across the four resource-loader contract suites.
from tests2.shared._resource_loader_helpers import (
    cleanup_resource_path,
    fake_dispatch_by_id,
    make_result_ref,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _REPO_ROOT / "backend"
_RESULT_BLOB_PY = (
    _BACKEND_ROOT / "app" / "core" / "resources" / "loaders" / "result_blob.py"
)


# 1. Mode acceptance


def test_path_mode_is_now_supported():
    """``ResultBlobLoader.load(..., mode='path')`` MUST NOT raise the
    'not supported' guard (it raised in 002; 004 lifted it)."""
    from app.core.resources.loaders import result_blob as loader_mod

    ref = make_result_ref()

    payload = b"hello-path-mode"
    captured, fake = fake_dispatch_by_id(payload=payload, backend="db_legacy")

    with patch.object(loader_mod, "_dispatch_by_id", fake):
        out = loader_mod.ResultBlobLoader().load(ref, mode="path")

    try:
        assert isinstance(out.path, Path)
        assert out.path.is_file(), (
            f"path-mode Resource must point to an existing file, got "
            f"{out.path!r}"
        )
        assert out.size_bytes == len(payload)
        assert out.kind == "result_blob"
        assert out.is_temporary is True
        assert out.data is None
        assert out.stream is None
        # The materialized file MUST contain exactly the dispatched bytes.
        assert out.path.read_bytes() == payload
    finally:
        cleanup_resource_path(out)


def test_stream_mode_remains_unsupported():
    """Defense against a future drift: ``stream`` MUST still raise."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    ref = make_result_ref()
    with pytest.raises(ValueError, match="not supported"):
        ResultBlobLoader().load(ref, mode="stream")  # type: ignore[arg-type]


# 2. Single-dispatch invariant (bytes + path share lookup/filter/SQL)


def test_path_mode_uses_same_dispatch_function_as_bytes_mode():
    """Both modes call ``_dispatch_by_id`` exactly once. There is NO
    second SQL query, no second tenant check, no parallel dispatch
    path for path mode -- the mode only affects materialization.
    """
    from app.core.resources.loaders import result_blob as loader_mod

    ref = make_result_ref()

    calls: list[tuple] = []

    def _counting_dispatch(result_id, *, group_id_filter, project_id_filter=None):
        calls.append((result_id, group_id_filter))
        return b"x", "db_legacy"

    with patch.object(loader_mod, "_dispatch_by_id", _counting_dispatch):
        out = loader_mod.ResultBlobLoader().load(ref, mode="path")

    try:
        assert len(calls) == 1, (
            "path mode must reuse the same single dispatch site as "
            "bytes mode; got call_count="
            f"{len(calls)}"
        )
        assert calls[0] == ("r1", "g1")
    finally:
        cleanup_resource_path(out)


def test_path_mode_honors_superadmin_filter_skip(monkeypatch):
    """``is_superadmin=True`` skips the SQL group_id filter for path
    mode exactly as for bytes mode (group_id_filter=None).
    """
    from app.core.resources.loaders import result_blob as loader_mod

    ref = make_result_ref(is_superadmin=True)

    captured, fake = fake_dispatch_by_id()

    monkeypatch.setattr(loader_mod, "_dispatch_by_id", fake)
    out = loader_mod.ResultBlobLoader().load(ref, mode="path")

    try:
        assert captured["group_id_filter"] is None, (
            "is_superadmin=True must skip the SQL group_id filter "
            "for path mode (parity with bytes mode)"
        )
    finally:
        cleanup_resource_path(out)


def test_path_mode_passes_group_id_filter_when_not_superadmin(monkeypatch):
    """``is_superadmin=False`` -> SQL filter pinned to
    ``ref.expected_group_id``. Cross-tenant rows return no row;
    path mode then raises FileNotFoundError downstream (covered
    separately).
    """
    from app.core.resources.loaders import result_blob as loader_mod

    ref = make_result_ref(group_id="alpha")

    captured, fake = fake_dispatch_by_id(backend="object_storage")

    monkeypatch.setattr(loader_mod, "_dispatch_by_id", fake)
    out = loader_mod.ResultBlobLoader().load(ref, mode="path")

    try:
        assert captured["group_id_filter"] == "alpha", (
            "non-superadmin path mode must pass expected_group_id "
            "as the SQL filter (parity with bytes mode tenant binding)"
        )
    finally:
        cleanup_resource_path(out)


# 3. Failure paths


def test_path_mode_raises_file_not_found_on_missing_row():
    """A row that does not exist (or is not visible to the group)
    surfaces as ``FileNotFoundError`` -- NOT as a zero-byte tempfile.
    """
    from app.core.resources.loaders import result_blob as loader_mod

    ref = make_result_ref()

    _, fake = fake_dispatch_by_id(payload=None, backend="none")

    with patch.object(loader_mod, "_dispatch_by_id", fake):
        with pytest.raises(FileNotFoundError, match="not found"):
            loader_mod.ResultBlobLoader().load(ref, mode="path")


def test_path_mode_raises_file_not_found_when_blob_data_is_none():
    """Object storage download failure (``data=None`` with a row
    that exists, ``backend != 'none'``) MUST surface as
    ``FileNotFoundError`` rather than materialize a zero-byte
    tempfile that hides an outage from the caller.
    """
    from app.core.resources.loaders import result_blob as loader_mod

    ref = make_result_ref()

    # Row exists (storage_key returned object_storage backend),
    # but the download failed (None bytes).
    _, fake = fake_dispatch_by_id(payload=None, backend="object_storage")

    with patch.object(loader_mod, "_dispatch_by_id", fake):
        with pytest.raises(FileNotFoundError, match="blob data unavailable"):
            loader_mod.ResultBlobLoader().load(ref, mode="path")


def test_path_mode_cleans_up_partial_tempfile_on_write_failure(monkeypatch, tmp_path):
    """If ``tf.write(data)`` raises (disk full, EROFS, permission),
    the partial tempfile MUST be unlinked best-effort and the
    original exception re-raised. No leaked half-written file.
    """
    from app.core.resources.loaders import result_blob as loader_mod

    real_named_tempfile = loader_mod.tempfile.NamedTemporaryFile
    leaked_paths: list[str] = []

    class _ExplodingFile:
        def __init__(self, real):
            self._real = real
            self.name = real.name
            leaked_paths.append(real.name)

        def write(self, data):
            raise OSError("simulated disk full")

        def close(self):
            self._real.close()

    def _fake_named_tempfile(**kwargs):
        real = real_named_tempfile(**kwargs)
        return _ExplodingFile(real)

    monkeypatch.setattr(loader_mod.tempfile, "NamedTemporaryFile", _fake_named_tempfile)

    with pytest.raises(OSError, match="simulated disk full"):
        loader_mod._materialize_to_temp_path(b"x" * 10)

    # All leaked names must have been cleaned up.
    for p in leaked_paths:
        assert not Path(p).exists(), (
            f"_materialize_to_temp_path leaked tempfile {p!r} after "
            "write failure; cleanup hardening is broken"
        )


# 4. Anti-god-engine: stdlib-only tempfile (no app.config import)


def test_loader_module_does_not_import_app_config():
    """Source-bound: the loader module MUST NOT import
    ``app.config`` (would tie the engine to domain settings).
    The stdlib ``tempfile`` module honors ``TMPDIR``/``TEMP``/``TMP``
    env vars natively, so callers that need a custom tempdir can
    set those instead of teaching the engine about settings."""
    text = _RESULT_BLOB_PY.read_text(encoding="utf-8")

    forbidden = (
        "from app.config",
        "import app.config",
    )
    offenders = [n for n in forbidden if n in text]
    assert not offenders, (
        f"app/core/resources/loaders/result_blob.py imports {offenders}: "
        "the engine must not import app.config (anti-god-engine). "
        "stdlib tempfile already honors TMPDIR/TEMP/TMP env vars."
    )


def test_loader_module_uses_stdlib_tempfile_only():
    """Source-bound: the path-mode helper MUST use stdlib
    ``tempfile.NamedTemporaryFile``. A migration to a domain
    helper (``app.shared.upload_guards`` or similar) would couple
    the engine to higher-level layers."""
    text = _RESULT_BLOB_PY.read_text(encoding="utf-8")
    assert "tempfile.NamedTemporaryFile" in text, (
        "path-mode materialization must use stdlib "
        "tempfile.NamedTemporaryFile (anti-god-engine). Found neither "
        "the call nor an equivalent stdlib path."
    )
    assert "import tempfile" in text, (
        "tempfile import must be top-level (defense against an "
        "accidental local-import workaround)"
    )


def test_loader_path_mode_does_not_pull_settings_at_import_time():
    """Defense-in-depth: importing the loader package MUST NOT pull
    ``app.config`` into ``sys.modules``. A stray top-level import
    would surface here even if the source-bound grep missed an
    aliased form (``import app.config as cfg``)."""
    # Force a fresh import of the loader to make the snapshot
    # meaningful even if something else previously pulled it.
    for mod in list(sys.modules):
        if mod.startswith("app.core.resources.loaders.result_blob"):
            del sys.modules[mod]

    before = set(sys.modules.keys())
    import importlib

    importlib.import_module("app.core.resources.loaders.result_blob")
    new_modules = set(sys.modules.keys()) - before

    leaked = {m for m in new_modules if m == "app.config" or m.startswith("app.config.")}
    assert not leaked, (
        f"importing result_blob.py pulled forbidden modules into "
        f"sys.modules: {sorted(leaked)}. The engine must remain "
        "decoupled from domain settings."
    )


# 5. Resource envelope shape (path-mode specifics)


def test_path_mode_resource_envelope_has_path_only_no_data_no_stream():
    """Mode contract: ``Resource.data`` and ``Resource.stream`` MUST
    be None in path mode. ``Resource.path`` MUST be set. Mixing
    populated outputs would let callers consume the same payload
    twice in different shapes (size_bytes invariant breaks)."""
    from app.core.resources.loaders import result_blob as loader_mod

    ref = make_result_ref()

    _, fake = fake_dispatch_by_id(payload=b"abc", backend="db_legacy")

    with patch.object(loader_mod, "_dispatch_by_id", fake):
        out = loader_mod.ResultBlobLoader().load(ref, mode="path")

    try:
        assert out.data is None
        assert out.stream is None
        assert out.path is not None
        assert out.is_temporary is True
        assert out.size_bytes == 3
        assert out.format_hint is None  # engine does not parse format
    finally:
        cleanup_resource_path(out)


def test_path_mode_writes_distinct_tempfiles_per_call(monkeypatch):
    """Each ``load(..., mode='path')`` call writes a fresh tempfile;
    callers MUST NOT see the same path returned twice (would violate
    is_temporary=True ownership)."""
    from app.core.resources.loaders import result_blob as loader_mod

    ref = make_result_ref()

    monkeypatch.setattr(
        loader_mod,
        "_dispatch_by_id",
        lambda result_id, *, group_id_filter, project_id_filter=None: (b"data", "db_legacy"),
    )
    loader = loader_mod.ResultBlobLoader()

    out1 = loader.load(ref, mode="path")
    out2 = loader.load(ref, mode="path")

    try:
        assert out1.path != out2.path, (
            "path mode must allocate a fresh tempfile per call "
            "(is_temporary=True implies caller-owned cleanup; "
            "shared paths break that contract)"
        )
        assert out1.path.is_file()
        assert out2.path.is_file()
    finally:
        cleanup_resource_path(out1)
        cleanup_resource_path(out2)
