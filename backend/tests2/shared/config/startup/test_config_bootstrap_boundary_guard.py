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
_CONFIG_ENV_LOADER = _REPO_ROOT / "backend" / "app" / "config_env_loader.py"
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
        for name in (
            "app.config",
            "app.bootstrap",
            "app.config_env_loader",
            "app.startup_validation",
        )
    }
    for name in saved:
        sys.modules.pop(name, None)

    try:
        importlib.import_module("app.config")

        assert "app.bootstrap" not in sys.modules
        assert "app.config_env_loader" not in sys.modules
        assert "app.startup_validation" not in sys.modules
    finally:
        for name in saved:
            sys.modules.pop(name, None)
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module
        app_pkg = sys.modules.get("app")
        if app_pkg is not None:
            for name, module in saved.items():
                attr = name.rsplit(".", 1)[-1]
                if module is not None:
                    setattr(app_pkg, attr, module)
                elif hasattr(app_pkg, attr):
                    delattr(app_pkg, attr)


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


def test_config_does_not_own_dotenv_loading_or_env_bootstrap():
    text = _CONFIG.read_text(encoding="utf-8")
    tree = ast.parse(text)

    forbidden_markers = {
        "from dotenv import load_dotenv",
        "import load_dotenv",
        "load_dotenv(",
        "load_backend_dotenv(",
        "def _should_load_dotenv(",
        "def should_load_dotenv(",
        "def load_config_env(",
    }
    for marker in forbidden_markers:
        assert marker not in text, (
            "app.config must stay passive; dotenv loading and raw "
            f"ENVIRONMENT/ENV bootstrap checks belong in app.config_env_loader, found {marker!r}."
        )

    imports_env_loader = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports_env_loader = imports_env_loader or any(
                alias.name == "app.config_env_loader" for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            imports_env_loader = imports_env_loader or (
                node.module == "app.config_env_loader"
            )

    assert not imports_env_loader, (
        "Importing app.config must not import app.config_env_loader. "
        "Bootstrap must call the env loader explicitly before importing Settings."
    )


def test_config_env_loader_owns_dotenv_loading_behavior():
    assert _CONFIG_ENV_LOADER.exists(), (
        "Env loading must live in app.config_env_loader so Settings remains passive."
    )

    text = _CONFIG_ENV_LOADER.read_text(encoding="utf-8")
    for marker in (
        "def should_load_dotenv(",
        "def load_backend_dotenv(",
        "load_dotenv(",
        "from app.environment import environment_name",
        "environment_name()",
    ):
        assert marker in text, (
            "app.config_env_loader must own dotenv loading and raw environment "
            f"bootstrap behavior, missing {marker!r}."
        )


def test_settings_has_no_magic_getattr_fallback():
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"))
    method_names = _class_method_names(tree, "Settings")

    assert "__getattr__" not in method_names


def test_unknown_settings_attribute_fails_loud():
    from app.config import Settings

    with pytest.raises(AttributeError):
        Settings().PLENORA_UNKNOWN_CONFIG_ATTRIBUTE


def test_startup_validation_owns_redis_and_filesystem_bootstrap_work():
    text = _STARTUP_VALIDATION.read_text(encoding="utf-8")

    for marker in (
        "def validate_startup(",
        "def ensure_broker(",
        "socket.create_connection",
        "shutil.rmtree",
        "DATA_DIR",
    ):
        assert marker in text
