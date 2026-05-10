"""Fixture parametrizzate che si risolvono in base al param ``dialect``
(o ``auth_style``) iniettato dal helper ``parametrize_*``.

Queste fixture **non** vivono in ``conftest.py`` per non sporcare il
namespace globale: i test che le vogliono fanno ``from runner.parametrized_fixtures
import db_connection_for_dialect`` (oppure si rilevano via plugin se
ne registriamo uno in futuro).

Per renderle visibili a pytest senza modificare ``conftest.py``, il
modulo è importato da ``runner.tests.conftest`` (vedi quel file) — in
questo modo le fixture vengono raccolte solo per i test sotto
``runner/tests/`` che le richiedono esplicitamente.

Pattern:

    @parametrize_dialects(only_light=True)
    def test_x(dialect: str, db_connection_for_dialect: str):
        ...

``request.getfixturevalue("dialect")`` legge il param indirettamente,
così non serve passare il fixture chain a mano.
"""

from __future__ import annotations

import pytest


# ── DB connection per dialetto ────────────────────────────────────────

@pytest.fixture
def db_connection_for_dialect(request: pytest.FixtureRequest) -> str:
    """Risolve il ``connection_id`` corretto in base al param ``dialect``.

    Richiede che il test sia decorato con ``@parametrize_dialects(...)``
    così che ``dialect`` sia disponibile come param/fixture indiretto.
    Se ``dialect`` non è disponibile fallback su ``postgres``.

    Le fixture session-scope ``postgres_conn_id``, ``mysql_conn_id``,
    ``sqlserver_conn_id``, ``oracle_conn_id`` sono definite nel
    ``conftest.py`` del runner. SQLite non ha fixture dedicata: il
    contratto plenora la accetta come type ma il file non è seedato in
    docker; per ora skippiamo.
    """
    dialect = (
        request.getfixturevalue("dialect")
        if "dialect" in request.fixturenames
        else "postgres"
    )

    if dialect == "sqlite":
        pytest.skip(
            "SQLite connection fixture non ancora implementata "
            "(no container dedicato nel compose)."
        )

    fixture_map = {
        "postgres": "postgres_conn_id",
        "mysql": "mysql_conn_id",
        "mssql": "sqlserver_conn_id",
        "oracle": "oracle_conn_id",
    }
    fixture_name = fixture_map.get(dialect)
    if not fixture_name:
        pytest.fail(f"Dialect non supportato: {dialect!r}")

    return str(request.getfixturevalue(fixture_name))


# ── API connection per auth_style ─────────────────────────────────────

@pytest.fixture
def api_conn_for_auth_style(request: pytest.FixtureRequest) -> str:
    """Risolve ``api_connection_id`` in base al param ``auth_style``.

    Mappa gli auth style esposti da ``parametrize_auth_styles`` alle
    fixture session-scope già presenti in ``conftest.py``:

    - ``api_key_*``      → ``api_conn_apikey_id``
    - ``oauth2_*``       → ``api_conn_oauth2_id``
    - ``arcgis_token``   → ``api_conn_arcgis_id``
    - ``bearer/basic``   → skip (fixture dedicata da aggiungere)
    """
    style = (
        request.getfixturevalue("auth_style")
        if "auth_style" in request.fixturenames
        else "api_key_header"
    )

    fixture_map: dict[str, str] = {
        "api_key_header": "api_conn_apikey_id",
        "api_key_query": "api_conn_apikey_id",
        "oauth2_client_credentials": "api_conn_oauth2_id",
        "oauth2_password": "api_conn_oauth2_id",
        "arcgis_token": "api_conn_arcgis_id",
    }

    fixture_name = fixture_map.get(style)
    if fixture_name is None:
        pytest.skip(
            f"Fixture api_connection per auth_style={style!r} non disponibile "
            "(es. bearer/basic richiedono nuove fixture in conftest.py)."
        )

    return str(request.getfixturevalue(fixture_name))


# ── Datasource per file format ────────────────────────────────────────

@pytest.fixture
def datasource_for_format(request: pytest.FixtureRequest) -> str:
    """Risolve ``datasource_id`` in base al param ``file_format``.

    Per ora i format spatial e tabular vengono entrambi serviti dal
    SFTP fixture (``sftp_datasource_id``). In futuro si può estendere
    con datasource dedicate per cloud (S3) o local.
    """
    fmt = (
        request.getfixturevalue("file_format")
        if "file_format" in request.fixturenames
        else "csv"
    )
    # Tutti i format passano per SFTP fixture nel seed corrente
    _ = fmt  # ack del param per chiarezza
    return str(request.getfixturevalue("sftp_datasource_id"))
