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


def _class_method_names(tree: ast.AST, class_name: str) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name for item in node.body if isinstance(item, ast.FunctionDef)
            }
    raise AssertionError(f"{class_name} not found")


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


def test_settings_startup_methods_are_not_present():
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"))
    method_names = _class_method_names(tree, "Settings")

    assert method_names.isdisjoint(
        {
            "_ensure_redis",
            "_ensure_redis_wsl_fallback",
            "_wsl_redis_fallback_allowed",
            "validate_startup",
        }
    ), (
        "Settings must remain data/config only; startup validation and "
        "Redis probing belong in app.startup_validation."
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
