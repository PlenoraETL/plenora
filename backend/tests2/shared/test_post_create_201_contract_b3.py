"""
Contract test F3-B3 (2026-04-24) — POST create → 201 (chiusura cluster F3).

Chiusura di F3: dopo F3-B1 (connections/api-connections/schedules) e
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# BE-GO-READY-TEST-INFRA-001: helper extracted to a shared module.
# Body + assertion messages preserved byte-exact.
from tests2.shared._route_status_helpers import _assert_post_route_status_201


def test_post_files_upload_declares_201_on_route():
    """F3-B3: POST /api/files/upload dichiara status_code=201."""
    from app.core.files.router import router as files_router

    _assert_post_route_status_201(
        files_router, "/files/upload", "POST /api/files/upload"
    )


def test_post_files_upload_multi_layer_declares_201_on_route():
    """F3-B3: POST /api/files/upload-multi-layer dichiara status_code=201."""
    from app.core.files.router import router as files_router

    _assert_post_route_status_201(
        files_router,
        "/files/upload-multi-layer",
        "POST /api/files/upload-multi-layer",
    )
