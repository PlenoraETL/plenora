"""Project-level binding contract for ``ResultBlobLoader``.

BE-GO-READY-RESOURCE-ENGINE-007-PROJECT-LEVEL-BINDING-AUDIT.

Locks the optional row-metadata project filter on the loader:

  1. ``ResourceRef.expected_project_id`` is OPTIONAL (default
     ``None``). Construction with no project_id continues to work
     for every existing caller — backward-compat.

  2. When ``expected_project_id`` is None, NO project filter is
     applied. The SQL is identical to pre-007 behaviour: the
     primary group_id filter is the only fail-closed clause.

  3. When ``expected_project_id`` is non-empty, the SQL gains an
     additional ``AND project_id = ?`` clause. A row whose
     `project_id` does not match returns no row — surfaced as
     ``FileNotFoundError`` to the caller (same fail-closed shape
     as the group-level mismatch).

  4. When ``expected_project_id`` matches, the load succeeds
     normally (the row is returned and dispatched by backend
     branch as usual).

  5. Anti-god-engine invariant preserved: types.py + result_blob.py
     do NOT gain imports from projects/auth/FastAPI/dataframe/GIS.
     Source-bound check.

  6. ``is_superadmin=True`` does NOT bypass the project filter.
     The audit-bypass semantics apply ONLY to the primary group
     filter; project binding is a row-level invariant the caller
     opted into.

Tests are stdlib + ``unittest.mock``-only; no DB / Redis / FastAPI
side-effects.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _REPO_ROOT / "backend"
_TYPES_PY = _BACKEND_ROOT / "app" / "core" / "resources" / "types.py"
_RESULT_BLOB_PY = (
    _BACKEND_ROOT / "app" / "core" / "resources" / "loaders" / "result_blob.py"
)


# ---------------------------------------------------------------------------
# 1. ResourceRef field shape — optional, defaults to None
# ---------------------------------------------------------------------------


def test_resource_ref_accepts_optional_expected_project_id():
    """`ResourceRef(expected_project_id=...)` is constructible
    with or without the field. Default is None."""
    from app.core.resources.types import ResourceRef

    # Without (back-compat)
    ref_no_project = ResourceRef(
        kind="result_blob",
        expected_group_id="g-1",
        result_id="r-1",
    )
    assert ref_no_project.expected_project_id is None

    # With non-empty string
    ref_with_project = ResourceRef(
        kind="result_blob",
        expected_group_id="g-1",
        result_id="r-1",
        expected_project_id="p-42",
    )
    assert ref_with_project.expected_project_id == "p-42"


def test_resource_ref_expected_project_id_does_not_affect_id_mutual_exclusion():
    """The mutual-exclusion check on (result_id, file_source_id,
    artifact_uri) MUST NOT count `expected_project_id` as one of
    those id slots. Sanity guard against a future regression."""
    from app.core.resources.types import ResourceRef

    # Adding expected_project_id alone (with NO id field) must
    # still raise the mutual-exclusion error.
    with pytest.raises(ValueError, match="exactly one id"):
        ResourceRef(
            kind="result_blob",
            expected_group_id="g-1",
            expected_project_id="p-42",
        )


# ---------------------------------------------------------------------------
# 2. SQL plumbing — project_id_filter applied only when set
# ---------------------------------------------------------------------------


def test_fetch_row_no_project_filter_when_none():
    """Default `project_id_filter=None` => SQL has NO `project_id`
    clause. Mirrors pre-007 behaviour byte-for-byte."""
    from app.core.resources.loaders import result_blob

    captured: dict = {}

    def _fake_fetchone(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return None

    with patch.object(result_blob, "_fetch_row", wraps=result_blob._fetch_row):
        with patch(
            "app.db.pg_connection.fetchone",
            side_effect=_fake_fetchone,
        ):
            result_blob._fetch_row(
                "r-1",
                group_id_filter="g-1",
                project_id_filter=None,
            )

    assert "sql" in captured
    assert "id = ?" in captured["sql"]
    assert "group_id = ?" in captured["sql"]
    assert "project_id = ?" not in captured["sql"], (
        "project_id_filter=None must NOT add a project_id clause"
    )
    assert captured["params"] == ("r-1", "g-1")


def test_fetch_row_adds_project_clause_when_set():
    """`project_id_filter='p-42'` => SQL gains `AND project_id =
    ?` and the parameter is bound."""
    from app.core.resources.loaders import result_blob

    captured: dict = {}

    def _fake_fetchone(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return None

    with patch(
        "app.db.pg_connection.fetchone",
        side_effect=_fake_fetchone,
    ):
        result_blob._fetch_row(
            "r-1",
            group_id_filter="g-1",
            project_id_filter="p-42",
        )

    assert "id = ?" in captured["sql"]
    assert "group_id = ?" in captured["sql"]
    assert "project_id = ?" in captured["sql"], (
        "project_id_filter set must add a project_id clause"
    )
    assert captured["params"] == ("r-1", "g-1", "p-42")


def test_fetch_row_select_includes_project_id_column():
    """The SELECT must include the `project_id` column so the
    dispatch / log shape can include it. Source-bound: we read
    the SQL string from the captured call."""
    from app.core.resources.loaders import result_blob

    captured: dict = {}

    def _fake_fetchone(sql, params):
        captured["sql"] = sql
        return None

    with patch(
        "app.db.pg_connection.fetchone",
        side_effect=_fake_fetchone,
    ):
        result_blob._fetch_row(
            "r-1",
            group_id_filter="g-1",
        )

    assert "SELECT data, storage_key, project_id FROM results" in captured["sql"]


# ---------------------------------------------------------------------------
# 3. Loader end-to-end: optional project filter
# ---------------------------------------------------------------------------


def test_loader_load_no_project_filter_when_ref_omits_it():
    """When `ref.expected_project_id` is None, the loader MUST
    invoke `_dispatch_by_id` with `project_id_filter=None`."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader
    from app.core.resources.types import ResourceRef

    captured: dict = {}

    def _fake_dispatch(result_id, *, group_id_filter, project_id_filter=None):
        captured["result_id"] = result_id
        captured["group_id_filter"] = group_id_filter
        captured["project_id_filter"] = project_id_filter
        return b"x", "db_legacy"

    loader = ResultBlobLoader()
    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g-1",
        result_id="r-1",
    )

    with patch(
        "app.core.resources.loaders.result_blob._dispatch_by_id",
        _fake_dispatch,
    ):
        loader.load(ref, mode="bytes")

    assert captured["result_id"] == "r-1"
    assert captured["group_id_filter"] == "g-1"
    assert captured["project_id_filter"] is None


def test_loader_load_passes_project_filter_when_set():
    """When `ref.expected_project_id='p-42'`, the loader MUST
    invoke `_dispatch_by_id` with `project_id_filter='p-42'`."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader
    from app.core.resources.types import ResourceRef

    captured: dict = {}

    def _fake_dispatch(result_id, *, group_id_filter, project_id_filter=None):
        captured["result_id"] = result_id
        captured["group_id_filter"] = group_id_filter
        captured["project_id_filter"] = project_id_filter
        return b"x", "db_legacy"

    loader = ResultBlobLoader()
    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g-1",
        result_id="r-1",
        expected_project_id="p-42",
    )

    with patch(
        "app.core.resources.loaders.result_blob._dispatch_by_id",
        _fake_dispatch,
    ):
        loader.load(ref, mode="bytes")

    assert captured["result_id"] == "r-1"
    assert captured["group_id_filter"] == "g-1"
    assert captured["project_id_filter"] == "p-42"


def test_loader_load_project_mismatch_raises_filenotfound():
    """When the dispatch returns ``"none"`` (no row matched), the
    loader raises ``FileNotFoundError`` regardless of whether the
    null result was caused by group mismatch, project mismatch,
    or both. The error message includes the project_id when set."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader
    from app.core.resources.types import ResourceRef

    def _fake_dispatch(result_id, *, group_id_filter, project_id_filter=None):
        return None, "none"

    loader = ResultBlobLoader()
    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g-1",
        result_id="r-1",
        expected_project_id="p-42",
    )

    with patch(
        "app.core.resources.loaders.result_blob._dispatch_by_id",
        _fake_dispatch,
    ):
        with pytest.raises(FileNotFoundError) as exc:
            loader.load(ref, mode="bytes")

    msg = str(exc.value)
    assert "r-1" in msg
    assert "g-1" in msg
    assert "p-42" in msg, (
        "FileNotFoundError message must mention expected_project_id "
        "when the caller passed it (operator visibility)"
    )


def test_loader_load_no_project_in_error_message_when_unset():
    """When the caller did NOT opt into project binding, the
    error message must NOT mention `project_id` (avoids leaking
    a None / empty project context into the operator-visible
    string)."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader
    from app.core.resources.types import ResourceRef

    def _fake_dispatch(result_id, *, group_id_filter, project_id_filter=None):
        return None, "none"

    loader = ResultBlobLoader()
    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g-1",
        result_id="r-1",
    )

    with patch(
        "app.core.resources.loaders.result_blob._dispatch_by_id",
        _fake_dispatch,
    ):
        with pytest.raises(FileNotFoundError) as exc:
            loader.load(ref, mode="bytes")

    msg = str(exc.value)
    assert "project_id" not in msg


# ---------------------------------------------------------------------------
# 4. is_superadmin does NOT bypass project filter
# ---------------------------------------------------------------------------


def test_is_superadmin_bypasses_group_filter_only_not_project_filter():
    """The is_superadmin audit-bypass applies ONLY to the primary
    group filter. If the caller opted into project binding,
    is_superadmin still passes the project filter through —
    project binding is a caller-driven row-level invariant."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader
    from app.core.resources.types import ResourceRef

    captured: dict = {}

    def _fake_dispatch(result_id, *, group_id_filter, project_id_filter=None):
        captured["group_id_filter"] = group_id_filter
        captured["project_id_filter"] = project_id_filter
        return b"x", "db_legacy"

    loader = ResultBlobLoader()
    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g-1",
        result_id="r-1",
        expected_project_id="p-42",
        is_superadmin=True,
    )

    with patch(
        "app.core.resources.loaders.result_blob._dispatch_by_id",
        _fake_dispatch,
    ):
        loader.load(ref, mode="bytes")

    # Group bypass: SQL filter dropped to None.
    assert captured["group_id_filter"] is None
    # Project filter: PRESERVED — caller opted in.
    assert captured["project_id_filter"] == "p-42"


# ---------------------------------------------------------------------------
# 5. Anti-god-engine — no new domain imports introduced
# ---------------------------------------------------------------------------


_FORBIDDEN_IMPORTS = {
    # Domain
    "app.core.projects",
    "app.core.auth",
    "app.core.artifacts",
    # Frameworks
    "fastapi",
    "starlette",
    # Heavy data
    "pandas",
    "geopandas",
    "fiona",
    "polars",
}


def _toplevel_imports(path: Path) -> set[str]:
    """Set of top-level imported module roots from ``path``."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out.add(mod)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
    return out


@pytest.mark.parametrize(
    "path",
    [_TYPES_PY, _RESULT_BLOB_PY],
    ids=["types.py", "result_blob.py"],
)
def test_resource_engine_modules_remain_anti_god_engine(path: Path):
    """`types.py` and `result_blob.py` MUST NOT introduce
    forbidden imports (projects / auth / artifacts / FastAPI /
    pandas / geopandas / fiona / polars). RESOURCE-ENGINE-007
    only adds an Optional[str] field — purely stdlib types."""
    imports = _toplevel_imports(path)
    violations: set[str] = set()
    for mod in imports:
        for forbidden in _FORBIDDEN_IMPORTS:
            if mod == forbidden or mod.startswith(forbidden + "."):
                violations.add(mod)
                break
    assert not violations, (
        f"{path.name} introduced forbidden import(s): "
        f"{sorted(violations)}. RESOURCE-ENGINE-007 must keep the "
        "engine stdlib + pg_connection only."
    )


def test_types_py_has_no_runtime_imports_beyond_stdlib():
    """`types.py` should remain stdlib-only (no `app.*` runtime
    imports). RESOURCE-ENGINE-007 just adds an Optional[str]
    field — no app-level dependency change."""
    imports = _toplevel_imports(_TYPES_PY)
    for mod in imports:
        if not mod:
            continue
        assert not mod.startswith("app."), (
            f"types.py top-level imports `{mod}` — but the engine "
            "type module must stay stdlib-only (no app.* deps)."
        )


# ---------------------------------------------------------------------------
# 6. Decision audit trail — source-bound rationale lock
# ---------------------------------------------------------------------------


def test_resource_ref_docstring_documents_project_binding_contract():
    """`ResourceRef` docstring MUST explicitly document:
      - the field is OPTIONAL;
      - it's row-metadata only (no project service / domain
        logic);
      - the engine doesn't import projects/auth/etc.
    Without this lock a future refactor could silently widen
    the contract to call into the project service."""
    text = _TYPES_PY.read_text(encoding="utf-8")
    # Marker phrases the docstring promises:
    assert "expected_project_id" in text
    assert "OPTIONAL" in text or "optional" in text
    assert "row-metadata" in text or "row metadata" in text
    assert "anti-god-engine" in text or "no imports from projects" in text or (
        "no imports from projects/auth" in text
    )
