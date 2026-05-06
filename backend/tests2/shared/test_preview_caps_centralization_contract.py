"""Source-bound contract for the centralized artifact-preview cap resolver.

BE-GO-READY-RUNTIME-002-CENTRALIZE-PREVIEW-CAP-RESOLVER.

Mirror of ``test_resource_caps_centralization_contract.py`` for
the preview-side cap. Locks that:

  - the canonical resolver lives in
    ``app.core.resources.preview_caps`` with the documented
    surface (``ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT``,
    ``ARTIFACT_PREVIEW_MAX_BYTES_ENV``,
    ``resolve_artifact_preview_max_bytes``);
  - the default is 25 MB and matches
    ``Settings.DEFAULTS["ARTIFACT_PREVIEW_MAX_BYTES"]``;
  - ``app/core/intermediate/artifact_reader.py`` consumes the
    canonical helper and does NOT redeclare a private
    ``_DEFAULT_ARTIFACT_PREVIEW_MAX_BYTES`` constant or a
    ``_resolve_max_bytes`` function;
  - the reader does NOT read ``ARTIFACT_PREVIEW_MAX_BYTES``
    from ``os.environ`` directly anymore (single point of
    parsing);
  - the preview cap and the resource cap stay separate -- the
    preview helper does NOT reference ``RESOURCE_MAX_BYTES``,
    and ``resource_caps.py`` does NOT reference
    ``ARTIFACT_PREVIEW_MAX_BYTES`` (locked by the sibling
    centralization test).

Drift mode the lock prevents
----------------------------
Pre-002 the reader owned a private ``_resolve_max_bytes`` that
duplicated the canonical
``resource_caps.resolve_resource_max_bytes`` parsing shape.
A future env-validation tweak (e.g. ``"500MB"`` suffix syntax)
would have landed in one resolver and not the other, producing
a behaviour split. Centralising both into siblings (same shape,
distinct cap/env) keeps any future tweak in lockstep without
ever merging the two values.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PREVIEW_CAPS_PY = (
    _BACKEND_ROOT / "app" / "core" / "resources" / "preview_caps.py"
)
_RESOURCE_CAPS_PY = (
    _BACKEND_ROOT / "app" / "core" / "resources" / "resource_caps.py"
)
_ARTIFACT_READER_PY = (
    _BACKEND_ROOT / "app" / "core" / "intermediate" / "artifact_reader.py"
)


def _python_code_only(text: str) -> str:
    """Strip triple-quoted docstrings and ``#`` comments.

    Same posture as the sibling
    ``test_resource_caps_centralization_contract`` helper -- a
    forbidden-token scan must not false-positive on prose that
    mentions ``ARTIFACT_PREVIEW_MAX_BYTES`` (every doc block in
    this batch references it).
    """
    out = re.sub(r'"""[\s\S]*?"""', "", text)
    out = re.sub(r"'''[\s\S]*?'''", "", out)
    out = re.sub(r"#.*$", "", out, flags=re.MULTILINE)
    return out


# ---------------------------------------------------------------------------
# 1. Helper exists with the documented surface
# ---------------------------------------------------------------------------


def test_helper_module_is_importable_with_canonical_surface():
    """The shared helper module must exist and expose the three
    names the reader + tests reference."""
    from app.core.resources import preview_caps

    assert hasattr(preview_caps, "resolve_artifact_preview_max_bytes")
    assert hasattr(preview_caps, "ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT")
    assert hasattr(preview_caps, "ARTIFACT_PREVIEW_MAX_BYTES_ENV")
    assert preview_caps.ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT == 25 * 1024 * 1024
    assert (
        preview_caps.ARTIFACT_PREVIEW_MAX_BYTES_ENV
        == "ARTIFACT_PREVIEW_MAX_BYTES"
    )


def test_helper_default_when_env_unset(monkeypatch):
    """No env -> the documented 25 MB default."""
    from app.core.resources.preview_caps import (
        ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT,
        resolve_artifact_preview_max_bytes,
    )

    monkeypatch.delenv("ARTIFACT_PREVIEW_MAX_BYTES", raising=False)
    assert (
        resolve_artifact_preview_max_bytes()
        == ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
    )


def test_helper_env_override_is_honored(monkeypatch):
    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "65536")
    from app.core.resources.preview_caps import (
        resolve_artifact_preview_max_bytes,
    )

    assert resolve_artifact_preview_max_bytes() == 65536


@pytest.mark.parametrize("bad_value", ["", "not-an-int", "0", "-1"])
def test_helper_invalid_or_nonpositive_falls_back(monkeypatch, bad_value):
    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", bad_value)
    from app.core.resources.preview_caps import (
        ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT,
        resolve_artifact_preview_max_bytes,
    )

    assert (
        resolve_artifact_preview_max_bytes()
        == ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
    )


def test_helper_accepts_explicit_env_mapping():
    """Mirror of the resource-caps escape hatch: pure dict env
    isolates a test from process state. Production reads live
    process env."""
    from app.core.resources.preview_caps import (
        ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT,
        resolve_artifact_preview_max_bytes,
    )

    assert (
        resolve_artifact_preview_max_bytes(env={})
        == ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
    )
    assert resolve_artifact_preview_max_bytes(
        env={"ARTIFACT_PREVIEW_MAX_BYTES": "12345"},
    ) == 12345
    assert resolve_artifact_preview_max_bytes(
        env={"ARTIFACT_PREVIEW_MAX_BYTES": "garbage"},
    ) == ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT


def test_settings_default_matches_helper_default():
    """``Settings.DEFAULTS["ARTIFACT_PREVIEW_MAX_BYTES"]`` MUST match
    ``preview_caps.ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT``. Drift here
    means the resolver and the settings layer disagree about the
    cap, which would surprise operators tuning via env."""
    from app.config import Settings
    from app.core.resources.preview_caps import (
        ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT,
    )

    assert (
        Settings.DEFAULTS["ARTIFACT_PREVIEW_MAX_BYTES"]
        == ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
        == 25 * 1024 * 1024
    )


# ---------------------------------------------------------------------------
# 2. Source-bound: artifact_reader.py MUST consume the canonical helper
# ---------------------------------------------------------------------------


def test_artifact_reader_imports_the_shared_helper():
    """``artifact_reader.py`` MUST import
    ``resolve_artifact_preview_max_bytes`` from
    ``app.core.resources.preview_caps``. A regression that
    re-introduces a private resolver would also drop this import,
    so failing the import check is sufficient."""
    text = _ARTIFACT_READER_PY.read_text(encoding="utf-8")
    assert (
        "from app.core.resources.preview_caps import" in text
        and "resolve_artifact_preview_max_bytes" in text
    ), (
        "artifact_reader.py must import "
        "`resolve_artifact_preview_max_bytes` from "
        "`app.core.resources.preview_caps` (BE-GO-READY-RUNTIME-002 "
        "centralized the cap resolver)."
    )


def test_artifact_reader_does_not_read_env_directly():
    """``artifact_reader.py`` MUST NOT contain a direct
    ``os.environ.get("ARTIFACT_PREVIEW_MAX_BYTES"...)`` /
    ``os.getenv("ARTIFACT_PREVIEW_MAX_BYTES"...)`` call. The shared
    helper is the single point that reads the env; a regression
    that re-introduces a local read is the canonical drift the
    helper exists to prevent."""
    forbidden_patterns = (
        re.compile(
            r"""os\.environ\s*\.\s*get\s*\(\s*['"]ARTIFACT_PREVIEW_MAX_BYTES['"]"""
        ),
        re.compile(
            r"""os\.environ\[\s*['"]ARTIFACT_PREVIEW_MAX_BYTES['"]\s*\]"""
        ),
        re.compile(
            r"""os\.getenv\s*\(\s*['"]ARTIFACT_PREVIEW_MAX_BYTES['"]"""
        ),
        re.compile(
            r"""_os\.environ\s*\.\s*get\s*\(\s*['"]ARTIFACT_PREVIEW_MAX_BYTES['"]"""
        ),
        re.compile(
            r"""_os\.getenv\s*\(\s*['"]ARTIFACT_PREVIEW_MAX_BYTES['"]"""
        ),
    )

    text = _python_code_only(_ARTIFACT_READER_PY.read_text(encoding="utf-8"))
    bad: list[str] = []
    for pat in forbidden_patterns:
        m = pat.search(text)
        if m:
            bad.append(m.group(0))

    assert not bad, (
        "artifact_reader.py MUST NOT read `ARTIFACT_PREVIEW_MAX_BYTES` "
        "directly from the env. Use "
        "`app.core.resources.preview_caps.resolve_artifact_preview_max_bytes`. "
        "Offenders:\n  " + "\n  ".join(bad)
    )


def test_artifact_reader_does_not_redeclare_default_constant():
    """``artifact_reader.py`` MUST NOT redeclare a private
    ``_DEFAULT_ARTIFACT_PREVIEW_MAX_BYTES = ... * 1024 * 1024``
    integer constant. The canonical default lives in
    ``preview_caps.ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT``; a parallel
    constant is a drift surface."""
    pattern = re.compile(
        r"_DEFAULT_ARTIFACT_PREVIEW_MAX_BYTES\s*=\s*\d+\s*\*"
    )
    text = _python_code_only(_ARTIFACT_READER_PY.read_text(encoding="utf-8"))
    m = pattern.search(text)
    assert m is None, (
        "artifact_reader.py MUST NOT redeclare "
        "`_DEFAULT_ARTIFACT_PREVIEW_MAX_BYTES`; use "
        "`preview_caps.ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT` instead. "
        f"Offender: {m.group(0) if m else None}"
    )


def test_artifact_reader_compat_alias_is_removed():
    """BE-GO-READY-RUNTIME-002: the reader-local
    ``_resolve_max_bytes`` was deleted. The canonical entry point is
    ``resolve_artifact_preview_max_bytes`` from
    ``app.core.resources.preview_caps``; reintroducing the alias
    would re-create a parallel symbol that future contributors
    might extend with local env parsing (the canonical drift).

    This lock fails LOUD if a contributor restores
    ``def _resolve_max_bytes`` in ``artifact_reader.py``, instead
    of silently allowing it under a delegation rule."""
    pattern = re.compile(
        r"^\s*def\s+_resolve_max_bytes\s*\(", re.MULTILINE
    )
    text = _python_code_only(_ARTIFACT_READER_PY.read_text(encoding="utf-8"))
    m = pattern.search(text)
    assert m is None, (
        "artifact_reader.py MUST NOT define a local "
        "`_resolve_max_bytes` alias (RUNTIME-002 deleted the "
        "compat wrapper). Call "
        "`resolve_artifact_preview_max_bytes` from "
        "`app.core.resources.preview_caps` directly."
    )


# ---------------------------------------------------------------------------
# 3. Resource cap and preview cap stay separate
# ---------------------------------------------------------------------------


def test_preview_caps_does_not_reference_resource_max_bytes():
    """``preview_caps.py`` is the single owner of the preview cap
    parsing surface. It MUST NOT reference ``RESOURCE_MAX_BYTES``
    in executable code -- the two caps are siblings, not stacked.
    A regression that routed the preview default through the
    resource resolver would silently lift the preview budget from
    25 MB to 200 MB."""
    src = _PREVIEW_CAPS_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(src)
    assert "RESOURCE_MAX_BYTES" not in code_only, (
        "`preview_caps.py` must NOT reference `RESOURCE_MAX_BYTES`; "
        "the resource cap is intentionally separate (sibling "
        "module `resource_caps.py`)."
    )


def test_resource_caps_does_not_reference_preview_cap():
    """Symmetric guard. ``resource_caps.py`` must NOT reference
    ``ARTIFACT_PREVIEW_MAX_BYTES`` in executable code; the preview
    cap lives in the sibling ``preview_caps.py``. The existing
    ``test_resource_caps_centralization_contract.py`` already locks
    this; we mirror it here so the preview-cap suite has end-to-end
    coverage of the separation."""
    src = _RESOURCE_CAPS_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(src)
    assert "ARTIFACT_PREVIEW_MAX_BYTES" not in code_only, (
        "`resource_caps.py` must NOT reference "
        "`ARTIFACT_PREVIEW_MAX_BYTES`; the preview cap is "
        "intentionally separate (sibling module "
        "`preview_caps.py`)."
    )


def test_preview_caps_does_not_import_resource_caps():
    """The two helpers are siblings. ``preview_caps.py`` MUST NOT
    import ``resource_caps`` -- if it ever did, a future
    consolidation could silently merge the two defaults."""
    text = _PREVIEW_CAPS_PY.read_text(encoding="utf-8")
    forbidden = (
        "from app.core.resources.resource_caps",
        "import app.core.resources.resource_caps",
        "from app.core.resources import resource_caps",
    )
    offenders = [s for s in forbidden if s in text]
    assert not offenders, (
        "preview_caps.py must NOT import resource_caps -- the two "
        f"helpers are siblings, not stacked. Offenders: {offenders}"
    )


def test_default_values_are_distinct_and_correct():
    """Behavioural lock: the two defaults MUST NOT collapse to the
    same value. Resource cap = 200 MB; preview cap = 25 MB. A
    regression that synced them silently would defeat the
    concurrency-sensitive preview budget."""
    from app.core.resources.preview_caps import (
        ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT,
    )
    from app.core.resources.resource_caps import RESOURCE_MAX_BYTES_DEFAULT

    assert ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT == 25 * 1024 * 1024
    assert RESOURCE_MAX_BYTES_DEFAULT == 200 * 1024 * 1024
    assert ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT < RESOURCE_MAX_BYTES_DEFAULT


def test_env_names_are_distinct():
    """Behavioural lock: the two env names MUST be distinct.
    Sharing the env would mean ops cannot tune one cap without
    accidentally tuning the other."""
    from app.core.resources.preview_caps import (
        ARTIFACT_PREVIEW_MAX_BYTES_ENV,
    )
    from app.core.resources.resource_caps import RESOURCE_MAX_BYTES_ENV

    assert ARTIFACT_PREVIEW_MAX_BYTES_ENV == "ARTIFACT_PREVIEW_MAX_BYTES"
    assert RESOURCE_MAX_BYTES_ENV == "RESOURCE_MAX_BYTES"
    assert ARTIFACT_PREVIEW_MAX_BYTES_ENV != RESOURCE_MAX_BYTES_ENV


def test_resource_max_bytes_env_does_not_drive_preview(monkeypatch):
    """End-to-end behavioural lock: setting ``RESOURCE_MAX_BYTES``
    MUST NOT change the preview cap. Catches a regression where
    the preview resolver accidentally consults the resource env."""
    monkeypatch.delenv("ARTIFACT_PREVIEW_MAX_BYTES", raising=False)
    monkeypatch.setenv("RESOURCE_MAX_BYTES", "1024")
    from app.core.resources.preview_caps import (
        ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT,
        resolve_artifact_preview_max_bytes,
    )

    assert (
        resolve_artifact_preview_max_bytes()
        == ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
    )


def test_artifact_preview_env_does_not_drive_resource_cap(monkeypatch):
    """Symmetric end-to-end behavioural lock: setting
    ``ARTIFACT_PREVIEW_MAX_BYTES`` MUST NOT change the resource
    cap."""
    monkeypatch.delenv("RESOURCE_MAX_BYTES", raising=False)
    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", "1024")
    from app.core.resources.resource_caps import (
        RESOURCE_MAX_BYTES_DEFAULT,
        resolve_resource_max_bytes,
    )

    assert resolve_resource_max_bytes() == RESOURCE_MAX_BYTES_DEFAULT
