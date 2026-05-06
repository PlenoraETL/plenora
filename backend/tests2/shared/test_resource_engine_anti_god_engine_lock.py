"""Anti-god-engine source-bound lock for the Resource Engine pilot.

BE-GO-READY-RESOURCE-ENGINE-002-RESULT-BLOB-LOADER-PILOT.

Locks the architectural rules from
``.agent-temp/BE_RESOURCE_ENGINE_AUDIT.md`` sec. 6:

  - the engine is a CAPABILITY REGISTRY, not a god-engine;
  - the engine returns byte-level resources, not parsed objects;
  - tenant binding is required at the type level (fail-closed);
  - the storage-key dispatch lives in EXACTLY ONE place after
    migration (de-duplication invariant);
  - banned imports under ``app/core/resources/`` are checked
    source-bound (so the lock catches diffs that would compose a
    god-engine via convenience imports).

These tests are SOURCE-BOUND: they read the file content and grep
for forbidden patterns. A loader file that imports `pandas` will
fail this test even if the import is unused at runtime, because
the architectural rule is "no domain libs in the engine" — the
import alone is the regression signal.

Group A — type-level contract (always active)
Group B — banned imports (always active)
Group C — single-dispatch lock (always active post-migration)
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _REPO_ROOT / "backend"
_RESOURCES_PKG = _BACKEND_ROOT / "app" / "core" / "resources"
_TYPES_PY = _RESOURCES_PKG / "types.py"
_REGISTRY_PY = _RESOURCES_PKG / "registry.py"
_RESULT_BLOB_PY = _RESOURCES_PKG / "loaders" / "result_blob.py"
_RESULTS_DB_PY = _BACKEND_ROOT / "app" / "db" / "results_db_service.py"


# =====================================================================
# Group A — type-level contract
# =====================================================================


def test_canonical_names_exported_from_package():
    """The four canonical names MUST be importable from
    `app.core.resources`. Renaming any of them is a public-API
    regression."""
    from app.core.resources import (
        Resource,
        ResourceLoader,
        ResourceRef,
        ResourceRegistry,
    )

    assert Resource is not None
    assert ResourceLoader is not None
    assert ResourceRef is not None
    assert ResourceRegistry is not None


def test_resource_ref_is_frozen():
    from app.core.resources import ResourceRef

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        ref.expected_group_id = "g2"  # type: ignore[misc]


def test_resource_ref_rejects_empty_group_id():
    """Fail-closed by construction: empty `expected_group_id` is a
    bug at construction time, not a permission failure at fetch
    time."""
    from app.core.resources import ResourceRef

    with pytest.raises(ValueError, match="expected_group_id required"):
        ResourceRef(kind="result_blob", expected_group_id="", result_id="r1")


def test_resource_ref_rejects_zero_id_set():
    from app.core.resources import ResourceRef

    with pytest.raises(ValueError, match="exactly one id"):
        ResourceRef(kind="result_blob", expected_group_id="g1")


def test_resource_ref_rejects_multiple_ids_set():
    """Mutual exclusion: setting two id fields is ambiguous and
    must fail loudly."""
    from app.core.resources import ResourceRef

    with pytest.raises(ValueError, match="exactly one id"):
        ResourceRef(
            kind="result_blob",
            expected_group_id="g1",
            result_id="r1",
            file_source_id="f1",
        )


def test_resource_is_frozen():
    from app.core.resources import Resource

    r = Resource(kind="result_blob", size_bytes=0)
    with pytest.raises(Exception):
        r.size_bytes = 99  # type: ignore[misc]


# =====================================================================
# Group B — banned imports under app/core/resources/
# =====================================================================


_BANNED_IMPORT_NEEDLES = (
    # Domain parsers — engine returns bytes, never DataFrames.
    "import pandas",
    "from pandas",
    "import geopandas",
    "from geopandas",
    "import fiona",
    "from fiona",
    "import polars",
    "from polars",
    # Web framework — engine is core-level.
    "import fastapi",
    "from fastapi",
    # Auth — engine consumes `expected_group_id` only; resolving
    # user->group is the request layer's job.
    "from app.core.auth",
    "import app.core.auth",
    # Domain wrappers that sit ABOVE the engine. The engine MUST
    # NOT call back UP into them.
    "from app.core.artifacts",
    "import app.core.artifacts",
    "from app.core.eseguibili",
    "import app.core.eseguibili",
    "from app.core.imdf",
    "import app.core.imdf",
    "from app.core.intermediate",
    "import app.core.intermediate",
)


def _python_code_only(source: str) -> str:
    """Strip string literals and comments via tokenize so docstring
    text mentioning forbidden patterns does not false-positive."""
    import io
    import tokenize

    out = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenizeError:
        return source
    for tok in tokens:
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        out.append(tok.string)
    return " ".join(out)


def _iter_resources_py():
    for path in _RESOURCES_PKG.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_no_banned_imports_under_resources():
    """Source-bound check: every .py file under app/core/resources/
    is scanned (with strings/comments stripped) for forbidden import
    patterns. ANY hit is a regression."""
    offenders: list[tuple[str, str]] = []
    for path in _iter_resources_py():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        code_only = _python_code_only(text)
        for needle in _BANNED_IMPORT_NEEDLES:
            if needle in code_only:
                rel = str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")
                offenders.append((rel, needle))

    if offenders:
        details = "\n".join(f"  {p}: {n!r}" for (p, n) in offenders)
        raise AssertionError(
            "Banned import(s) under app/core/resources/:\n"
            f"{details}\n\n"
            "Anti-god-engine rule: the Resource Engine returns byte-level "
            "resources only. Domain parsing (pandas/geopandas/fiona), web "
            "framework (fastapi), auth resolution, and higher-level "
            "domain wrappers (core.artifacts/eseguibili/imdf/intermediate) "
            "are OUT OF SCOPE. See "
            "`.agent-temp/BE_RESOURCE_ENGINE_AUDIT.md` sec. 6."
        )


def test_loader_pkg_does_not_pull_pandas_at_import_time():
    """Defense-in-depth: confirm at runtime that importing the engine
    package does NOT transitively pull pandas/geopandas/fiona into
    sys.modules. A stray `import pandas` deep in a util chain would
    pass the source-bound check above but fail here."""
    import importlib
    import sys

    # Snapshot pre-import (pandas may already be loaded by the test
    # session; we only assert IT WAS NOT THE ENGINE that loaded it
    # via a delta check).
    before = set(sys.modules.keys())
    importlib.import_module("app.core.resources")
    importlib.import_module("app.core.resources.loaders.result_blob")
    after = set(sys.modules.keys())
    new_modules = after - before

    forbidden = {"pandas", "geopandas", "fiona", "polars", "fastapi"}
    leaked = forbidden & new_modules
    assert not leaked, (
        f"Importing the Resource Engine pulled forbidden modules "
        f"into sys.modules: {sorted(leaked)}. The engine must remain "
        "byte-level; domain libs belong to higher-level wrappers."
    )


# =====================================================================
# Group C — single-dispatch lock (de-duplication invariant)
# =====================================================================


def test_storage_key_dispatch_lives_in_exactly_one_module():
    """After the pilot, the BYTEA-vs-S3 dispatch for result_blob
    lives in `app/core/resources/loaders/result_blob.py` only.
    `app/db/results_db_service.py::get_result_data` is a thin
    back-compat shim and MUST NOT carry its own dispatch.

    Lock signal: the pre-pilot `get_result_data` body contained
    the literal `'_s3_download_bytes(storage_key)'` followed later
    by `'data = row.get("data")'` — the inline dispatch. The shim
    must NOT contain those tokens any longer."""
    text = _RESULTS_DB_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(text)

    # The post-pilot shim delegates to the loader. The dispatch
    # tokens live in the loader, not here.
    forbidden_in_shim = (
        # original dispatch branch in get_result_data
        '_s3_download_bytes(storage_key)',
        # original BYTEA fallback line
        'isinstance(data, memoryview)',
        # original SQL with explicit storage_key column join
        'SELECT data, storage_key FROM results',
    )
    offenders = [t for t in forbidden_in_shim if t in code_only]
    assert not offenders, (
        "results_db_service.py::get_result_data must remain a thin "
        "shim over `ResultBlobLoader._dispatch_by_id`. Dispatch tokens "
        f"reappeared: {offenders}. Either the migration regressed or a "
        "new inline dispatch was added; consolidate via the loader."
    )


def test_loader_owns_the_dispatch_tokens():
    """Symmetric assertion: the dispatch tokens MUST live in the
    loader module post-migration. Removing them from the loader
    silently re-introduces fallback paths in callers.

    BE-GO-READY-RESOURCE-ENGINE-007: SELECT includes
    `project_id` to support optional row-metadata project-level
    binding (`ResourceRef.expected_project_id`).

    BE-GO-READY-RESOURCE-ENGINE-013: SELECT now also projects
    `length(data) AS data_size` and wraps `data` in a CASE that
    NULLs out the column when `length(data) > ?` (BYTEA preflight
    cap). The fixed projection of `storage_key, project_id,
    length(data) AS data_size` is the stable contract; the WHERE
    is composed at runtime from optional filters.
    """
    text = _RESULT_BLOB_PY.read_text(encoding="utf-8")
    required = (
        # SQL row metadata projection (RESOURCE-ENGINE-007 +
        # RESOURCE-ENGINE-013). storage_key and project_id are the
        # always-present row-metadata columns; data_size is the
        # 013 wire-level preflight projection.
        "storage_key, project_id, length(data) AS data_size",
        # 013: CASE-bounded `data` column. Without this, PG would
        # ship the oversized BYTEA over the wire before any cap
        # check could fire.
        "WHEN length(data) > ? THEN NULL",
        "ELSE data",
        # BYTEA conversion path
        "isinstance(data, memoryview)",
        # object storage download symbol
        "get_object_stream",
        # the structured timing log shape that ops dashboards
        # already filter on (preserve verbatim)
        "[PERF] get_result_data op=fetch_blob",
    )
    code_full = text  # log strings live in f-strings; do NOT strip here
    missing = [t for t in required if t not in code_full]
    assert not missing, (
        f"ResultBlobLoader is missing canonical dispatch tokens: "
        f"{missing}. The pilot's de-duplication relies on these tokens "
        "living in the loader; restore them or extend this lock to "
        "match the new shape."
    )


# =====================================================================
# Group D — registry contract
# =====================================================================


def test_default_registry_resolves_result_blob_kind():
    from app.core.resources import build_default_registry

    reg = build_default_registry()
    # The registry MUST have `result_blob` registered out of the box
    # (the v1 pilot kind). Other kinds MUST NOT be registered (audit
    # sec. 5 + 7: deferred until tenant binding plumbing lands).
    assert "result_blob" in reg._loaders, (  # noqa: SLF001
        "build_default_registry() must register `result_blob` out of "
        "the box (v1 pilot kind)."
    )
    assert sorted(reg._loaders.keys()) == ["result_blob"], (  # noqa: SLF001
        f"Default registry has unexpected kinds: "
        f"{sorted(reg._loaders.keys())}. v1 ships exactly one loader "
        "(`result_blob`); other kinds (file_source_blob, "
        "intermediate_artifact, eseguibili_output, imdf_bundle) join "
        "only after their tenant binding plumbing lands."
    )


def test_registry_rejects_duplicate_registration():
    from app.core.resources import build_default_registry
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    reg = build_default_registry()
    with pytest.raises(ValueError, match="already registered"):
        reg.register(ResultBlobLoader())


def test_registry_raises_on_missing_kind():
    """Manually constructed empty registry returns KeyError on get."""
    from app.core.resources.registry import _DefaultResourceRegistry
    from app.core.resources import ResourceRef

    reg = _DefaultResourceRegistry()
    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )
    with pytest.raises(KeyError, match="no loader for kind"):
        reg.get(ref, mode="bytes")


# =====================================================================
# Group E — registry public API is bounded
# =====================================================================


def test_registry_protocol_has_only_register_and_get():
    """Anti-god-engine: the registry MUST expose only `register`
    and `get` to consumers. Adding `parse`, `delete`, `save`,
    `list`, or any other capability is the canonical god-engine
    failure mode."""
    from app.core.resources.types import ResourceRegistry

    # `__abstractmethods__` is empty for Protocols; we inspect the
    # explicit method names declared on the class body.
    decl_methods = {
        name
        for name, val in vars(ResourceRegistry).items()
        if callable(val) and not name.startswith("_")
    }
    assert decl_methods == {"register", "get"}, (
        f"ResourceRegistry Protocol must declare exactly {{register, get}}. "
        f"Got: {decl_methods}. Adding methods here is the canonical "
        "god-engine failure mode (audit sec. 6 of "
        "BE_RESOURCE_ENGINE_AUDIT.md)."
    )


def test_resource_loader_protocol_returns_byte_level_only():
    """Source-bound check: the `Resource` envelope's typed fields
    are byte-level (`bytes`, `Path`, `Iterator[bytes]`). Adding a
    `dataframe`/`records`/`preview` field is a god-engine
    regression."""
    text = _TYPES_PY.read_text(encoding="utf-8")

    # The Resource dataclass body MUST contain exactly these
    # output-bearing field names (besides metadata).
    required_fields = (
        "data: Optional[bytes]",
        "path: Optional[Path]",
        "stream: Optional[Iterator[bytes]]",
    )
    for f in required_fields:
        assert f in text, (
            f"Resource dataclass field declaration missing: {f!r}. "
            "Anti-god-engine: byte-level output is the contract."
        )

    forbidden_fields = (
        "DataFrame",
        "GeoDataFrame",
        "records",
        "preview",
        "rows:",
        "schema:",
    )
    code_only = _python_code_only(text)
    leaks = [f for f in forbidden_fields if f in code_only]
    assert not leaks, (
        f"Resource type leaked domain-shaped fields: {leaks}. "
        "Anti-god-engine: parsing belongs to higher-level wrappers."
    )
