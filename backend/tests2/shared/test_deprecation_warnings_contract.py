"""
Contract test R-21/R-22 -- DeprecationWarning emit-once per legacy alias.

Verifies that the remaining legacy alias helpers in the codebase emit a
"""

from __future__ import annotations

import warnings as _w
from dataclasses import dataclass
from typing import Any, Callable
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Alias matrix


@dataclass(frozen=True)
class _AliasCase:
    """One row of the alias contract table."""

    rid: str
    label: str
    # Lazy resolvers so failure to import one alias does not mask the others.
    helper: Callable[[], Any]      # the lru_cache'd emit helper
    invoke: Callable[[], Any]      # invokes the wrapper as a caller would
    expected_in_message: tuple[str, ...]
    expected_message_signature: str


def _get_urby_loopback_helper() -> Any:
    from app.core.security import outbound_url
    return outbound_url._warn_urby_loopback_alias_once


def _invoke_urby_loopback() -> Any:
    from app.core.security import outbound_url
    return outbound_url._warn_urby_loopback_alias_once()


def _get_object_storage_helper() -> Any:
    from app.infrastructure.storage import cloud_backends
    return cloud_backends._warn_get_object_storage_deprecated_once


def _invoke_object_storage() -> Any:
    from app.infrastructure.storage import cloud_backends
    fake_storage = object()
    with patch.object(
        cloud_backends, "get_runtime_object_storage",
        return_value=fake_storage,
    ):
        return cloud_backends.get_object_storage()


_ALIAS_CASES: tuple[_AliasCase, ...] = (
    _AliasCase(
        rid="R-21",
        label="urby_loopback_env_alias",
        helper=_get_urby_loopback_helper,
        invoke=_invoke_urby_loopback,
        expected_in_message=(
            "URBY_ALLOW_LOOPBACK_OUTBOUND",
            "[DEPRECATED]",
            "PLENORA_ALLOW_LOOPBACK_OUTBOUND",
            "2026-Q4",
        ),
        expected_message_signature="URBY_ALLOW_LOOPBACK_OUTBOUND",
    ),
    _AliasCase(
        rid="R-22",
        label="get_object_storage_alias",
        helper=_get_object_storage_helper,
        invoke=_invoke_object_storage,
        expected_in_message=(
            "get_object_storage",
            "2026-Q4",
        ),
        expected_message_signature="get_object_storage",
    ),
)


def _ids(cases: tuple[_AliasCase, ...]) -> list[str]:
    return [f"{c.rid}/{c.label}" for c in cases]


# Per-alias contract: emit + content


@pytest.mark.parametrize("case", _ALIAS_CASES, ids=_ids(_ALIAS_CASES))
def test_alias_emits_deprecation_warning_with_canonical_message(case: _AliasCase):
    """Each legacy alias must emit a Python DeprecationWarning whose
    message names the alias, the migration target, and the removal target."""
    case.helper().cache_clear()
    with pytest.warns(DeprecationWarning) as record:
        case.invoke()
    assert len(record) == 1, (
        f"{case.rid} {case.label}: expected exactly 1 DeprecationWarning "
        f"on first invocation, got {len(record)}."
    )
    msg = str(record[0].message)
    for needle in case.expected_in_message:
        assert needle in msg, (
            f"{case.rid} {case.label}: DeprecationWarning message must "
            f"contain {needle!r}. Got: {msg!r}"
        )
    # The R-22 wrapper allows two equivalent migration targets
    # (`get_runtime_object_storage` OR `get_backup_object_storage`).
    if case.rid == "R-22":
        assert (
            "get_runtime_object_storage" in msg
            or "get_backup_object_storage" in msg
        ), (
            f"R-22 {case.label}: message must point at the explicit "
            "runtime/backup target, not just the alias name."
        )


# Per-alias contract: emit-once cache


@pytest.mark.parametrize("case", _ALIAS_CASES, ids=_ids(_ALIAS_CASES))
def test_alias_emit_is_cached_once_per_process(case: _AliasCase):
    """`lru_cache(maxsize=1)` guarantees emit-once per process. The second
    invocation must NOT raise a DeprecationWarning whose message matches
    this alias's signature."""
    case.helper().cache_clear()
    # First invocation: emit.
    with pytest.warns(DeprecationWarning):
        case.invoke()
    # Second invocation: cache hit, no further warning for this alias.
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        case.invoke()
    matches = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
        and case.expected_message_signature in str(w.message)
    ]
    assert not matches, (
        f"{case.rid} {case.label}: emit-once violated. "
        f"Caught {len(matches)} warning(s) on second invocation."
    )


# Cross-cutting: independent caches across the four aliases


def test_each_alias_has_an_independent_cache():
    """Triggering one alias must not silence another. This catches
    accidental shared `lru_cache` reuse across helpers."""
    for case in _ALIAS_CASES:
        case.helper().cache_clear()

    # Trigger only the URBY loopback alias.
    with pytest.warns(DeprecationWarning):
        _invoke_urby_loopback()

    # The object storage alias must still emit on its first invocation.
    with pytest.warns(DeprecationWarning) as record:
        _invoke_object_storage()
    assert "get_object_storage" in str(record[0].message)


def test_api_utils_compat_module_is_tombstoned():
    """R-23/R-24 API utility shim was removed after migrating callers."""
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[2]

    assert not (backend_root / "app" / "shared" / "api_utils.py").exists()


def test_prepare_headers_uses_canonical_cached_oauth2_token():
    """OAuth auth headers must use the canonical cache helper directly."""
    from app.shared.connections import oauth

    calls: list[dict[str, Any]] = []

    def _fake_cached(auth: dict[str, Any]) -> str:
        calls.append(dict(auth))
        return "tok-1"

    headers = oauth.prepare_headers(
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://auth.example/token",
            "client_id": "client-1",
        },
        oauth2_token_getter=_fake_cached,
    )

    assert headers["Authorization"] == "Bearer tok-1"
    assert calls == [
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://auth.example/token",
            "client_id": "client-1",
        }
    ]
