from __future__ import annotations

import pandas as pd
import pytest

from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.regression]


def test_non_join_transform_reserves_output_headroom(monkeypatch):
    from app.config import settings
    from app.core.manipola import transform_memory

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 1_050)
    monkeypatch.setitem(settings.DEFAULTS, "MANIPOLA_TRANSFORM_HEADROOM_FACTOR", 1.0)
    monkeypatch.setattr(transform_memory, "get_current_rss_bytes", lambda: 1_000)

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        transform_memory.guard_transform_headroom(
            [pd.DataFrame({"a": range(10), "b": range(10)})],
            node_id="formula-1",
        )

    assert exc_info.value.stage == transform_memory.TRANSFORM_MEMORY_STAGE
    assert exc_info.value.path_basename == "transform_formula-1"


def test_non_join_transform_guard_can_be_disabled(monkeypatch):
    from app.config import settings
    from app.core.manipola import transform_memory

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 0)
    transform_memory.guard_transform_headroom(
        [pd.DataFrame({"a": range(10)})],
        node_id="noop",
    )
