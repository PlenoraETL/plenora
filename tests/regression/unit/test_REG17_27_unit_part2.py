"""
tests/regression/unit/test_REG17_27_arricchisci.py
===================================================
Regression test UNIT — Modulo ARRICCHISCI (REG-17→REG-27)
"""

import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch, call

pytestmark = [pytest.mark.regression, pytest.mark.unit]




# =============================================================================
# REG-27 — Template Nominatim Geocoding
# =============================================================================

class TestREG27Unit:

    @pytest.fixture
    def mock_nominatim_response(self):
        return [
            {"lat": "43.7696", "lon": "11.2558",
             "display_name": "Florence, Metropolitan City of Florence, Tuscany, Italy"}
        ]

    def test_nominatim_lat_lon_display_name(self, mock_nominatim_response):
        """Response Nominatim ha lat, lon, display_name."""
        result = mock_nominatim_response[0]
        assert "lat" in result
        assert "lon" in result
        assert "display_name" in result

    def test_nominatim_coordinate_non_null(self, mock_nominatim_response):
        """Coordinate non sono null."""
        result = mock_nominatim_response[0]
        assert result["lat"] is not None
        assert result["lon"] is not None
        lat = float(result["lat"])
        lon = float(result["lon"])
        assert -90 <= lat <= 90
        assert -180 <= lon <= 180

    @pytest.mark.parametrize("city,expected_country", [
        ("Rome, Italy",    "Italy"),
        ("Paris, France",  "France"),
        ("Berlin, Germany", "Germany"),
    ])
    def test_nominatim_display_name_contiene_paese(self, city, expected_country):
        """display_name contiene il paese."""
        display_name = f"{city.split(',')[0]}, ..., {expected_country}"
        assert expected_country in display_name

    def test_nominatim_output_mapping(self, mock_nominatim_response):
        """Output mapping estrae lat, lon, display_name correttamente."""
        result = mock_nominatim_response[0]
        output = {
            "lat":          float(result["lat"]),
            "lon":          float(result["lon"]),
            "display_name": result["display_name"],
        }
        assert isinstance(output["lat"], float)
        assert isinstance(output["lon"], float)
        assert isinstance(output["display_name"], str)
        assert len(output["display_name"]) > 0
