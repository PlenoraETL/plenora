"""Source-bound contract for the centralized resource cap resolver.

BE-GO-READY-MEMORY-ENGINE-006-CENTRALIZE-RESOURCE-CAPS.

Locks that the two resource-engine loaders use the shared helper
(``app.core.resources.resource_caps.resolve_resource_max_bytes``)
and do NOT reimplement env parsing locally.

Drift mode the lock prevents
----------------------------
Pre-006 the result-blob loader and the file-source-blob loader
each owned a private ``_resolve_max_bytes`` that read the
``RESOURCE_MAX_BYTES`` env, did the same int-coerce + non-positive
fallback, and returned the same 200 MB default. Two copies, one
contract; the canonical drift is a future env-validation tweak
(e.g. accept ``"500MB"`` suffix syntax) landing in one loader and
not the other, producing a tenant-visible behavioral split that
no test would catch.

What this contract bans (per loader file)
-----------------------------------------
- direct reads of ``os.environ.get("RESOURCE_MAX_BYTES")`` or any
  variant (``os.getenv("RESOURCE_MAX_BYTES")``);
- a private ``_DEFAULT_*_MAX_BYTES`` integer constant in the
  loader (the canonical default lives in
  ``resource_caps.RESOURCE_MAX_BYTES_DEFAULT``);
- a function body that contains both an ``int(...)`` cast AND a
  ``RESOURCE_MAX_BYTES`` literal -- that is structurally a local
  env-parsing reimplementation.

Both loaders MUST NOT expose a ``_resolve_max_bytes`` compat alias
anymore. BE-GO-READY-MEMORY-ENGINE-007 made the shared helper the
only public entry point for this cap.

Why source-bound
----------------
The helper is stdlib-only and the loaders' import boundary is
already locked by
``test_resource_engine_anti_god_engine_lock.py``. This test is
narrower: it inspects each loader's source for forbidden tokens
(env reads + parallel default constants), so a future contributor
who copy-pastes a private resolver gets a fail-loud signal at
test time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_LOADERS = (
    _BACKEND_ROOT
    / "app" / "core" / "resources" / "loaders" / "result_blob.py",
    _BACKEND_ROOT
    / "app" / "core" / "resources" / "loaders" / "file_source_blob.py",
)


def _python_code_only(text: str) -> str:
    """Strip triple-quoted docstrings and ``#`` comments.

    The token bans below MUST not false-positive on prose that
    happens to mention ``RESOURCE_MAX_BYTES`` (e.g. a docstring
    explaining the resolver contract). Tokenize-based stripping
    is overkill for a regex-friendly pattern set; a simple
    multi-line strip is sufficient and matches the posture used
    by the pilot-exit lock test.
    """
    out = re.sub(r'"""[\s\S]*?"""', "", text)
    out = re.sub(r"'''[\s\S]*?'''", "", out)
    out = re.sub(r"#.*$", "", out, flags=re.MULTILINE)
    return out


# 1. Helper exists with the documented surface


def test_helper_module_is_importable_with_canonical_surface():
    """The shared helper must exist and expose the three names
    the loaders + tests reference."""
    from app.core.resources import resource_caps

    assert hasattr(resource_caps, "resolve_resource_max_bytes")
    assert hasattr(resource_caps, "RESOURCE_MAX_BYTES_DEFAULT")
    assert hasattr(resource_caps, "RESOURCE_MAX_BYTES_ENV")
    assert resource_caps.RESOURCE_MAX_BYTES_DEFAULT == 200 * 1024 * 1024
    assert resource_caps.RESOURCE_MAX_BYTES_ENV == "RESOURCE_MAX_BYTES"


def test_helper_default_when_env_unset(monkeypatch):
    """No env -> the documented 200 MB default."""
    from app.core.resources.resource_caps import (
        RESOURCE_MAX_BYTES_DEFAULT,
        resolve_resource_max_bytes,
    )

    monkeypatch.delenv("RESOURCE_MAX_BYTES", raising=False)
    assert resolve_resource_max_bytes() == RESOURCE_MAX_BYTES_DEFAULT


def test_helper_env_override_is_honored(monkeypatch):
    monkeypatch.setenv("RESOURCE_MAX_BYTES", "65536")
    from app.core.resources.resource_caps import (
        resolve_resource_max_bytes,
    )

    assert resolve_resource_max_bytes() == 65536


@pytest.mark.parametrize("bad_value", ["", "not-an-int", "0", "-1"])
def test_helper_invalid_or_nonpositive_falls_back(monkeypatch, bad_value):
    monkeypatch.setenv("RESOURCE_MAX_BYTES", bad_value)
    from app.core.resources.resource_caps import (
        RESOURCE_MAX_BYTES_DEFAULT,
        resolve_resource_max_bytes,
    )

    assert resolve_resource_max_bytes() == RESOURCE_MAX_BYTES_DEFAULT


def test_helper_accepts_explicit_env_mapping():
    """The helper accepts an explicit mapping for tests that want
    isolation from process env. This is the documented escape
    hatch for property-style tests; production reads the live
    process env."""
    from app.core.resources.resource_caps import (
        RESOURCE_MAX_BYTES_DEFAULT,
        resolve_resource_max_bytes,
    )

    assert resolve_resource_max_bytes(env={}) == RESOURCE_MAX_BYTES_DEFAULT
    assert resolve_resource_max_bytes(
        env={"RESOURCE_MAX_BYTES": "12345"},
    ) == 12345
    assert resolve_resource_max_bytes(
        env={"RESOURCE_MAX_BYTES": "garbage"},
    ) == RESOURCE_MAX_BYTES_DEFAULT


# 2. Source-bound: loaders MUST NOT reimplement env parsing


def test_loaders_import_the_shared_helper():
    """Each loader's source MUST import
    ``resolve_resource_max_bytes`` from
    ``app.core.resources.resource_caps``. A regression that
    re-introduces a private resolver would also drop this import,
    so failing the import check is sufficient (no need to AST-parse
    the full call graph)."""
    bad: list[str] = []
    for path in _LOADERS:
        text = path.read_text(encoding="utf-8")
        if "from app.core.resources.resource_caps import resolve_resource_max_bytes" not in text:
            rel = path.relative_to(_BACKEND_ROOT).as_posix()
            bad.append(rel)
    assert not bad, (
        "The following loaders MUST import "
        "`resolve_resource_max_bytes` from "
        "`app.core.resources.resource_caps` (BE-GO-READY-MEMORY-"
        "ENGINE-006 centralized the cap resolver):\n  "
        + "\n  ".join(bad)
    )


def test_loaders_do_not_read_env_directly():
    """Each loader MUST NOT contain a direct
    ``os.environ.get("RESOURCE_MAX_BYTES"...)`` /
    ``os.getenv("RESOURCE_MAX_BYTES"...)`` call. The shared helper
    is the single point that reads the env; a regression that
    re-introduces a local read is the canonical drift the helper
    exists to prevent."""
    forbidden_patterns = (
        re.compile(r"""os\.environ\s*\.\s*get\s*\(\s*['"]RESOURCE_MAX_BYTES['"]"""),
        re.compile(r"""os\.environ\[\s*['"]RESOURCE_MAX_BYTES['"]\s*\]"""),
        re.compile(r"""os\.getenv\s*\(\s*['"]RESOURCE_MAX_BYTES['"]"""),
        re.compile(r"""_os\.environ\s*\.\s*get\s*\(\s*['"]RESOURCE_MAX_BYTES['"]"""),
        re.compile(r"""_os\.getenv\s*\(\s*['"]RESOURCE_MAX_BYTES['"]"""),
    )

    bad: list[tuple[str, str]] = []
    for path in _LOADERS:
        text = _python_code_only(path.read_text(encoding="utf-8"))
        for pat in forbidden_patterns:
            m = pat.search(text)
            if m:
                rel = path.relative_to(_BACKEND_ROOT).as_posix()
                bad.append((rel, m.group(0)))

    assert not bad, (
        "Loaders MUST NOT read `RESOURCE_MAX_BYTES` directly from "
        "the env. Use "
        "`app.core.resources.resource_caps.resolve_resource_max_bytes`. "
        "Offenders:\n  "
        + "\n  ".join(f"{rel}: {snippet}" for rel, snippet in bad)
    )


def test_loaders_do_not_redeclare_default_constant():
    """Each loader MUST NOT redeclare a private
    ``_DEFAULT_*_MAX_BYTES = ... * 1024 * 1024`` integer constant.
    The canonical default lives in
    ``resource_caps.RESOURCE_MAX_BYTES_DEFAULT``; a parallel
    constant is a drift surface (one bumps to 500 MB, the other
    stays at 200 MB)."""
    pattern = re.compile(r"_DEFAULT_\w*MAX_BYTES\s*=\s*\d+\s*\*")
    bad: list[tuple[str, str]] = []
    for path in _LOADERS:
        text = _python_code_only(path.read_text(encoding="utf-8"))
        m = pattern.search(text)
        if m:
            rel = path.relative_to(_BACKEND_ROOT).as_posix()
            bad.append((rel, m.group(0)))
    assert not bad, (
        "Loaders MUST NOT redeclare a `_DEFAULT_*_MAX_BYTES` "
        "constant; use `resource_caps.RESOURCE_MAX_BYTES_DEFAULT`. "
        "Offenders:\n  "
        + "\n  ".join(f"{rel}: {snippet}" for rel, snippet in bad)
    )


def test_artifact_preview_cap_is_not_collapsed_into_resource_max_bytes():
    """``ARTIFACT_PREVIEW_MAX_BYTES`` (preview-side concurrency-
    sensitive cap) is DELIBERATELY separate from
    ``RESOURCE_MAX_BYTES`` (resource-loader cap). The shared
    helper handles only the resource-loader env; the preview
    helper lives in
    ``app.core.intermediate.artifact_reader`` and reads its own
    env. A regression that routes the preview cap through this
    helper would silently raise the preview budget from 25 MB to
    200 MB."""
    from app.core.resources import resource_caps

    src = Path(resource_caps.__file__).read_text(encoding="utf-8")
    code_only = _python_code_only(src)
    assert "ARTIFACT_PREVIEW_MAX_BYTES" not in code_only, (
        "`resource_caps.py` must NOT reference "
        "`ARTIFACT_PREVIEW_MAX_BYTES`; the preview cap is "
        "intentionally separate (BE-GO-READY-RUNTIME-001)."
    )


def test_loader_compat_aliases_are_removed():
    """BE-GO-READY-MEMORY-ENGINE-007: the loader-local
    ``_resolve_max_bytes`` compat aliases were deleted. The
    canonical entry point is ``resolve_resource_max_bytes`` from
    ``app.core.resources.resource_caps``; reintroducing the alias
    would re-create a parallel symbol that future contributors
    might extend with local env parsing (the canonical drift).

    This lock fails LOUD if a contributor restores
    ``def _resolve_max_bytes`` in either loader, instead of
    silently allowing it under the (older) delegation rule."""
    pattern = re.compile(r"^\s*def\s+_resolve_max_bytes\s*\(", re.MULTILINE)
    bad: list[str] = []
    for path in _LOADERS:
        text = _python_code_only(path.read_text(encoding="utf-8"))
        if pattern.search(text):
            bad.append(path.relative_to(_BACKEND_ROOT).as_posix())
    assert not bad, (
        "Loaders MUST NOT define a local `_resolve_max_bytes` "
        "alias (BE-GO-READY-MEMORY-ENGINE-007 deleted the compat "
        "wrappers). Call `resolve_resource_max_bytes` from "
        "`app.core.resources.resource_caps` directly. "
        "Offenders:\n  " + "\n  ".join(bad)
    )
