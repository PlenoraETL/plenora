"""Contract test — helper `save_columns_metadata_with_retry`.

Coperture (integrano i test estrai che verificano il flusso end-to-end):
  - retry solo su transient (OperationalError, TimeoutError)
  - non-transient → raise subito
  - return False del service → raise subito
  - campo `attempts` accurato nella eccezione
  - chained exception (__cause__) preservata
  - happy path no sleep
  - tutti i backoff usati (invariante: len(_BACKOFF_SEC) + 1 == _MAX_ATTEMPTS)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture
def fast_backoff(monkeypatch):
    """Accelera i backoff per test veloci."""
    import app.shared.columns_metadata as _cm
    monkeypatch.setattr(_cm, "_BACKOFF_SEC", (0.01, 0.01, 0.01))
    return _cm


@pytest.fixture
def mock_logger():
    return MagicMock()


def _call_helper(project_service, logger, project_id="p1", columns=None):
    from app.shared.columns_metadata import save_columns_metadata_with_retry
    save_columns_metadata_with_retry(
        project_service=project_service,
        project_id=project_id,
        columns_metadata=columns or [{"name": "x", "type": "str"}],
        logger=logger,
    )


# =============================================================================
# Happy path
# =============================================================================


def test_helper_happy_path_one_attempt(fast_backoff, mock_logger):
    ps = MagicMock()
    ps.update_project_columns_metadata.return_value = True
    _call_helper(ps, mock_logger)
    ps.update_project_columns_metadata.assert_called_once()


def test_helper_happy_path_no_sleep(fast_backoff, mock_logger, monkeypatch):
    sleeps = []
    monkeypatch.setattr(fast_backoff._time, "sleep", lambda s: sleeps.append(s))
    ps = MagicMock()
    ps.update_project_columns_metadata.return_value = True
    _call_helper(ps, mock_logger)
    assert sleeps == []


# =============================================================================
# Retry transient
# =============================================================================


def test_helper_retries_on_operational_error_and_succeeds(fast_backoff, mock_logger):
    import psycopg2
    ps = MagicMock()
    ps.update_project_columns_metadata.side_effect = [
        psycopg2.OperationalError("blip"), True,
    ]
    _call_helper(ps, mock_logger)
    assert ps.update_project_columns_metadata.call_count == 2


def test_helper_retries_on_timeout_error(fast_backoff, mock_logger):
    ps = MagicMock()
    ps.update_project_columns_metadata.side_effect = [
        TimeoutError("wait"), TimeoutError("wait"), True,
    ]
    _call_helper(ps, mock_logger)
    assert ps.update_project_columns_metadata.call_count == 3


def test_helper_raises_after_all_retries(fast_backoff, mock_logger):
    import psycopg2
    from app.shared.exceptions import ColumnsMetadataSaveError
    err = psycopg2.OperationalError("persistent")
    ps = MagicMock()
    ps.update_project_columns_metadata.side_effect = [err, err, err, err]
    with pytest.raises(ColumnsMetadataSaveError) as ei:
        _call_helper(ps, mock_logger)
    assert ps.update_project_columns_metadata.call_count == 4
    assert ei.value.attempts == 4
    assert ei.value.__cause__ is err
    assert ei.value.result_already_persisted is True


# =============================================================================
# Non-transient
# =============================================================================


@pytest.mark.parametrize("exc", [
    ValueError("canvas corrotto"),
    TypeError("unexpected kwarg"),
    AttributeError("missing method"),
    KeyError("no such col"),
])
def test_helper_non_transient_raises_immediately(fast_backoff, mock_logger, exc):
    from app.shared.exceptions import ColumnsMetadataSaveError
    ps = MagicMock()
    ps.update_project_columns_metadata.side_effect = exc
    with pytest.raises(ColumnsMetadataSaveError) as ei:
        _call_helper(ps, mock_logger)
    assert ps.update_project_columns_metadata.call_count == 1
    assert ei.value.attempts == 1
    assert type(exc).__name__ in ei.value.reason


# =============================================================================
# Service ritorna False (progetto inesistente)
# =============================================================================


def test_helper_service_returns_false_raises_no_retry(fast_backoff, mock_logger):
    from app.shared.exceptions import ColumnsMetadataSaveError
    ps = MagicMock()
    ps.update_project_columns_metadata.return_value = False
    with pytest.raises(ColumnsMetadataSaveError) as ei:
        _call_helper(ps, mock_logger, project_id="p-ghost")
    assert ps.update_project_columns_metadata.call_count == 1
    assert "non trovato" in ei.value.reason.lower()
    assert ei.value.project_id == "p-ghost"


# =============================================================================
# Invariante backoff
# =============================================================================


def test_all_backoff_values_used_on_full_failure(fast_backoff, mock_logger, monkeypatch):
    """Regression: pre-fix c'era un off-by-one che faceva scartare
    l'ultimo valore del backoff array."""
    # Ripristina i backoff reali per il test
    import app.shared.columns_metadata as _cm
    monkeypatch.setattr(_cm, "_BACKOFF_SEC", (0.2, 0.5, 1.0))
    monkeypatch.setattr(_cm, "_MAX_ATTEMPTS", 4)

    sleeps = []
    monkeypatch.setattr(_cm._time, "sleep", lambda s: sleeps.append(s))

    import psycopg2
    from app.shared.exceptions import ColumnsMetadataSaveError
    err = psycopg2.OperationalError("x")
    ps = MagicMock()
    ps.update_project_columns_metadata.side_effect = [err] * 4

    with pytest.raises(ColumnsMetadataSaveError):
        _call_helper(ps, mock_logger)

    assert sleeps == [0.2, 0.5, 1.0]
