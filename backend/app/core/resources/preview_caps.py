"""Shared cap resolver - single source of truth for
``ARTIFACT_PREVIEW_MAX_BYTES``.

BE-GO-READY-RUNTIME-002-CENTRALIZE-PREVIEW-CAP-RESOLVER.

Background
----------
The intermediate-artifact preview path (``artifact_reader.py``)
historically owned its OWN ``_resolve_max_bytes`` that read
``ARTIFACT_PREVIEW_MAX_BYTES`` from ``os.environ``, did the
canonical int-coerce + non-positive fallback, and returned a
25 MB default. The result-blob loader had the SAME shape pre
``MEMORY-ENGINE-006``, before that batch consolidated it into
``resource_caps.resolve_resource_max_bytes``. RUNTIME-002
applies the same consolidation to the preview cap - same
parsing shape, but DELIBERATELY a separate module + separate
env + separate default so the two caps cannot drift into each
other.

Why a SEPARATE module from ``resource_caps.py``
-----------------------------------------------
``ARTIFACT_PREVIEW_MAX_BYTES`` (default 25 MB) is the cap on
the FastAPI preview path: N concurrent tabs / refreshes share
the same thread pool, so the per-request budget must stay
small. ``RESOURCE_MAX_BYTES`` (default 200 MB post
``MEMORY-ENGINE-005``) is the cap on background / non-preview
blob loaders (result blobs, file-source blobs) that run under
the worker, NOT under the API request budget. Routing the
preview cap through ``resource_caps`` would silently lift the
preview budget from 25 MB to 200 MB and re-couple the two
budgets. The
``test_resource_caps_centralization_contract.py::test_artifact_preview_cap_is_not_collapsed_into_resource_max_bytes``
guard locks that the two caps stay distinct.

This module mirrors ``resource_caps.py`` line-for-line in shape
(constants + ``resolve_*`` function), so a future env-validation
tweak (e.g. ``"500MB"`` suffix syntax) lands in one canonical
place and applies to both caps in lockstep, without ever merging
their *values*.

Anti-god-engine
---------------
Pure leaf module:

  - stdlib only (``os.environ`` is the default ``env`` argument);
  - zero ``app.config`` imports (the reader leaves stay clean);
  - zero ``pandas`` / ``geopandas`` / ``fiona`` / ``polars`` /
    ``fastapi`` / ``app.core.auth`` / domain-engine imports;
  - zero imports of the resource-cap resolver (the two helpers
    are intentionally siblings, not stacked).

Locked by the existing
``tests2/shared/test_resource_engine_anti_god_engine_lock.py``
banned-import scan and by the new source-bound contract added in
``tests2/shared/test_preview_caps_centralization_contract.py``.

Default
-------
25 MB (``BE-GO-READY-RUNTIME-001-PREVIEW-MAX-BYTES-CAP``).
Mirrors ``Settings.DEFAULTS["ARTIFACT_PREVIEW_MAX_BYTES"]`` without
importing ``app.config`` from a leaf reader. The two values are
kept in sync by the preview-cap test suite.

Failure mode
------------
Invalid (non-int), zero, or negative env values fall back to the
25 MB default. The helper NEVER raises - a misconfigured env
must not break a preview request; the operator-visible signal is
``Settings.DEFAULTS`` continuing to apply.
"""

from __future__ import annotations

import os as _os
from typing import Mapping

__all__ = [
    "ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT",
    "ARTIFACT_PREVIEW_MAX_BYTES_ENV",
    "resolve_artifact_preview_max_bytes",
]


ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT: int = 25 * 1024 * 1024  # 25 MB
ARTIFACT_PREVIEW_MAX_BYTES_ENV: str = "ARTIFACT_PREVIEW_MAX_BYTES"


def resolve_artifact_preview_max_bytes(
    env: Mapping[str, str] | None = None,
) -> int:
    """Resolve the artifact-preview byte cap.

    Args:
        env: optional mapping with ``str -> str`` semantics.
            Defaults to ``os.environ``. Tests can pass a plain
            ``dict`` for isolation; production reads live env.
            Read at each call so monkeypatch / runtime override
            takes effect without a process restart.

    Returns:
        The cap in bytes. Override via the
        ``ARTIFACT_PREVIEW_MAX_BYTES`` env entry. Invalid
        (non-int), zero, or negative values fall back to
        ``ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT`` (25 MB).
    """
    source = _os.environ if env is None else env
    raw = source.get(ARTIFACT_PREVIEW_MAX_BYTES_ENV)
    if not raw:
        return ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
    return v if v > 0 else ARTIFACT_PREVIEW_MAX_BYTES_DEFAULT
