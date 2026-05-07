"""
Contract test — helper puri di scripts/test_restore.py.

Il grosso dello script e' subprocess shell-out a psql/pg_restore (non
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Import dinamico: scripts/ non e' un package regolare.
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "test_restore.py"


@pytest.fixture(scope="module")
def test_restore_mod():
    spec = importlib.util.spec_from_file_location("test_restore_script", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["test_restore_script"] = mod
    spec.loader.exec_module(mod)
    return mod


# 1. derive_test_db_name


def test_derive_test_db_name_matches_pattern(test_restore_mod):
    name = test_restore_mod.derive_test_db_name()
    assert test_restore_mod.TEST_DB_PATTERN.match(name), (
        f"Nome generato non matcha pattern safe: {name}"
    )


def test_derive_test_db_name_is_postgres_safe_identifier(test_restore_mod):
    """PostgreSQL identifier validi: [a-zA-Z_][a-zA-Z0-9_]*, max 63 char."""
    name = test_restore_mod.derive_test_db_name()
    assert len(name) <= 63
    assert re.match(r"^[a-z_][a-z0-9_]*$", name)


def test_derive_test_db_name_two_calls_distinct_after_second(test_restore_mod):
    """Se chiamato nello stesso secondo, il nome puo' coincidere (ok per
    semantica del test: ogni run e' un evento — ISO al secondo).
    Ma dopo > 1s devono differire."""
    import time
    n1 = test_restore_mod.derive_test_db_name()
    time.sleep(1.1)
    n2 = test_restore_mod.derive_test_db_name()
    assert n1 != n2


# 2. split_db_url + make_target_url


def test_split_db_url_default_case(test_restore_mod):
    admin, original = test_restore_mod.split_db_url(
        "postgresql://user:pw@host:5432/urby_app"
    )
    assert admin == "postgresql://user:pw@host:5432/postgres"
    assert original == "urby_app"


def test_split_db_url_preserves_query_params(test_restore_mod):
    admin, _ = test_restore_mod.split_db_url(
        "postgresql://u:p@host/urby?sslmode=require"
    )
    assert "sslmode=require" in admin
    assert admin.endswith("/postgres?sslmode=require")


def test_split_db_url_no_path_falls_back_to_postgres(test_restore_mod):
    admin, original = test_restore_mod.split_db_url("postgresql://u:p@host")
    assert original == "postgres"
    assert admin.endswith("/postgres")


def test_make_target_url_points_at_test_db(test_restore_mod):
    admin = "postgresql://u:p@host:5432/postgres"
    target = test_restore_mod.make_target_url(admin, "plenora_restore_test_20260417_030000")
    assert target == "postgresql://u:p@host:5432/plenora_restore_test_20260417_030000"


# 3. TEST_DB_PATTERN rejects unsafe names


@pytest.mark.parametrize("bad_name", [
    "urby_app",                                    # prod DB name — deve essere rifiutato
    "postgres",                                    # DB sistema
    "plenora_restore_test_2026",                      # timestamp incompleto
    'plenora_restore_test_20260417_030000"; DROP DB', # injection
    "plenora_restore_test_20260417_030000x",          # char extra
    "URBY_restore_test_20260417_030000",           # maiuscole
    "",
    "plenora_restore_test_20260417_030000; SELECT 1",
])
def test_test_db_pattern_rejects_unsafe_names(test_restore_mod, bad_name):
    assert not test_restore_mod.TEST_DB_PATTERN.match(bad_name), (
        f"Pattern ha accettato nome unsafe: {bad_name!r}"
    )


@pytest.mark.parametrize("good_name", [
    "plenora_restore_test_20260417_030000",
    "plenora_restore_test_99991231_235959",
])
def test_test_db_pattern_accepts_valid_names(test_restore_mod, good_name):
    assert test_restore_mod.TEST_DB_PATTERN.match(good_name)


# 4. Safety gate: dropdb rifiuta nomi fuori pattern


def test_dropdb_refuses_names_outside_pattern(test_restore_mod, capsys):
    """dropdb e' l'ultima difesa: se qualcuno passa un nome fuori pattern,
    NON deve mai essere eseguito il DROP (rischio di cancellare un DB reale)."""
    calls = []

    def _fake_run(*args, **kwargs):
        calls.append(args)
        from unittest.mock import MagicMock
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        return m

    with patch("subprocess.run", side_effect=_fake_run):
        test_restore_mod.dropdb(
            "postgresql://u:p@host/postgres",
            "urby_app",  # NON matcha il pattern
        )

    assert calls == [], (
        "dropdb ha invocato subprocess.run con un nome fuori pattern! "
        "La safety gate TEST_DB_PATTERN.match() non ha bloccato la call."
    )

    # Logga anche un warning sullo skip
    out = capsys.readouterr().out
    assert "dropdb skippato" in out or "pattern" in out.lower()


def test_createdb_refuses_names_outside_pattern(test_restore_mod):
    """createdb solleva invece di chiamare psql su nomi fuori pattern."""
    with pytest.raises(ValueError, match="pattern"):
        test_restore_mod.createdb(
            "postgresql://u:p@host/postgres",
            "../etc/passwd",  # nome ovviamente unsafe
        )
