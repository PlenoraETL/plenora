from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


APP_DIR = Path(__file__).resolve().parents[2] / "app"
APP_SHARED = APP_DIR / "shared"


def _defined_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_oauth_token_implementation_lives_outside_api_utils():
    api_utils_path = APP_SHARED / "api_utils.py"
    oauth_path = APP_SHARED / "connections" / "oauth.py"
    root_oauth_path = APP_SHARED / "oauth.py"

    oauth_functions = _defined_functions(oauth_path)

    token_impl_functions = {
        "_oauth2_cache_key",
        "_fetch_oauth2_password_token",
        "_fetch_oauth2_client_credentials_token",
        "get_cached_oauth2_token",
        "invalidate_oauth2_token",
        "get_arcgis_token",
    }

    assert not api_utils_path.exists()
    assert token_impl_functions.issubset(oauth_functions)
    assert not root_oauth_path.exists()


def test_http_and_ssrf_implementation_lives_outside_api_utils():
    api_utils_path = APP_SHARED / "api_utils.py"
    http_session_path = APP_DIR / "infrastructure" / "http" / "session.py"
    outbound_url_path = APP_DIR / "core" / "security" / "outbound_url.py"

    assert not api_utils_path.exists()

    assert "get_shared_session" in _defined_functions(http_session_path)
    assert "validate_external_url" in _defined_functions(outbound_url_path)
    assert "check_outbound_address" in _defined_functions(outbound_url_path)


def test_runtime_code_does_not_import_shared_api_utils():
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR).as_posix()
        src = path.read_text(encoding="utf-8")
        if "app.shared.api_utils" in src:
            offenders.append(rel)

    assert offenders == []
