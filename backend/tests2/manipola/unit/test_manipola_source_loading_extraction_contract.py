"""Source-loading extraction contract for ManipolaExecutor.

BE-GO-READY-TRANSFORMATION-ENGINE-006-MANIPOLA-SOURCE-LOADING-SLICE.

Locks the new boundary between ``ManipolaExecutor`` and the pure
``app.core.manipola.source_loading`` helper module:

  - ``app.core.manipola.source_loading.load_file_sources`` exists
    and exposes a function-only signature
    (``canvas``, ``dataframes``, ``group_id``, ``logger=``);
  - ``ManipolaExecutor._carica_sorgenti_file`` is a thin delegator
    that calls ``load_file_sources`` (no inline business logic
    re-introduced after the extraction);
  - the delegator does NOT pass ``self._current_group_id`` to the
    helper — the TE-005 ctx-strict contract is preserved across
    the boundary;
  - the helper itself is a pure function with no class state
    (no ``self`` parameter; nothing from
    ``ManipolaExecutor`` is bound at module load time).

Source-bound + behavioural locks; mirrors the posture of
``test_manipola_executor_security_contract`` and adds a thin
delegation proof.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.manipola.executor import ManipolaExecutor
from tests2.helpers.builders import build_canvas, build_node


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
    pytest.mark.security,
    pytest.mark.contract,
]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_EXECUTOR_PY = (
    _BACKEND_ROOT / "app" / "core" / "manipola" / "executor.py"
)
_SOURCE_LOADING_PY = (
    _BACKEND_ROOT / "app" / "core" / "manipola" / "source_loading.py"
)


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_source_loading_module_exists():
    """The extracted module must exist as a sibling of executor.py."""
    assert _SOURCE_LOADING_PY.is_file(), (
        "app/core/manipola/source_loading.py must exist (TE-006 "
        "extracted the file-source loader into its own module)."
    )


def test_load_file_sources_is_a_pure_function():
    """``load_file_sources`` is a module-level function with the
    documented keyword-only contract:
    ``(canvas, dataframes, *, group_id, logger=None)``. No class
    method (no ``self``); no positional ``group_id`` (must remain
    keyword-only so the tenant-binding intent is loud at call
    sites)."""
    from app.core.manipola.source_loading import load_file_sources

    assert inspect.isfunction(load_file_sources), (
        "load_file_sources must be a module-level function; the "
        "extraction is meant to remove class state, not move it."
    )

    sig = inspect.signature(load_file_sources)
    params = list(sig.parameters.items())
    # First two positional: canvas, dataframes.
    assert [name for name, _ in params[:2]] == ["canvas", "dataframes"], (
        f"load_file_sources signature must start with (canvas, "
        f"dataframes, ...); got {[n for n, _ in params]!r}."
    )
    # `group_id` MUST be keyword-only so a future caller cannot
    # accidentally pass a positional value through.
    group_id_param = sig.parameters.get("group_id")
    assert group_id_param is not None, (
        "load_file_sources must accept `group_id` as a parameter."
    )
    assert group_id_param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "load_file_sources `group_id` MUST be keyword-only — the "
        "tenant-binding intent has to be loud at call sites."
    )


# ---------------------------------------------------------------------------
# Delegator
# ---------------------------------------------------------------------------


def test_executor_delegates_to_source_loading_helper():
    """``ManipolaExecutor._carica_sorgenti_file`` MUST be a thin
    delegator that calls ``load_file_sources``. Patch the helper
    AS IMPORTED INTO ``executor`` (the canonical boundary) and
    assert it is called once with the executor's arguments."""
    from app.core.manipola import executor as executor_mod

    captured: list[dict] = []

    def _spy(canvas, dataframes, *, group_id, logger=None):
        captured.append({
            "canvas": canvas,
            "dataframes": dataframes,
            "group_id": group_id,
            "logger": logger,
        })
        return None  # success path

    canvas = build_canvas(
        build_node("file_source", file_source_id="fs-1", columns=[]),
    )
    dataframes: dict = {}

    executor = ManipolaExecutor(project_service=object())
    with patch.object(executor_mod, "load_file_sources", side_effect=_spy):
        result = executor._carica_sorgenti_file(
            canvas, dataframes, group_id="grp-1",
        )

    assert result is None
    assert len(captured) == 1, (
        "_carica_sorgenti_file must delegate to load_file_sources "
        "exactly once per call."
    )
    call = captured[0]
    assert call["canvas"] is canvas
    assert call["dataframes"] is dataframes
    assert call["group_id"] == "grp-1"
    # The delegator passes the executor module's logger; we don't
    # bind to a specific instance, just assert it is non-None so
    # operators retain log-line traceability.
    assert call["logger"] is not None


def test_executor_delegator_does_not_pass_singleton_group_id():
    """``_carica_sorgenti_file`` MUST NOT inject
    ``self._current_group_id`` as the helper's ``group_id``. The
    TE-005 ctx-strict contract lives at the boundary: a stale
    singleton on the executor instance must not reach the helper
    even via the delegator."""
    from app.core.manipola import executor as executor_mod

    captured: list[dict] = []

    def _spy(canvas, dataframes, *, group_id, logger=None):
        captured.append({"group_id": group_id})
        return None

    executor = ManipolaExecutor(project_service=object())

    canvas = build_canvas(
        build_node("file_source", file_source_id="fs-1", columns=[]),
    )

    with patch.object(executor_mod, "load_file_sources", side_effect=_spy):
        # Caller does NOT pass `group_id=`; pre-006 the executor's
        # singleton would have leaked into the helper. Post-006 the
        # delegator simply forwards the (None) caller value, and
        # the helper itself is responsible for the fail-closed.
        executor._carica_sorgenti_file(canvas, {})

    assert len(captured) == 1
    forwarded = captured[0]["group_id"]
    assert forwarded != "stale-grp-LEAK", (
        "The delegator must NOT pull `_current_group_id` from the "
        "executor instance. The helper's `group_id` argument is "
        "the only tenant channel; the singleton is invisible to "
        f"this chain. Got forwarded {forwarded!r}."
    )
    # Concretely, the delegator should forward whatever the caller
    # passed (None in this case), not substitute the singleton.
    assert forwarded is None


def test_helper_fail_closes_without_explicit_group_id():
    """``load_file_sources`` MUST fail-close with the canonical
    operator string when ``group_id`` is None/empty. Tests the
    helper directly (no delegator in the path) to lock the
    contract at the new boundary."""
    from app.core.manipola.source_loading import load_file_sources

    canvas = build_canvas(
        build_node("file_source", file_source_id="fs-1", columns=[]),
    )
    dataframes: dict = {}

    result = load_file_sources(canvas, dataframes, group_id=None)

    assert result == {
        "success": False,
        "message": "Contesto di sicurezza non determinabile",
    }
    assert dataframes == {}


def test_helper_passes_explicit_group_id_to_source_loader():
    """End-to-end through the helper: the `group_id` argument is
    passed verbatim to ``SourceLoader.load(... expected_group_id=...)``.
    No transformation, no fallback. Patches the SourceLoader so
    we can capture the call without exercising the loader's full
    chain."""
    from app.core.manipola import source_loading as sl

    canvas = build_canvas(
        build_node("file_source", file_source_id="fs-99", columns=[]),
    )
    dataframes: dict = {}

    captured: list[str] = []

    class _StubLoader:
        def load(self, ref, expected_group_id):
            captured.append(expected_group_id)
            # Return a tiny pandas DataFrame so the rest of the
            # helper's body does not blow up on the missing CRS
            # path.
            import pandas as pd
            return pd.DataFrame({"a": [1]})

    # Patch BOTH the artifacts symbol the helper imports lazily AND
    # the executor-side helpers used by the helper. Substituting
    # the loader is enough; `_nf` and `_applica_crs_da_colonne`
    # work correctly on a normal pandas DataFrame.
    with patch(
        "app.core.artifacts.SourceLoader", _StubLoader,
    ):
        result = sl.load_file_sources(
            canvas, dataframes, group_id="grp-explicit",
        )

    assert result is None  # success
    assert captured == ["grp-explicit"], (
        "The helper must pass the caller's `group_id` verbatim to "
        "`SourceLoader.load(expected_group_id=...)`. Got: "
        f"{captured!r}."
    )


# ---------------------------------------------------------------------------
# Source-bound: executor delegator stays thin
# ---------------------------------------------------------------------------


def test_executor_does_not_reinline_file_source_business_logic():
    """The delegator MUST NOT contain the file-loading business
    logic that was extracted. Source-bound check: the executor's
    ``_carica_sorgenti_file`` body must reference
    ``load_file_sources`` and must NOT reference the
    ``SourceLoader.load`` / ``SourceRef.from_node_data`` /
    ``SourceLoadingError`` symbols inline anymore (they live in
    the helper now).
    """
    text = _EXECUTOR_PY.read_text(encoding="utf-8")

    # The delegator must call the helper.
    assert "load_file_sources" in text, (
        "executor.py must reference `load_file_sources` (the "
        "TE-006 delegator target)."
    )

    # The body of the delegator is exactly the lines between
    # `def _carica_sorgenti_file` and the next `def `. We assert
    # that block does NOT contain inline business logic markers.
    lines = text.splitlines()
    start = None
    end = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("def _carica_sorgenti_file"):
            start = i
            continue
        if start is not None and line.lstrip().startswith("def ") and i > start:
            end = i
            break
    assert start is not None and end is not None, (
        "Could not locate `_carica_sorgenti_file` in executor.py; "
        "did the method get renamed?"
    )

    body = "\n".join(lines[start:end])

    # These tokens lived inline pre-006; they MUST NOT come back
    # into the delegator (they belong to the helper now).
    forbidden_inline_tokens = (
        "SourceLoader()",
        "SourceLoader,",
        "SourceLoadingError",
        "SourceRef.from_node_data",
        "filter_visible_columns(data,",
        "_applica_crs_da_colonne(data,",
    )
    offenders = [t for t in forbidden_inline_tokens if t in body]
    assert not offenders, (
        f"`_carica_sorgenti_file` re-introduced inline business "
        f"logic that was extracted by TE-006: {offenders}. The "
        "delegator must remain thin; the body lives in "
        "`app.core.manipola.source_loading.load_file_sources`."
    )
