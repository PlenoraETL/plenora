"""Guards for keeping Settings data-only and startup behavior explicit."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG = _REPO_ROOT / "backend" / "app" / "config.py"
_CONFIG_DISPLAY = _REPO_ROOT / "backend" / "app" / "config_display.py"
_STARTUP_VALIDATION = _REPO_ROOT / "backend" / "app" / "startup_validation.py"


def _class_method_names(tree: ast.AST, class_name: str) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name for item in node.body if isinstance(item, ast.FunctionDef)
            }
    raise AssertionError(f"{class_name} not found")


def test_settings_does_not_own_startup_methods():
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


def test_startup_validation_owns_operational_boot_checks():
    text = _STARTUP_VALIDATION.read_text(encoding="utf-8")
    for marker in (
        "def validate_startup(",
        "def ensure_broker(",
        "shutil.rmtree",
        "socket.create_connection",
    ):
        assert marker in text, (
            f"app.startup_validation must own startup behavior marker {marker!r}."
        )


def test_config_does_not_own_cli_display_prints():
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"))
    direct_print_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]

    assert direct_print_calls == [], (
        "app.config must not own CLI/display output; "
        "human-facing print calls belong in app.config_display."
    )


def test_config_display_owns_print_config_output():
    text = _CONFIG_DISPLAY.read_text(encoding="utf-8")

    assert "def print_config(" in text
    assert "print(" in text
