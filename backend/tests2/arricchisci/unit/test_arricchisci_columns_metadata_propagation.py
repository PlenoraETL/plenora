"""Contract test — propagation `ColumnsMetadataSaveError` da ARRICCHISCI."""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_arricchisci_uses_helper_and_no_silent_warning():
    """Regression guard: il call-site arricchisci usa il helper e NON
    contiene piu' il try/except logger.warning silenzioso."""
    from pathlib import Path
    src = Path(
        __file__
    ).parents[3] / "app" / "core" / "arricchisci" / "executor.py"
    content = src.read_text(encoding="utf-8")
    assert "save_columns_metadata_with_retry" in content, (
        "arricchisci/executor.py non chiama il helper condiviso"
    )
    assert "Impossibile salvare columns_metadata" not in content, (
        "arricchisci/executor.py contiene ancora il warning silenzioso"
    )


def test_arricchisci_save_error_propagates(monkeypatch):
    """Se il save fallisce permanentemente, il helper solleva
    ColumnsMetadataSaveError e il caller NON deve swalloware."""
    from unittest.mock import MagicMock
    import app.shared.columns_metadata as _cm
    from app.shared.exceptions import ColumnsMetadataSaveError

    monkeypatch.setattr(_cm, "_BACKOFF_SEC", (0.01, 0.01, 0.01))

    # Force non-transient raise dal service
    ps = MagicMock()
    ps.update_project_columns_metadata.side_effect = ValueError(
        "canvas corrotto test"
    )
    logger = MagicMock()

    with pytest.raises(ColumnsMetadataSaveError):
        _cm.save_columns_metadata_with_retry(
            project_service=ps,
            project_id="p-arricchisci",
            columns_metadata=[{"name": "a", "type": "str"}],
            logger=logger,
        )
