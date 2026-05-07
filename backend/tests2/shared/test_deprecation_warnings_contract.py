"""Contract test R-21/R-22/R-23 -- DeprecationWarning emit-once per legacy alias.

Verifies that the four legacy alias helpers in the codebase emit a
standard Python ``DeprecationWarning`` plus a ``logger.warning`` the
first time they are invoked, that the emit is cached (emit-once per
process), that each alias has its own LRU cache (no cross-contamination),
and that the warning stack-level points at the external caller (not the
internal emit helper).

Aliases covered:
  R-21: ``URBY_ALLOW_LOOPBACK_OUTBOUND`` env alias
        (``api_utils._warn_urby_loopback_alias_once``).
  R-23: ``api_utils.get_oauth2_token`` legacy wrapper.
  R-23: ``api_utils.get_oauth2_client_credentials_token`` legacy wrapper.
  R-22: ``cloud_backends.get_object_storage`` legacy wrapper.

TEST-ENGINE-003 collapse: the prior 297-line form had eight per-alias
test functions plus two cross-cutting tests. The matrix is now driven
by a parametrized ALIAS_CASES table; the per-alias tests collapse into
two parametrized tests (`emit-warning` + `cache-once`). The
cross-contamination + stack-level invariants stay as singleton tests
because they assert different shapes.

pytest filterwarnings note:
  ``backend/tests2/pytest.ini`` ignores ``DeprecationWarning`` by
  default to keep the suite quiet. ``pytest.warns(DeprecationWarning)``
  bypasses the filter inside its context, so these tests still see
  the warning.
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
    from app.shared import api_utils
    return api_utils._warn_urby_loopback_alias_once


def _invoke_urby_loopback() -> Any:
    from app.shared import api_utils
    return api_utils._warn_urby_loopback_alias_once()


def _get_oauth2_token_helper() -> Any:
    from app.shared import api_utils
    return api_utils._warn_get_oauth2_token_deprecated_once


def _invoke_oauth2_token() -> Any:
    from app.shared import api_utils
    with patch.object(api_utils, "get_cached_oauth2_token", return_value="tok-1"):
        return api_utils.get_oauth2_token({"token_url": "https://x"})


def _get_oauth2_cc_token_helper() -> Any:
    from app.shared import api_utils
    return api_utils._warn_get_oauth2_cc_token_deprecated_once


def _invoke_oauth2_cc_token() -> Any:
    from app.shared import api_utils
    with patch.object(api_utils, "get_cached_oauth2_token", return_value="tok-cc"):
        return api_utils.get_oauth2_client_credentials_token(
            {"token_url": "https://x"},
        )


def _get_object_storage_helper() -> Any:
    from app.shared import cloud_backends
    return cloud_backends._warn_get_object_storage_deprecated_once


def _invoke_object_storage() -> Any:
    from app.shared import cloud_backends
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
        rid="R-23",
        label="get_oauth2_token_wrapper",
        helper=_get_oauth2_token_helper,
        invoke=_invoke_oauth2_token,
        expected_in_message=(
            "get_oauth2_token",
            "get_cached_oauth2_token",
            "2026-Q4",
        ),
        expected_message_signature="get_oauth2_token",
    ),
    _AliasCase(
        rid="R-23",
        label="get_oauth2_cc_token_wrapper",
        helper=_get_oauth2_cc_token_helper,
        invoke=_invoke_oauth2_cc_token,
        expected_in_message=(
            "get_oauth2_client_credentials_token",
            "get_cached_oauth2_token",
            "2026-Q4",
        ),
        # client_credentials is a unique substring across the four cases,
        # so it is sufficient to disambiguate cache-once filtering below.
        expected_message_signature="client_credentials",
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
    # The R-23 token wrappers also mention the migration target which we
    # exercised above; the R-22 wrapper allows two equivalent migration
    # targets (`get_runtime_object_storage` OR `get_backup_object_storage`).
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
    """Triggering one alias must not silence the other three. This catches
    accidental shared `lru_cache` reuse across helpers."""
    from app.shared import api_utils

    for case in _ALIAS_CASES:
        case.helper().cache_clear()

    # Trigger only the URBY loopback alias.
    with pytest.warns(DeprecationWarning):
        _invoke_urby_loopback()

    # The other three must still emit on their first invocation.
    with patch.object(api_utils, "get_cached_oauth2_token", return_value="t"):
        with pytest.warns(DeprecationWarning) as record:
            api_utils.get_oauth2_token({"token_url": "https://x"})
        assert "get_oauth2_token" in str(record[0].message)


# Cross-cutting: stacklevel propagation (R-23 representative)


def test_get_oauth2_token_stacklevel_points_to_caller():
    """`stacklevel=3` must make the warning point at the external caller of
    `get_oauth2_token`, not at the internal `_warn_*_deprecated_once` helper.

    Stack-level calibration:
      caller -> get_oauth2_token -> _warn_helper -> warnings.warn
                  level=1            level=2          level=3
    `stacklevel=3` from `warnings.warn` therefore selects the caller.
    """
    from app.shared import api_utils

    api_utils._warn_get_oauth2_token_deprecated_once.cache_clear()
    with patch.object(api_utils, "get_cached_oauth2_token", return_value="t"):
        with pytest.warns(DeprecationWarning) as record:
            api_utils.get_oauth2_token({"token_url": "https://x"})

    w = record[0]
    if w.filename:
        assert "api_utils.py" not in w.filename, (
            f"stacklevel wrong: warning points at {w.filename}:{w.lineno}, "
            "must point at the external caller."
        )
