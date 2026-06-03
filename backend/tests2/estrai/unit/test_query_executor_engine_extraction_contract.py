"""
Contract tests for the QueryExecutor engine factory extraction.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests2.shared._ast_identifiers import (
    all_identifiers,
    imports_symbol_from,
    module_classdefs,
)


pytestmark = [pytest.mark.unit, pytest.mark.estrai, pytest.mark.contract]


def test_query_executor_reexports_default_engine_factory_for_compatibility():
    from app.core.query_execution.query_engines import (
        DefaultEngineFactory as ExtractedFactory,
    )
    from app.core.query_execution.query_executor import (
        DefaultEngineFactory as PublicFactory,
    )

    assert PublicFactory is ExtractedFactory


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def test_query_executor_keeps_engine_factory_out_of_orchestrator_source():
    source = (
        _BACKEND_ROOT / "app" / "core" / "query_execution" / "query_executor.py"
    ).read_text(encoding="utf-8")

    # AST-bound: re-export via import reale + la classe NON deve essere DEFINITA
    # qui (solo importata) + niente identificatori dell'impl engine inline.
    assert imports_symbol_from(
        source, "app.core.query_execution.query_engines", "DefaultEngineFactory"
    )
    assert "DefaultEngineFactory" not in module_classdefs(source)
    identifiers = all_identifiers(source)
    assert "quote_plus" not in identifiers
    assert "fast_executemany" not in identifiers


def test_query_executor_default_engine_factory_is_injected_at_construction():
    from app.core.query_execution.query_executor import QueryExecutor

    factory = MagicMock(name="default_engine_factory")
    with patch(
        "app.core.query_execution.query_executor.DefaultEngineFactory",
        return_value=factory,
    ) as factory_cls:
        executor = QueryExecutor(logger=MagicMock())

    factory_cls.assert_called_once_with()
    assert executor._engine_factory is factory
