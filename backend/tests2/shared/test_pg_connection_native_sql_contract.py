from __future__ import annotations

import importlib
import sys

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


@pytest.fixture()
def _restore_pg_connection_module():
    """Ripristina l'oggetto modulo originale a fine test — in sys.modules
    E come attributo del package padre `app.db`.

    Questi test re-importano `app.db.pg_connection` da zero per verificare
    l'import passivo. Senza ripristino, il modulo FRESCO resta vivo mentre
    i consumer gia' importati (es. `migrations_runner`) tengono il VECCHIO
    oggetto: i patch su `app.db.pg_connection.*` colpiscono uno dei due e
    l'altro finisce sul pool reale → PoolTimeout nei test a valle.

    Il ripristino deve coprire ENTRAMBE le sedi: `import a.b.c as x` risolve
    l'attributo `c` sul package padre, non sys.modules — ripristinarne una
    sola lascia le due viste disallineate (ImportError "not in sys.modules"
    su importlib.reload, patch che mancano il bersaglio)."""
    import app.db as app_db_pkg

    original = sys.modules.get("app.db.pg_connection")
    yield
    if original is not None:
        sys.modules["app.db.pg_connection"] = original
        app_db_pkg.pg_connection = original
    else:
        sys.modules.pop("app.db.pg_connection", None)
        if hasattr(app_db_pkg, "pg_connection"):
            del app_db_pkg.pg_connection


def test_pg_connection_import_is_passive_without_database_url(
    monkeypatch, _restore_pg_connection_module
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sys.modules.pop("app.db.pg_connection", None)

    pg = importlib.import_module("app.db.pg_connection")

    assert pg._DATABASE_URL is None
    assert pg._POOL_CONFIG_LOADED is False
    with pytest.raises(RuntimeError, match="DATABASE_URL non configurato"):
        pg._get_pool()


def test_pg_connection_import_is_passive_with_malformed_pool_env(
    monkeypatch, _restore_pg_connection_module
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("DB_POOL_MIN", "not-an-int")
    sys.modules.pop("app.db.pg_connection", None)

    pg = importlib.import_module("app.db.pg_connection")

    assert pg._POOL_CONFIG_LOADED is False
    with pytest.raises(RuntimeError, match="DB_POOL_MIN"):
        pg._get_pool()


def test_pg_connection_rejects_legacy_question_mark_placeholders():
    import app.db.pg_connection as pg

    with pytest.raises(ValueError, match="native psycopg2 placeholders"):
        pg._adapt_params("SELECT * FROM users WHERE id = ?", ("u1",))


def test_pg_connection_keeps_native_psycopg2_placeholders_unchanged():
    import app.db.pg_connection as pg

    sql, params = pg._adapt_params(
        "SELECT * FROM users WHERE id = %s AND group_id = %s",
        ("u1", "g1"),
    )

    assert sql == "SELECT * FROM users WHERE id = %s AND group_id = %s"
    assert params == ("u1", "g1")
