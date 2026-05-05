"""Path-mode temp-safety guard for ``ResultBlobLoader``.

BE-GO-READY-RESOURCE-ENGINE-006-PATH-MODE-TEMP-SAFETY-GUARD.

Locks the ``mode='path'`` tempfile-materialization safety
invariants:

  1. **Identifiable prefix**: every file produced by
     ``_materialize_to_temp_path(...)`` is named with the explicit
     ``plenora-result-blob-`` prefix. Operator audits of the
     tempdir + cleanup helpers can identify the engine's files.

  2. **No leak on write failure**: when ``tf.write(...)`` raises,
     the partial tempfile is closed AND unlinked best-effort
     before the original exception is re-raised. The tempdir must
     not contain orphaned ``plenora-result-blob-*`` files after a
     write-failure path.

  3. **Cleanup is path-bounded**: ``cleanup_temp_path(path)``
     refuses to delete anything that does NOT
     (a) carry the ``plenora-result-blob-`` prefix on its basename,
     AND (b) resolve to a parent inside the stdlib tempfile root.
     Defense-in-depth: a wrong-prefix file outside the tempdir,
     a right-prefix file outside the tempdir, and a wrong-prefix
     file inside the tempdir all fail the guard.

  4. **`is_temporary=True` documents caller cleanup contract**:
     the loader's path-mode return shape carries
     ``Resource(is_temporary=True)`` and the docstring must
     explicitly name this responsibility. Source-bound check on
     the loader docstring + the helper's docstring.

Anti-god-engine: tests are stdlib-only (``tempfile`` + ``pathlib``
+ ``unittest.mock``); no FastAPI, no DB, no Redis.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# ---------------------------------------------------------------------------
# 1. Prefix invariant
# ---------------------------------------------------------------------------


def test_materialize_to_temp_path_uses_plenora_prefix():
    """Every materialized tempfile must carry the
    ``plenora-result-blob-`` prefix in its basename."""
    from app.core.resources.loaders.result_blob import (
        _RESULT_BLOB_TEMP_PREFIX,
        _materialize_to_temp_path,
        cleanup_temp_path,
    )

    path = _materialize_to_temp_path(b"hello world")
    try:
        assert path.exists(), "materialized tempfile must exist on disk"
        assert path.name.startswith(_RESULT_BLOB_TEMP_PREFIX), (
            f"basename {path.name!r} must start with the engine "
            f"prefix {_RESULT_BLOB_TEMP_PREFIX!r}"
        )
        assert _RESULT_BLOB_TEMP_PREFIX == "plenora-result-blob-", (
            "Prefix value is a stable contract; changing it is a "
            "behaviour change visible to operators / cleanup helpers."
        )
        assert path.read_bytes() == b"hello world"
    finally:
        cleanup_temp_path(path)


def test_materialize_writes_into_stdlib_tempdir():
    """Files land in `tempfile.gettempdir()` (or a subdir thereof).
    The engine does NOT honor a domain-specific TEMP_DIR; the
    stdlib root is the only allowed location."""
    from app.core.resources.loaders.result_blob import (
        _materialize_to_temp_path,
        cleanup_temp_path,
    )

    path = _materialize_to_temp_path(b"x")
    try:
        tempdir = Path(tempfile.gettempdir()).resolve()
        assert path.resolve().parent == tempdir or (
            tempdir in path.resolve().parents
        ), (
            f"materialized path {path!r} must live under the stdlib "
            f"tempdir {tempdir!r}"
        )
    finally:
        cleanup_temp_path(path)


# ---------------------------------------------------------------------------
# 2. No leak on write failure
# ---------------------------------------------------------------------------


def test_materialize_unlinks_partial_on_write_failure():
    """When ``tf.write(...)`` raises (e.g. disk-full / EROFS), the
    partial tempfile MUST be unlinked. The tempdir must not be left
    with an orphaned ``plenora-result-blob-*`` file."""
    from app.core.resources.loaders import result_blob

    # Snapshot the pre-call set of plenora-result-blob-* files.
    tempdir = Path(tempfile.gettempdir())
    before = {
        p.name for p in tempdir.iterdir()
        if p.is_file() and p.name.startswith(result_blob._RESULT_BLOB_TEMP_PREFIX)
    }

    class _BoomWriter(io_block := type("io_block", (), {})):
        pass

    captured_path: dict = {}
    real_named_tempfile = tempfile.NamedTemporaryFile

    class _BoomTempFile:
        def __init__(self, *args, **kwargs):
            self._real = real_named_tempfile(*args, **kwargs)
            self.name = self._real.name
            captured_path["path"] = Path(self.name)

        def write(self, _data):
            raise OSError("simulated EROFS / disk-full mid-write")

        def close(self):
            return self._real.close()

    with patch.object(
        result_blob.tempfile,
        "NamedTemporaryFile",
        _BoomTempFile,
    ):
        with pytest.raises(OSError, match="simulated EROFS"):
            result_blob._materialize_to_temp_path(b"payload")

    # The captured path's exact tempfile MUST be unlinked.
    assert "path" in captured_path, "tempfile was never created"
    assert not captured_path["path"].exists(), (
        f"partial tempfile {captured_path['path']!r} must be "
        "unlinked after write failure (no leaked half-written file)"
    )

    # And the tempdir as a whole must not gain a new
    # plenora-result-blob-* file (defense in depth: even if a future
    # implementation skipped the captured-path branch, the global
    # snapshot should not grow).
    after = {
        p.name for p in tempdir.iterdir()
        if p.is_file() and p.name.startswith(result_blob._RESULT_BLOB_TEMP_PREFIX)
    }
    new_files = after - before
    assert not new_files, (
        f"write failure leaked plenora-result-blob-* files: {sorted(new_files)}"
    )


# ---------------------------------------------------------------------------
# 3. Cleanup helper is path-bounded
# ---------------------------------------------------------------------------


def test_cleanup_refuses_path_without_prefix(tmp_path):
    """A file in the tempdir but NOT bearing the engine's prefix
    must NOT be deleted by ``cleanup_temp_path``."""
    from app.core.resources.loaders.result_blob import cleanup_temp_path

    # Create a wrong-prefix file inside the stdlib tempdir.
    tempdir = Path(tempfile.gettempdir())
    intruder = tempdir / "not-the-engine-prefix.txt"
    intruder.write_bytes(b"sensitive")
    try:
        result = cleanup_temp_path(intruder)
        assert result is False, (
            "cleanup_temp_path must refuse paths missing the "
            "plenora-result-blob- prefix (defense in depth: an "
            "accidental cleanup_temp_path('/etc/passwd') must not "
            "destroy arbitrary files)"
        )
        assert intruder.exists(), (
            f"intruder file {intruder!r} must still exist after the "
            "cleanup refusal"
        )
    finally:
        if intruder.exists():
            intruder.unlink()


def test_cleanup_refuses_path_outside_tempdir(tmp_path):
    """A right-prefix file OUTSIDE the stdlib tempdir must NOT be
    deleted. Both guards (prefix AND tempdir) are required."""
    from app.core.resources.loaders.result_blob import (
        _RESULT_BLOB_TEMP_PREFIX,
        cleanup_temp_path,
    )

    # Create a right-prefix file outside the tempdir (in pytest's
    # tmp_path which is a separate area). On Windows pytest's
    # tmp_path is usually under the user temp BUT pytest manages it
    # under a subdir like `pytest-of-USER/pytest-N/test_X`. Skip the
    # test if pytest's tmp_path resolves under the system tempdir
    # (which would defeat the test premise on this platform).
    real_tempdir = Path(tempfile.gettempdir()).resolve()
    decoy_dir = tmp_path.resolve()
    try:
        decoy_dir.relative_to(real_tempdir)
        # tmp_path IS under the system tempdir. Use a different
        # location: the backend repo root (always outside the system
        # tempdir on every supported platform).
        repo_root = Path(__file__).resolve().parents[3]
        decoy_dir = repo_root / ".agent-temp"
        decoy_dir.mkdir(parents=True, exist_ok=True)
    except ValueError:
        # tmp_path is NOT under the system tempdir — already a safe
        # decoy location.
        pass

    decoy = decoy_dir / f"{_RESULT_BLOB_TEMP_PREFIX}intruder.bin"
    decoy.write_bytes(b"sensitive")
    try:
        result = cleanup_temp_path(decoy)
        assert result is False, (
            "cleanup_temp_path must refuse paths outside the stdlib "
            "tempdir, even when the prefix matches (defense in depth: "
            "a drifted/malicious prefix on a non-tempdir path must "
            "not enable arbitrary deletion)"
        )
        assert decoy.exists(), (
            f"decoy file {decoy!r} must still exist after refusal"
        )
    finally:
        if decoy.exists():
            decoy.unlink()


def test_cleanup_succeeds_on_engine_owned_path():
    """A file with both invariants (prefix + tempdir parent) must
    be deletable by the helper. Idempotent: a second call returns
    ``False`` because the file no longer exists."""
    from app.core.resources.loaders.result_blob import (
        _materialize_to_temp_path,
        cleanup_temp_path,
    )

    path = _materialize_to_temp_path(b"engine-owned")
    assert path.exists()

    first = cleanup_temp_path(path)
    assert first is True, (
        "cleanup_temp_path must succeed on a file produced by "
        "_materialize_to_temp_path"
    )
    assert not path.exists(), (
        f"file {path!r} must be unlinked after cleanup"
    )

    # Idempotent: calling again returns False (file already gone),
    # without raising.
    second = cleanup_temp_path(path)
    assert second is False


def test_cleanup_never_raises_on_filesystem_errors(tmp_path):
    """The helper is designed for cleanup hooks: it must NEVER
    raise. Even on a non-existent path with a valid prefix, it
    returns False rather than propagating an exception."""
    from app.core.resources.loaders.result_blob import (
        _RESULT_BLOB_TEMP_PREFIX,
        cleanup_temp_path,
    )

    # Path that has the right prefix but does not exist.
    ghost = Path(tempfile.gettempdir()) / f"{_RESULT_BLOB_TEMP_PREFIX}ghost.bin"
    if ghost.exists():
        ghost.unlink()

    # Must not raise.
    result = cleanup_temp_path(ghost)
    assert result is False


# ---------------------------------------------------------------------------
# 4. is_temporary=True contract — docstring lock
# ---------------------------------------------------------------------------


def test_loader_path_mode_returns_is_temporary_true():
    """End-to-end sanity: path-mode load returns
    ``Resource(is_temporary=True)`` so the caller knows it owns
    cleanup."""
    from app.core.resources.loaders.result_blob import (
        ResultBlobLoader,
        cleanup_temp_path,
    )
    from app.core.resources.types import ResourceRef

    loader = ResultBlobLoader()
    ref = ResourceRef(
        kind="result_blob",
        result_id="r-1",
        expected_group_id="g-1",
    )

    # Mock the dispatch to short-circuit the DB / object-storage
    # path; we want to test ONLY the materialization + Resource
    # shape contract.
    captured: list = []

    def _fake_dispatch(result_id, *, group_id_filter, project_id_filter=None):
        captured.append((result_id, group_id_filter))
        return b"engine-owned-bytes", "db_legacy"

    with patch(
        "app.core.resources.loaders.result_blob._dispatch_by_id",
        _fake_dispatch,
    ):
        resource = loader.load(ref, mode="path")

    try:
        assert resource.is_temporary is True, (
            "Resource.is_temporary MUST be True in path mode — it "
            "signals to the caller that they own cleanup."
        )
        assert resource.path is not None
        assert resource.path.exists()
        assert resource.path.name.startswith("plenora-result-blob-")
        assert resource.size_bytes == len(b"engine-owned-bytes")
        assert resource.kind == "result_blob"
    finally:
        cleanup_temp_path(resource.path)


def test_loader_docstring_documents_caller_cleanup_responsibility():
    """Source-bound: the loader's ``_materialize_to_temp_path``
    docstring AND ``cleanup_temp_path`` docstring must explicitly
    name the caller-owned cleanup contract.

    Without this lock, a future refactor could remove the
    documentation and silently strand callers."""
    import inspect

    from app.core.resources.loaders.result_blob import (
        _materialize_to_temp_path,
        cleanup_temp_path,
    )

    materialize_doc = inspect.getdoc(_materialize_to_temp_path) or ""
    assert "is_temporary=True" in materialize_doc, (
        "_materialize_to_temp_path docstring must reference "
        "Resource(is_temporary=True) caller-cleanup contract"
    )
    assert "cleanup" in materialize_doc.lower(), (
        "_materialize_to_temp_path docstring must mention cleanup"
    )

    cleanup_doc = inspect.getdoc(cleanup_temp_path) or ""
    assert "is_temporary=True" in cleanup_doc, (
        "cleanup_temp_path docstring must reference the "
        "Resource(is_temporary=True) caller contract"
    )
    assert "plenora-result-blob-" in cleanup_doc, (
        "cleanup_temp_path docstring must reference the prefix guard"
    )


# ---------------------------------------------------------------------------
# 5. Path mode stays fallback-only — bytes mode is the default for new
#    consumers. Source-bound check that bytes mode is documented as
#    the default surface on the loader.
# ---------------------------------------------------------------------------


def test_path_mode_documented_as_fallback_in_module_docstring():
    """The module docstring (or the ``_materialize_to_temp_path``
    section) must describe path mode as fallback-only."""
    from app.core.resources.loaders import result_blob

    src_path = Path(result_blob.__file__)
    text = src_path.read_text(encoding="utf-8")
    # The fallback-only guidance must appear somewhere in the
    # module's source documentation. Stable substring check.
    assert "FALLBACK-ONLY" in text or "fallback-only" in text.lower(), (
        "result_blob.py must document path mode as fallback-only "
        "(section comment or docstring). Default for new consumers "
        "is bytes mode (in-memory, no tempfile)."
    )
