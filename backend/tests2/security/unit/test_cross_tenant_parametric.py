from unittest.mock import patch

import pytest

from app.core.arricchisci.api_connector import ApiConnectionService
from app.core.manipola.executor import ManipolaExecutor
from tests2.helpers.builders import build_canvas, build_node


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.regression]


def _manipola_file_source_denied():
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = "grp-1"
    canvas = build_canvas(build_node("file_source", file_source_id="fs-99", columns=[]))
    dataframes = {}

    with patch("app.shared.sharing.is_resource_visible", return_value=False):
        result = executor._carica_sorgenti_file(canvas, dataframes)

    assert result == {
        "success": False,
        "message": "Accesso negato: il file sorgente 'fs-99' non appartiene al tuo gruppo",
    }


def _manipola_project_source_denied():
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = "grp-1"
    executor.results_base_dir = None
    canvas = build_canvas(build_node("project_source", source_project_id="proj-99", columns=[]))
    dataframes = {}

    with patch("app.db.db_service.get_project", return_value={"id": "proj-99", "group_id": "grp-2"}):
        result = executor._carica_sorgenti_progetto(canvas, dataframes, force_recalculate=False)

    assert result == {
        "success": False,
        "message": "Accesso negato: il progetto sorgente 'proj-99' appartiene a un gruppo diverso.",
    }


def _arricchisci_file_source_denied():
    service = ApiConnectionService.__new__(ApiConnectionService)
    service._current_group_id = "grp-1"

    with patch("app.shared.sharing.is_resource_visible", return_value=False):
        with pytest.raises(ValueError, match="Accesso negato"):
            service._assert_file_source_group_access("fs-99")


def _arricchisci_project_source_denied():
    service = ApiConnectionService.__new__(ApiConnectionService)
    service._current_group_id = "grp-1"
    service.results_dir = None
    node_data = {"source_project_id": "proj-99"}

    with patch("app.db.db_service.get_project", return_value={"id": "proj-99", "group_id": "grp-2"}):
        with pytest.raises(ValueError, match="Accesso negato: progetto sorgente 'proj-99'"):
            service._load_source_project(node_data)


@pytest.mark.parametrize(
    "scenario",
    [
        _manipola_file_source_denied,
        _manipola_project_source_denied,
        _arricchisci_file_source_denied,
        _arricchisci_project_source_denied,
    ],
    ids=[
        "manipola-file-source",
        "manipola-project-source",
        "arricchisci-file-source",
        "arricchisci-project-source",
    ],
)
def test_cross_tenant_access_is_denied_across_source_loaders(scenario):
    scenario()
