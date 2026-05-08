from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


APP_SHARED = Path(__file__).resolve().parents[2] / "app" / "shared"


def _defined_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_oauth_token_implementation_lives_outside_api_utils():
    api_utils_path = APP_SHARED / "api_utils.py"
    oauth_path = APP_SHARED / "oauth.py"

    api_utils_functions = _defined_functions(api_utils_path)
    oauth_functions = _defined_functions(oauth_path)

    token_impl_functions = {
        "_oauth2_cache_key",
        "_fetch_oauth2_password_token",
        "_fetch_oauth2_client_credentials_token",
        "get_cached_oauth2_token",
        "invalidate_oauth2_token",
        "get_arcgis_token",
    }

    assert token_impl_functions.isdisjoint(api_utils_functions)
    assert token_impl_functions.issubset(oauth_functions)

    api_utils_src = api_utils_path.read_text(encoding="utf-8")
    assert "_OAUTH2_TOKEN_CACHE" not in api_utils_src
    assert "_ARCGIS_TOKEN_CACHE" not in api_utils_src
