"""
Contract test — Bug preview/execute consumer post-#6 cluster (2026-04-23).

Blinda: quando `connection_service.get_connection` raise
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.estrai.executor import EstraiExecutor
from app.shared.crypto import CredentialUnavailableError


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def mock_metadata():
    m = MagicMock()
    m.default_connection_id = "conn-1"
    m.project_type = "estrai"
    return m


@pytest.fixture
def mock_canvas():
    canvas = MagicMock()
    # Nodo "result" richiesto sia da preview() che da execute()
    result_node = MagicMock()
    result_node.type = "result"
    result_node.id = "result-1"
    canvas.nodes = [result_node]
    canvas.edges = []
    return canvas


@pytest.fixture
def executor_with_failing_connection(mock_metadata, mock_canvas):
    """EstraiExecutor con get_connection che raise CredentialUnavailableError."""
    project_service = MagicMock()
    project_service.get_project_metadata.return_value = mock_metadata
    project_service.get_project_canvas.return_value = mock_canvas

    connection_service = MagicMock()
    connection_service.get_connection.side_effect = CredentialUnavailableError(
        "connection 'conn-1': password non decriptabile "
        "(chiave mancante/sbagliata o token corrotto)"
    )

    return EstraiExecutor(
        project_service=project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=connection_service,
        features_service=None,
    )


def test_preview_returns_user_error_when_credential_unavailable(
    executor_with_failing_connection, mock_canvas
):
    """Bug post-#6: preview DEVE tradurre CredentialUnavailableError
    in result-dict user_error, non bubblare al worker."""
    result = executor_with_failing_connection.preview(
        project_id="proj-1",
        canvas=mock_canvas,
        limit=10,
    )

    assert result["success"] is False
    assert result.get("user_error") is True
    assert "credenziale" in result["message"].lower()
    assert "decriptabile" in result["message"].lower()


def test_execute_returns_user_error_when_credential_unavailable(
    executor_with_failing_connection, mock_canvas
):
    """Bug post-#6 simmetrico: execute() stesso gap, stesso fix."""
    result = executor_with_failing_connection.execute(
        project_id="proj-1",
        canvas=mock_canvas,
    )

    assert result["success"] is False
    assert result.get("user_error") is True
    assert "credenziale" in result["message"].lower()
    assert "decriptabile" in result["message"].lower()


def test_preview_propagates_non_credential_runtime_errors(mock_metadata, mock_canvas):
    """Il catch CredentialUnavailableError NON e' swallow-all: eccezioni
    non-credential (es. RuntimeError interno) continuano a bubblare al
    worker come prima, cosi' il [CRASH] handler Celery funziona per
    gli errori davvero imprevisti."""
    project_service = MagicMock()
    project_service.get_project_metadata.return_value = mock_metadata
    project_service.get_project_canvas.return_value = mock_canvas

    connection_service = MagicMock()
    connection_service.get_connection.side_effect = RuntimeError("DB connection pool exhausted")

    executor = EstraiExecutor(
        project_service=project_service,
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=connection_service,
        features_service=None,
    )

    with pytest.raises(RuntimeError, match="pool exhausted"):
        executor.preview(project_id="proj-1", canvas=mock_canvas, limit=10)
