"""Contract test — propagation `ColumnsMetadataSaveError` dal task Celery
`workers/tasks/enrichment_task.py`.

Critico: se il save fallisce in modo permanente, il task DEVE terminare
FAILED. Pre-refactor swallowavamo con warning → task SUCCESS + metadata
stale → downstream (manipola, carica) riceveva tipi `str` fallback.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_enrichment_task_uses_helper_no_silent_warning():
    """Regression guard: il task usa il helper e non ha piu' il
    try/except warning silenzioso sul save metadata."""
    from pathlib import Path
    src = Path(
        __file__
    ).parents[3] / "workers" / "tasks" / "enrichment_task.py"
    content = src.read_text(encoding="utf-8")
    assert "save_columns_metadata_with_retry" in content, (
        "enrichment_task.py non chiama il helper condiviso"
    )
    assert "Impossibile salvare columns_metadata" not in content, (
        "enrichment_task.py contiene ancora il warning silenzioso"
    )


def test_enrichment_task_save_error_propagates_to_celery(monkeypatch):
    """Smoke: errore non-transient → ColumnsMetadataSaveError sollevato
    dal helper → Celery marca FAILED."""
    from unittest.mock import MagicMock
    import app.shared.columns_metadata as _cm
    from app.shared.exceptions import ColumnsMetadataSaveError

    monkeypatch.setattr(_cm, "_BACKOFF_SEC", (0.01, 0.01, 0.01))

    ps = MagicMock()
    ps.update_project_columns_metadata.side_effect = TypeError(
        "schema mismatch"
    )
    logger = MagicMock()

    with pytest.raises(ColumnsMetadataSaveError) as ei:
        _cm.save_columns_metadata_with_retry(
            project_service=ps,
            project_id="p-enrichment",
            columns_metadata=[{"name": "a", "type": "int"}],
            logger=logger,
            op="enrichment_task[test]:save_columns_metadata",
        )
    assert ei.value.project_id == "p-enrichment"
    assert ei.value.attempts == 1  # no retry su TypeError
