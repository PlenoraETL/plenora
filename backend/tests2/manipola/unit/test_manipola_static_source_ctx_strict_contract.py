"""Static-source ctx-strict contract (BE-GO-READY-TRANSFORMATION-ENGINE-008).

Proves that ``_carica_sorgenti_statiche`` does NOT read
``self._current_group_id``:

  - stale singleton is invisible to this chain (sentinel used instead);
  - explicit ``group_id`` kwarg is forwarded verbatim to SourceLoader;
  - absent both, the ``"_static_"`` sentinel is used (non-authorizing).
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from app.core.manipola.executor import ManipolaExecutor
from tests2.helpers.builders import build_canvas, build_node


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
    pytest.mark.security,
    pytest.mark.contract,
]


def _canvas_with_static(fields=None):
    return build_canvas(
        build_node("staticValues", fields=fields or [{"name": "x", "value": "1"}]),
    )


class _StubLoader:
    """Captures the expected_group_id passed to SourceLoader.load."""

    def __init__(self):
        self.calls: list[str] = []

    def load(self, ref, *, expected_group_id, **kw):
        self.calls.append(expected_group_id)
        return pd.DataFrame([{"x": "1"}])


def test_static_source_does_not_read_stale_singleton():
    """TE-008: stale ``_current_group_id`` MUST NOT reach SourceLoader.load.

    Pre-008 the singleton leaked via ``_resolve_executing_group_id``.
    Post-008 the sentinel ``"_static_"`` is used instead."""
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = "stale-grp-LEAK"

    stub = _StubLoader()
    with patch("app.core.artifacts.SourceLoader", lambda: stub):
        result = executor._carica_sorgenti_statiche(_canvas_with_static(), {})

    assert result is None, f"Expected success (None), got {result!r}"
    assert len(stub.calls) == 1
    assert stub.calls[0] != "stale-grp-LEAK", (
        "_carica_sorgenti_statiche MUST NOT forward the stale singleton "
        "_current_group_id to SourceLoader (TE-008 cut)."
    )
    assert stub.calls[0] == "_static_", (
        f"Absent explicit group_id, sentinel '_static_' expected. "
        f"Got {stub.calls[0]!r}."
    )


def test_static_source_forwards_explicit_group_id():
    """TE-008: explicit ``group_id`` is forwarded verbatim, not replaced by sentinel."""
    executor = ManipolaExecutor(project_service=object())

    stub = _StubLoader()
    with patch("app.core.artifacts.SourceLoader", lambda: stub):
        result = executor._carica_sorgenti_statiche(
            _canvas_with_static(), {}, group_id="grp-explicit"
        )

    assert result is None
    assert stub.calls == ["grp-explicit"], (
        f"Explicit group_id must be forwarded verbatim. Got {stub.calls!r}."
    )


def test_static_source_uses_sentinel_when_no_group_id_and_no_singleton():
    """TE-008: sentinel ``'_static_'`` is used when group_id is absent
    and ``_current_group_id`` is not set."""
    executor = ManipolaExecutor(project_service=object())
    # No _current_group_id attribute at all.

    stub = _StubLoader()
    with patch("app.core.artifacts.SourceLoader", lambda: stub):
        result = executor._carica_sorgenti_statiche(_canvas_with_static(), {})

    assert result is None
    assert stub.calls == ["_static_"], (
        f"Sentinel '_static_' expected when no group_id provided. Got {stub.calls!r}."
    )


def test_static_source_uses_sentinel_not_none_when_singleton_is_none():
    """TE-008: even when ``_current_group_id`` is explicitly set to None,
    the sentinel ``'_static_'`` is used (not None, which would fail the
    SourceLoader non-empty guard)."""
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = None

    stub = _StubLoader()
    with patch("app.core.artifacts.SourceLoader", lambda: stub):
        result = executor._carica_sorgenti_statiche(_canvas_with_static(), {})

    assert result is None
    assert stub.calls == ["_static_"], (
        f"Sentinel '_static_' must replace None singleton. Got {stub.calls!r}."
    )
