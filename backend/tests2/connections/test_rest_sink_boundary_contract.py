"""Contracts for the neutral REST sink provider boundary."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_rest_sink_lives_under_connections_and_keeps_features_alias() -> None:
    from app.core.connections.rest_sink import RestFeatureSink, RestSinkConfig
    from app.core.connections.rest_sink import RestFeatureSink as LegacyRestFeatureSink
    from app.core.connections.rest_sink import RestSinkConfig as LegacyRestSinkConfig

    assert LegacyRestFeatureSink is RestFeatureSink
    assert LegacyRestSinkConfig is RestSinkConfig


def test_rest_sink_provider_boundary_has_no_owned_network_stack() -> None:
    import app.core.connections.rest_sink as rest_sink

    assert "requests" not in rest_sink.__dict__
    assert "httpx" not in rest_sink.__dict__
