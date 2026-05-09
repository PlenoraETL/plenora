"""DB6 audit guard for PostgreSQL pool/search_path/typecaster hardening.

This is intentionally source-bound: the worker scope is audit/guard, not
live-DB mutation. It documents accepted unstable internals and the next
hardening surface around custom typecasters.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]

REPO_ROOT = Path(__file__).resolve().parents[3]
PG_CONNECTION = REPO_ROOT / "backend" / "app" / "db" / "pg_connection.py"
INSTALL_NON_DOCKER = REPO_ROOT / "docs" / "ops" / "install-non-docker.md"
APP_ROOT = REPO_ROOT / "backend" / "app"


def _pg_source() -> str:
    return PG_CONNECTION.read_text(encoding="utf-8")


def test_pool_headroom_private_psycopg2_attrs_are_isolated_and_marked():
    """Private psycopg2 pool attrs are acceptable only in one wrapper.

    psycopg2 exposes no public checked-out/free counters. The runtime
    contract is: keep `_used`/`_pool` inside `get_pool_headroom`, keep it
    best-effort, and let health/readiness fail open if introspection breaks.
    """
    src = _pg_source()
    headroom_section = src[src.index("def get_pool_headroom"):]

    assert "def get_pool_headroom" in src
    assert "Best-effort" in headroom_section
    assert 'getattr(pool, "_used", {})' in headroom_section
    assert 'getattr(pool, "_pool", [])' in headroom_section

    assert src.count('getattr(pool, "_used", {})') == 1
    assert src.count('getattr(pool, "_pool", [])') == 1


def test_pool_sets_schema_first_search_path_and_docs_public_schema_hardening():
    """Pool search_path is schema-first and install docs revoke public CREATE."""
    src = _pg_source()
    docs = INSTALL_NON_DOCKER.read_text(encoding="utf-8")

    assert 'opts = [f"-c search_path={_DB_SCHEMA},public"]' in src
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC;" in docs
    assert "CREATE SCHEMA IF NOT EXISTS plenora AUTHORIZATION plenora;" in docs
    assert "GRANT USAGE, CREATE ON SCHEMA plenora TO plenora;" in docs
    assert "do not grant `CREATE` on `public` to `plenora`" in docs


def test_custom_oid_typecasters_are_not_registered_globally():
    """psycopg2 must own JSON/date/timestamp parsing at the DB boundary."""
    src = _pg_source()

    assert "register_type(" not in src
    assert "new_type(" not in src
    assert "_JSONB_OID" not in src
    assert "_JSON_OID" not in src
    assert "JSONB_AS_TEXT" not in src
    assert "JSON_AS_TEXT" not in src
    assert "_TIMESTAMPTZ_OID" not in src
    assert "_TIMESTAMP_OID" not in src
    assert "_DATE_OID" not in src
    assert "TSTZ_AS_TEXT" not in src
    assert "TS_AS_TEXT" not in src
    assert "DATE_AS_TEXT" not in src
    assert "_timestamp_as_text" not in src


def test_runtime_sql_does_not_hardcode_plenora_schema_prefix():
    """Runtime SQL must use search_path instead of `plenora.` literals."""

    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if "plenora." not in text:
                    continue
                upper = " ".join(text.upper().split())
                if any(
                    marker in upper
                    for marker in (
                        "FROM PLENORA.",
                        "INTO PLENORA.",
                        "UPDATE PLENORA.",
                        "DELETE FROM PLENORA.",
                        "JOIN PLENORA.",
                    )
                ):
                    rel = path.relative_to(REPO_ROOT).as_posix()
                    violations.append(f"{rel}:{node.lineno}:{text.strip()[:120]}")

    assert not violations, (
        "Runtime SQL must not hardcode `plenora.`; pg_connection sets "
        "search_path from DB_SCHEMA. Use schema-relative table names.\n"
        + "\n".join(f"  - {item}" for item in violations)
    )
