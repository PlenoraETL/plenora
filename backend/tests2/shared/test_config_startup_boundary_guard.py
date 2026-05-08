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


def _class_method(tree: ast.AST, class_name: str, method_name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} not found")


def test_settings_validate_startup_is_only_a_compatibility_shim():
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"))
    method = _class_method(tree, "Settings", "validate_startup")
    names = {node.id for node in ast.walk(method) if isinstance(node, ast.Name)}

    assert "validate_startup" in names
    forbidden = {"shutil", "subprocess", "socket", "stat", "os"}
    assert not (names & forbidden), (
        "Settings.validate_startup must not own operational behavior; "
        "filesystem cleanup and Redis probing belong in app.startup_validation."
    )


def test_startup_validation_owns_operational_boot_checks():
    text = _STARTUP_VALIDATION.read_text(encoding="utf-8")
    for marker in (
        "def validate_startup(",
        "def ensure_redis(",
        "def ensure_redis_wsl_fallback(",
        "shutil.rmtree",
        "socket.create_connection",
        "subprocess.Popen",
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
