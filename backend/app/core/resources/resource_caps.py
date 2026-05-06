"""Shared cap resolver — single source of truth for
``RESOURCE_MAX_BYTES``.

BE-GO-READY-MEMORY-ENGINE-006-CENTRALIZE-RESOURCE-CAPS.

Background
----------
Pre-006 the result-blob loader and the file-source-blob loader
each owned a private ``_resolve_max_bytes()`` that read the
``RESOURCE_MAX_BYTES`` env, did the same int-coerce + non-positive
fallback, and returned the same 200 MB default. Two copies, one
contract; the canonical drift mode is a future env-validation tweak
(e.g. allow ``MAX_BYTES`` suffix parsing) landing in one loader and
not the other.

This module owns the env-resolution surface for every cap that maps
to ``RESOURCE_MAX_BYTES``. ``ARTIFACT_PREVIEW_MAX_BYTES`` is
DELIBERATELY NOT routed through this helper -- preview is a
separate operator-tunable budget (concurrency-sensitive on the API
thread-pool) and lives in
``app/core/intermediate/artifact_reader.py`` with its own
resolver.

Anti-god-engine
---------------
Pure leaf module:

  - stdlib only (``os.environ`` is the default ``env`` argument);
  - zero ``app.config`` imports (the loader leaves stay clean);
  - zero ``pandas`` / ``geopandas`` / ``fiona`` / ``polars`` /
    ``fastapi`` / ``app.core.auth`` / domain-engine imports.

Locked by the existing
``tests2/shared/test_resource_engine_anti_god_engine_lock.py``
banned-import scan and by the new source-bound contract added in
``tests2/shared/test_resource_caps_centralization_contract.py``.

Default
-------
200 MB (BE-GO-READY-MEMORY-005). Mirrors
``Settings.DEFAULTS["RESOURCE_MAX_BYTES"]`` without importing
``app.config`` from a leaf loader. The two values are kept in sync
by the install-doc guard tests.

Failure mode
------------
Invalid (non-int), zero, or negative env values fall back to the
200 MB default. The helper NEVER raises -- a misconfigured env
must not break a blob fetch; the operator-visible signal is
``Settings.DEFAULTS`` continuing to apply.
"""

from __future__ import annotations

import os as _os
from typing import Mapping

__all__ = [
    "RESOURCE_MAX_BYTES_DEFAULT",
    "RESOURCE_MAX_BYTES_ENV",
    "resolve_resource_max_bytes",
]


RESOURCE_MAX_BYTES_DEFAULT: int = 200 * 1024 * 1024  # 200 MB
RESOURCE_MAX_BYTES_ENV: str = "RESOURCE_MAX_BYTES"


def resolve_resource_max_bytes(
    env: Mapping[str, str] | None = None,
) -> int:
    """Resolve the resource-blob byte cap.

    Args:
        env: optional mapping with ``str -> str`` semantics.
            Defaults to ``os.environ``. Tests can pass a plain
            ``dict`` for isolation; production reads live env.
            Read at each call so monkeypatch / runtime override
            takes effect without a process restart.

    Returns:
        The cap in bytes. Override via the ``RESOURCE_MAX_BYTES``
        env entry. Invalid (non-int), zero, or negative values
        fall back to ``RESOURCE_MAX_BYTES_DEFAULT`` (200 MB).
    """
    source = _os.environ if env is None else env
    raw = source.get(RESOURCE_MAX_BYTES_ENV)
    if not raw:
        return RESOURCE_MAX_BYTES_DEFAULT
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return RESOURCE_MAX_BYTES_DEFAULT
    return v if v > 0 else RESOURCE_MAX_BYTES_DEFAULT
