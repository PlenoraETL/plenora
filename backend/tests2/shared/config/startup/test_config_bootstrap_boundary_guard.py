"""Guards for keeping Settings data-only and bootstrap explicit."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[5]
_CONFIG = _REPO_ROOT / "backend" / "app" / "config.py"
_STARTUP_VALIDATION = _REPO_ROOT / "backend" / "app" / "startup_validation.py"


def _class_method(tree: ast.AST, class_name: str, method_name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} not found")


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
        func = call.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def test_importing_config_does_not_import_bootstrap_or_startup_validation():
    saved = {
        name: sys.modules.get(name)
        for name in ("app.config", "app.bootstrap", "app.startup_validation")
    }
    for name in saved:
        sys.modules.pop(name, None)

    try:
        importlib.import_module("app.config")

        assert "app.bootstrap" not in sys.modules
        assert "app.startup_validation" not in sys.modules
    finally:
        for name in saved:
            sys.modules.pop(name, None)
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module


def test_settings_startup_methods_are_compatibility_delegates_only():
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"))

    expected_delegates = {
        "_ensure_redis": "ensure_redis",
        "_ensure_redis_wsl_fallback": "ensure_redis_wsl_fallback",
        "_wsl_redis_fallback_allowed": "wsl_redis_fallback_allowed",
        "validate_startup": "validate_startup",
    }
    forbidden_calls = {
        "chmod",
        "create_connection",
        "iterdir",
        "mkdir",
        "Popen",
        "rmtree",
        "touch",
        "unlink",
    }

    for method_name, delegate_name in expected_delegates.items():
        method = _class_method(tree, "Settings", method_name)
        calls = _called_names(method)

        assert delegate_name in calls
        assert calls.isdisjoint(forbidden_calls), (
            f"Settings.{method_name} must delegate startup behavior to "
            "app.startup_validation instead of owning operational side effects."
        )


def test_startup_validation_owns_redis_and_filesystem_bootstrap_work():
    text = _STARTUP_VALIDATION.read_text(encoding="utf-8")

    for marker in (
        "def validate_startup(",
        "def ensure_redis(",
        "def ensure_redis_wsl_fallback(",
        "socket.create_connection",
        "subprocess.Popen",
        "shutil.rmtree",
        "DATA_DIR",
    ):
        assert marker in text
