from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.auth.db import (
    GroupRow,
    GroupSummaryRow,
    UserRow,
    hydrate_group_row,
    hydrate_group_summary_row,
    hydrate_user_row,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.security]

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_AUTH_DB_PATH = _BACKEND_ROOT / "app" / "core" / "auth" / "db.py"
_PERMISSIVE_HELPERS = frozenset({"_as_str", "_as_bool", "_as_int"})
_PERMISSIVE_BUILTINS = frozenset({"str", "bool", "int"})
_HYDRATION_FUNCTIONS = frozenset(
    {"hydrate_user_row", "hydrate_group_row", "hydrate_group_summary_row"}
)


def _auth_db_tree() -> ast.Module:
    return ast.parse(_AUTH_DB_PATH.read_text(encoding="utf-8"), filename=str(_AUTH_DB_PATH))


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _top_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def test_hydrate_user_row_accepts_valid_strict_db_row():
    row = hydrate_user_row(
        {
            "id": "u1",
            "username": "alice",
            "password_hash": "hash",
            "email": None,
            "group_id": "g1",
            "role": "admin",
            "is_active": True,
            "created_at": "2026-05-08T10:00:00+00:00",
            "updated_at": "2026-05-08T10:00:00+00:00",
        }
    )

    assert row == UserRow(
        id="u1",
        username="alice",
        password_hash="hash",
        email=None,
        group_id="g1",
        role="admin",
        is_active=True,
        created_at="2026-05-08T10:00:00+00:00",
        updated_at="2026-05-08T10:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", None),
        ("username", None),
        ("password_hash", None),
        ("group_id", None),
        ("role", None),
        ("is_active", "true"),
        ("created_at", 123),
    ],
)
def test_hydrate_user_row_rejects_malformed_required_columns(field, value):
    raw = {
        "id": "u1",
        "username": "alice",
        "password_hash": "hash",
        "email": "alice@example.test",
        "group_id": "g1",
        "role": "admin",
        "is_active": True,
        "created_at": "2026-05-08T10:00:00+00:00",
        "updated_at": "2026-05-08T10:00:00+00:00",
    }
    raw[field] = value

    with pytest.raises(ValidationError, match=field):
        hydrate_user_row(raw)


def test_hydrate_user_row_preserves_missing_optional_defaults():
    row = hydrate_user_row({"id": "u1", "username": "alice"})

    assert row == UserRow(id="u1", username="alice")


def test_hydrate_user_row_rejects_empty_mapping():
    with pytest.raises(ValidationError, match="id"):
        hydrate_user_row({})


def test_hydrate_group_row_accepts_valid_strict_db_row():
    row = hydrate_group_row(
        {
            "id": "g1",
            "name": "Group 1",
            "description": None,
            "created_at": "2026-05-08T10:00:00+00:00",
            "updated_at": "2026-05-08T10:00:00+00:00",
        }
    )

    assert row == GroupRow(
        id="g1",
        name="Group 1",
        description=None,
        created_at="2026-05-08T10:00:00+00:00",
        updated_at="2026-05-08T10:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", None),
        ("name", None),
        ("created_at", 123),
        ("updated_at", 123),
    ],
)
def test_hydrate_group_row_rejects_malformed_required_columns(field, value):
    raw = {
        "id": "g1",
        "name": "Group 1",
        "description": "desc",
        "created_at": "2026-05-08T10:00:00+00:00",
        "updated_at": "2026-05-08T10:00:00+00:00",
    }
    raw[field] = value

    with pytest.raises(ValidationError, match=field):
        hydrate_group_row(raw)


def test_hydrate_group_row_rejects_empty_mapping():
    with pytest.raises(ValidationError, match="id"):
        hydrate_group_row({})


def test_hydrate_group_summary_row_accepts_valid_strict_db_row():
    row = hydrate_group_summary_row(
        {
            "projects": 1,
            "users": 2,
            "connections": 3,
            "file_sources": 4,
            "datasources": 5,
            "api_connections": 6,
            "schedules": 7,
        }
    )

    assert row == GroupSummaryRow(
        projects=1,
        users=2,
        connections=3,
        file_sources=4,
        datasources=5,
        api_connections=6,
        schedules=7,
    )


@pytest.mark.parametrize("value", [None, "1", 1.0, True])
def test_hydrate_group_summary_row_rejects_malformed_counts(value):
    with pytest.raises(ValidationError, match="projects"):
        hydrate_group_summary_row({"projects": value})


def test_hydrate_group_summary_row_preserves_missing_count_defaults():
    assert hydrate_group_summary_row({"projects": 1}) == GroupSummaryRow(projects=1)


def test_auth_db_hydration_has_no_permissive_scalar_helpers():
    tree = _auth_db_tree()

    defined_helpers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in _PERMISSIVE_HELPERS
    }
    helper_calls: list[str] = []
    builtin_calls: list[str] = []
    hydrate_functions = _top_level_functions(tree)

    for name in _HYDRATION_FUNCTIONS:
        function = hydrate_functions[name]
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node.func)
            if call_name in _PERMISSIVE_HELPERS:
                helper_calls.append(f"{name}:{node.lineno}:{call_name}")
            if call_name in _PERMISSIVE_BUILTINS:
                builtin_calls.append(f"{name}:{node.lineno}:{call_name}")

    assert not defined_helpers
    assert not helper_calls
    assert not builtin_calls


def test_auth_db_hydration_functions_validate_with_strict_pydantic_adapters():
    tree = _auth_db_tree()
    hydrate_functions = _top_level_functions(tree)
    expected_adapter_by_function = {
        "hydrate_user_row": "_USER_ROW_ADAPTER",
        "hydrate_group_row": "_GROUP_ROW_ADAPTER",
        "hydrate_group_summary_row": "_GROUP_SUMMARY_ROW_ADAPTER",
    }

    for function_name, adapter_name in expected_adapter_by_function.items():
        validate_calls = [
            node
            for node in ast.walk(hydrate_functions[function_name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "validate_python"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == adapter_name
        ]
        assert validate_calls, f"{function_name} must hydrate through {adapter_name}"

    source = _AUTH_DB_PATH.read_text(encoding="utf-8")
    for strict_type in ("StrictStr", "StrictBool", "StrictInt", "TypeAdapter"):
        assert strict_type in source
