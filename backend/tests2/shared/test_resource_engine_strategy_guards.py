"""Strategy / capability-registry guards for the Resource Engine.

BE-GO-READY-RESOURCE-ENGINE-003-STRATEGY-GUARD-ARTIFACT-AUDIT.

This file complements
``tests2/shared/test_resource_engine_anti_god_engine_lock.py`` by
locking the **strategy-shape** invariants that keep the Resource
Engine a Capability Registry rather than a god-engine:

  - per-kind dispatch: every loader handles EXACTLY ONE kind and
    rejects refs of any other kind (no multiplexing inside a loader);
  - mandatory ``expected_group_id`` at the type level (already
    locked by `__post_init__`; here we lock a few additional
    constructions paths that could erode the guard);
  - byte-level output only: the ``Resource`` envelope's output
    fields are ``bytes`` / ``Path`` / ``Iterator[bytes]`` plus
    metadata; no parsed object slot.

These guards are explicitly source-bound where possible: a refactor
that restored a banned import / a multi-kind loader / a parsed-object
field would fail this lock at CI time without needing a runtime
exercise.

Group A — per-kind dispatch invariant
Group B — mandatory tenant binding (defense-in-depth)
Group C — byte-level output invariant (metadata-only)
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _REPO_ROOT / "backend"
_LOADERS_DIR = _BACKEND_ROOT / "app" / "core" / "resources" / "loaders"
_TYPES_PY = _BACKEND_ROOT / "app" / "core" / "resources" / "types.py"


def _iter_loader_modules():
    """Yield each loader .py file under ``app/core/resources/loaders/``
    (excluding ``__init__.py``, private package utilities, and pycache).

    Private utilities (files starting with ``_``) are shared helpers
    for the loader modules (e.g. ``_shared.py``). They are NOT
    loaders and MUST NOT carry a ``kind`` property.
    """
    for path in _LOADERS_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        if path.name.startswith("_"):
            continue
        yield path


# =====================================================================
# Group A — per-kind dispatch invariant
# =====================================================================


def test_each_loader_module_declares_exactly_one_kind_via_property():
    """Source-bound: every loader module under
    ``app/core/resources/loaders/`` MUST contain exactly one class
    that exposes a ``kind`` property returning a ``ResourceKind``
    string literal. A loader that multiplexes kinds (e.g. returns
    ``"result_blob" if ... else "file_source_blob"``) is the
    canonical god-engine failure mode."""
    offenders: list[str] = []

    for path in _iter_loader_modules():
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)

        loader_classes = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            kind_props = []
            for sub in node.body:
                # @property def kind(self) -> ...: return "<literal>"
                if not isinstance(sub, ast.FunctionDef):
                    continue
                if sub.name != "kind":
                    continue
                if not any(
                    isinstance(d, ast.Name) and d.id == "property"
                    for d in sub.decorator_list
                ):
                    continue
                # Body must be a single Return of a Constant str.
                if (
                    len(sub.body) == 1
                    and isinstance(sub.body[0], ast.Return)
                    and isinstance(sub.body[0].value, ast.Constant)
                    and isinstance(sub.body[0].value.value, str)
                ):
                    kind_props.append(sub.body[0].value.value)
                else:
                    kind_props.append(None)  # non-literal kind => offender
            if kind_props:
                loader_classes.append((node.name, kind_props))

        if not loader_classes:
            offenders.append(
                f"{path.name}: no loader class with a `kind` property "
                "(every loader module must expose at least one)"
            )
            continue

        for cls_name, kinds in loader_classes:
            if len(kinds) != 1:
                offenders.append(
                    f"{path.name}::{cls_name}: declared {len(kinds)} `kind` "
                    f"properties (must be exactly one per loader class)"
                )
                continue
            (k,) = kinds
            if k is None:
                offenders.append(
                    f"{path.name}::{cls_name}: `kind` property body is not "
                    "a single literal `return \"<kind>\"`. Per-kind dispatch "
                    "requires a static, source-readable kind."
                )

    assert not offenders, (
        "Per-kind dispatch invariant violated:\n  "
        + "\n  ".join(offenders)
    )


def test_each_loader_module_rejects_mismatched_kind_at_load_time():
    """Source-bound: every loader's ``load()`` body MUST contain a
    guard that rejects a ``ResourceRef`` whose kind does not match
    the loader's declared kind. The guard is what makes per-kind
    dispatch fail-fast at runtime even if the registry is bypassed.

    Lock signal: the body must contain the substring
    ``ref.kind != "<kind>"`` (the canonical guard shape used by
    ``ResultBlobLoader``).
    """
    offenders: list[str] = []
    for path in _iter_loader_modules():
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            kind_literal: str | None = None
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "kind":
                    if (
                        len(sub.body) == 1
                        and isinstance(sub.body[0], ast.Return)
                        and isinstance(sub.body[0].value, ast.Constant)
                        and isinstance(sub.body[0].value.value, str)
                    ):
                        kind_literal = sub.body[0].value.value
            if kind_literal is None:
                continue

            # Find load() and verify it contains the kind guard.
            load_fn = next(
                (
                    sub
                    for sub in node.body
                    if isinstance(sub, ast.FunctionDef) and sub.name == "load"
                ),
                None,
            )
            if load_fn is None:
                offenders.append(
                    f"{path.name}::{node.name}: no `load()` method"
                )
                continue
            load_src = ast.get_source_segment(text, load_fn) or ""
            needle = f'ref.kind != "{kind_literal}"'
            alt_needle = f"ref.kind != '{kind_literal}'"
            if needle not in load_src and alt_needle not in load_src:
                offenders.append(
                    f"{path.name}::{node.name}.load: missing kind guard "
                    f"`ref.kind != {kind_literal!r}`. Per-kind dispatch "
                    "requires every loader to fail-fast on mismatched "
                    "ResourceKind, even if the registry is bypassed."
                )

    assert not offenders, (
        "Per-kind dispatch guard missing in loader(s):\n  "
        + "\n  ".join(offenders)
    )


def test_loader_kind_property_value_is_in_resource_kind_literal():
    """Runtime cross-check: the kind a loader claims via its
    property MUST also be present in ``ResourceKind`` (the typed
    Literal). A loader that claims a kind not in the Literal can
    still be registered today, but the registry would never route
    to it -- that's a silent capability hole."""
    from app.core.resources import ResourceKind

    valid_kinds = set(get_args(ResourceKind))

    # Currently the v1 registry has only result_blob registered.
    # We import the loader directly (not the registry) to keep the
    # test independent of registry mutation in tests.
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    loader = ResultBlobLoader()
    assert loader.kind in valid_kinds, (
        f"ResultBlobLoader.kind={loader.kind!r} is not in "
        f"ResourceKind={sorted(valid_kinds)}. Either expand the "
        "Literal or fix the loader's kind."
    )


def test_loader_load_rejects_mismatched_kind_with_value_error():
    """Behavioural: passing a ref whose kind does not match the
    loader's declared kind raises ``ValueError`` (the per-kind
    dispatch fast-fail). Verified by constructing a ref with a
    kind not handled by ResultBlobLoader. Skipped if no second
    kind exists in the Literal yet (v1 ships exactly one)."""
    from app.core.resources import ResourceKind, ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    valid_kinds = set(get_args(ResourceKind))
    other_kinds = valid_kinds - {"result_blob"}
    if not other_kinds:
        pytest.skip(
            "v1 ships exactly one ResourceKind; per-kind mismatch test "
            "becomes meaningful only after a second kind is added"
        )

    other = next(iter(other_kinds))
    ref = ResourceRef(
        kind=other,
        expected_group_id="g1",
        result_id="r1",
    )
    with pytest.raises(ValueError, match="does not handle kind"):
        ResultBlobLoader().load(ref, mode="bytes")


# =====================================================================
# Group B — mandatory tenant binding (defense-in-depth)
# =====================================================================


def test_resource_ref_post_init_is_present_in_source():
    """Source-bound: ``ResourceRef`` MUST declare ``__post_init__``.
    Removing it would silently drop the fail-closed group_id /
    one-id-set guards (the dataclass would then accept empty /
    ambiguous refs)."""
    text = _TYPES_PY.read_text(encoding="utf-8")
    assert "def __post_init__" in text, (
        "ResourceRef.__post_init__ is the structural enforcement of "
        "mandatory tenant binding (`expected_group_id`) and "
        "mutual-exclusion of id fields. It must remain in source."
    )


def test_resource_ref_post_init_rejects_whitespace_only_group_id():
    """Behavioural defense-in-depth: an ``expected_group_id`` of pure
    whitespace is semantically empty. The current ``__post_init__``
    uses a falsy check (``if not self.expected_group_id``) which
    accepts ``"  "`` -- this test pins down the current behavior so a
    future tightening (``.strip()``) is an explicit choice, and a
    future loosening (e.g. dropping the guard) is caught."""
    from app.core.resources import ResourceRef

    # Current behavior: whitespace IS accepted (truthy string).
    # We pin this so a future tightening is intentional.
    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="  ",
        result_id="r1",
    )
    assert ref.expected_group_id == "  ", (
        "Current ResourceRef accepts whitespace-only group_id. "
        "If you intentionally tightened the guard to reject it, "
        "update this test to use pytest.raises(ValueError) and "
        "document the change in BE_RESOURCE_ENGINE_AUDIT.md."
    )


@pytest.mark.parametrize("bad_group_id", ["", None])
def test_resource_ref_construction_rejects_empty_group_id_parametrized(bad_group_id):
    """Defense-in-depth: cover both empty string and ``None`` as
    inputs. ``None`` will fail the dataclass type at runtime via the
    same falsy check (Python does not enforce the ``str`` annotation
    at construction); the guard catches both."""
    from app.core.resources import ResourceRef

    with pytest.raises(ValueError, match="expected_group_id required"):
        ResourceRef(
            kind="result_blob",
            expected_group_id=bad_group_id,  # type: ignore[arg-type]
            result_id="r1",
        )


# =====================================================================
# Group C — byte-level output invariant (metadata only)
# =====================================================================


def test_resource_envelope_has_no_parsed_object_field_via_ast():
    """Source-bound AST: walk the ``Resource`` ``ClassDef`` body and
    enumerate every annotated attribute. The set must equal exactly
    the byte-level + metadata fields. Adding a ``DataFrame`` /
    ``records`` / ``preview`` field is a god-engine regression;
    AST inspection catches it even if the field is annotated with
    a string forward ref (``"pd.DataFrame"``)."""
    text = _TYPES_PY.read_text(encoding="utf-8")
    tree = ast.parse(text)

    resource_cls: ast.ClassDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Resource":
            resource_cls = node
            break

    assert resource_cls is not None, "Resource class not found in types.py"

    declared_fields: set[str] = set()
    for stmt in resource_cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            declared_fields.add(stmt.target.id)

    expected = {
        "kind",
        "size_bytes",
        "data",
        "path",
        "stream",
        "format_hint",
        "is_temporary",
    }
    assert declared_fields == expected, (
        f"Resource fields drifted from byte-level invariant.\n"
        f"  declared: {sorted(declared_fields)}\n"
        f"  expected: {sorted(expected)}\n"
        "Anti-god-engine: byte-level (data/path/stream) + metadata "
        "(kind/size_bytes/format_hint/is_temporary) ONLY. Parsing "
        "belongs to higher-level wrappers."
    )


def test_resource_size_bytes_is_required_int_not_optional():
    """The ``size_bytes`` field MUST be a required ``int`` (no default,
    no ``Optional``). A loader that cannot produce a size MUST raise,
    not return ``size_bytes=None`` or ``size_bytes=0`` silently.
    Caps + rate-limits depend on this contract.
    """
    text = _TYPES_PY.read_text(encoding="utf-8")
    tree = ast.parse(text)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "Resource"):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id == "size_bytes"
            ):
                # Annotation must be `int`, not `Optional[int]`.
                ann_src = ast.unparse(stmt.annotation)
                assert ann_src == "int", (
                    f"Resource.size_bytes annotation drifted to {ann_src!r}; "
                    "must remain `int` (required, non-Optional)."
                )
                # No default -> stmt.value is None.
                assert stmt.value is None, (
                    "Resource.size_bytes must have NO default. A default "
                    "(0 or None) lets loaders silently return unsized "
                    "resources, which breaks size-cap enforcement."
                )
                return
    pytest.fail("Resource.size_bytes field not found")


def test_resource_data_path_stream_remain_optional_byte_level():
    """The output fields must remain Optional and byte-level typed.
    Anything else (DataFrame, dict, list) is the regression we want
    to catch. Verified via AST-unparsed annotation strings."""
    text = _TYPES_PY.read_text(encoding="utf-8")
    tree = ast.parse(text)

    expected_annotations = {
        "data": "Optional[bytes]",
        "path": "Optional[Path]",
        "stream": "Optional[Iterator[bytes]]",
    }
    seen: dict[str, str] = {}

    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "Resource"):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id in expected_annotations
            ):
                seen[stmt.target.id] = ast.unparse(stmt.annotation)

    assert seen == expected_annotations, (
        f"Resource output-field annotations drifted:\n"
        f"  seen:     {seen}\n"
        f"  expected: {expected_annotations}\n"
        "Byte-level invariant: data/path/stream MUST stay Optional "
        "and byte-level. A `DataFrame` / `dict` annotation here is a "
        "god-engine regression."
    )


def test_loader_returns_resource_dataclass_only():
    """Behavioural: ``ResultBlobLoader.load(...)`` returns a
    ``Resource`` instance, never a tuple, dict, or domain object.
    Mock the dispatch to avoid hitting PG; assert envelope shape."""
    from app.core.resources import Resource, ResourceRef
    from app.core.resources.loaders import result_blob as loader_mod

    payload = b"hello-world"

    def _fake_dispatch(result_id, *, group_id_filter, project_id_filter=None):
        # RESOURCE-ENGINE-007: dispatch gained an optional
        # project_id_filter kwarg. Accept it (default None) for
        # backward compatibility with this contract test.
        return payload, "db_legacy"

    original = loader_mod._dispatch_by_id
    loader_mod._dispatch_by_id = _fake_dispatch
    try:
        ref = ResourceRef(
            kind="result_blob",
            expected_group_id="g1",
            result_id="r1",
        )
        out = loader_mod.ResultBlobLoader().load(ref, mode="bytes")
    finally:
        loader_mod._dispatch_by_id = original

    assert isinstance(out, Resource), (
        f"Loader returned {type(out).__name__}, not Resource. The byte-level "
        "envelope is the only allowed return type."
    )
    assert out.data == payload
    assert out.size_bytes == len(payload)
    assert out.path is None
    assert out.stream is None
    assert out.kind == "result_blob"
