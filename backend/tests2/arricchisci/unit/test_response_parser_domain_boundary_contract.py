"""BE097 C2 contract for Arricchisci-owned response parsing."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_APP_ROOT = _BACKEND_ROOT / "app"


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
    return imports


def test_arricchisci_uses_domain_response_parser_directly() -> None:
    source = _APP_ROOT / "core" / "arricchisci" / "api_connection_generator_enricher.py"
    imports = _imports_for(source)

    assert "app.core.arricchisci.response_parser" in imports
    assert "app.utils.response_parser" not in imports


def test_response_parser_keeps_nested_api_mapping_behavior() -> None:
    from app.core.arricchisci.response_parser import ResponseParser

    rows = ResponseParser(
        {
            "root_path": "data",
            "iterate_on": [
                {"path": "", "as": "station"},
                {"path": "prod", "as": "product"},
                {"path": "val", "as": "measurement"},
            ],
            "mappings": [
                {"column": "station", "path": "station.name"},
                {"column": "variable", "path": "product.var"},
                {"column": "value", "path": "measurement.val"},
            ],
            "transforms": [
                {
                    "column": "celsius",
                    "source": "value",
                    "operation": "kelvin_to_celsius",
                    "condition": "variable == 'B12101'",
                }
            ],
        }
    ).parse(
        {
            "data": [
                {
                    "name": "A",
                    "prod": [
                        {
                            "var": "B12101",
                            "val": [{"val": 293.15}],
                        }
                    ],
                }
            ]
        }
    )

    assert rows == [
        {
            "station": "A",
            "variable": "B12101",
            "value": 293.15,
            "celsius": 20.0,
        }
    ]


def test_utils_response_parser_compat_alias_is_removed() -> None:
    assert not (_APP_ROOT / "utils" / "response_parser.py").exists()
