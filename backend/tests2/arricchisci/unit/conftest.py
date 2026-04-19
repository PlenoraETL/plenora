from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.arricchisci.api_connector import ApiConnectionService


@pytest.fixture
def api_service():
    with patch("app.core.arricchisci.api_connector.settings") as mock_settings:
        mock_settings.DATA_DIR = Path("/tmp")
        mock_settings.RESULTS_DIR = Path("/tmp")
        mock_settings.DEFAULT_GROUP = "default"
        svc = ApiConnectionService.__new__(ApiConnectionService)
        svc.data_dir = Path("/tmp")
        svc.results_dir = Path("/tmp")
        return svc
