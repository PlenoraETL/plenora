"""
Contract test N5 — preview endpoint ha cap esplicito su limit.

Previous behavior: `limit: int = 100` senza Query validation.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.params import Query as FQuery


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def test_preview_endpoint_limit_has_upper_bound():
    """Il Query di `limit` deve avere le=10000 per prevenire OOM.

    FastAPI moderno (pydantic v2) espone i vincoli numerici via
    `metadata` list di annotated_types (Ge, Le).
    """
    from app.core.orchestrator.router import preview_project

    sig = inspect.signature(preview_project)
    limit_param = sig.parameters.get("limit")
    assert limit_param is not None, "parametro limit mancante"

    q = limit_param.default
    assert isinstance(q, FQuery), (
        "limit deve essere un fastapi.Query con cap, non un int nudo"
    )
    # Estrai vincoli da q.metadata
    ge_vals = [m.ge for m in q.metadata if hasattr(m, "ge")]
    le_vals = [m.le for m in q.metadata if hasattr(m, "le")]
    assert 1 in ge_vals, f"atteso ge=1 in metadata, trovato {q.metadata}"
    assert 10000 in le_vals, f"atteso le=10000 in metadata, trovato {q.metadata}"


def test_preview_endpoint_limit_default_is_100():
    """Regression: il default resta 100 (backcompat)."""
    from app.core.orchestrator.router import preview_project

    sig = inspect.signature(preview_project)
    q = sig.parameters["limit"].default
    assert q.default == 100
