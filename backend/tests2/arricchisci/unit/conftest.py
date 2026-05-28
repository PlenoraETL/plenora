"""
Provides pytest fixtures for unit tests.

Centralizes local pytest setup for the surrounding test package.
"""

import pytest

from app.core.arricchisci.api_connector import ApiConnectionService


@pytest.fixture
def api_service():
    return ApiConnectionService.__new__(ApiConnectionService)
