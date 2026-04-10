"""
tests/estrai/unit/test_database_inspector.py
=============================================
Unit test su DatabaseInspector con mock cursor.

NON richiede DB reale. Usa mock della ConnectionFactory per simulare
le risposte del database senza connettersi a nessun server.

Obiettivi coverage:
  - get_tables_and_columns(): flusso completo per ogni DB con mock
  - get_views_and_columns(): stesso ma per le viste
  - SQLQueries: verifica le query SQL statiche non siano vuote e corrette
  - Dependency Injection: verifica che il mock venga chiamato correttamente
  - Tipo non supportato → ValueError
  - Mutation score: assert sui valori esatti dei tipi restituiti
"""

import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.estrai.database_inspector import (
    DatabaseInspector,
    DatabaseConnection,
    SQLQueries,
    TypeMapper,
    ResultProcessor,
    create_inspector,
)


# =============================================================================
# Helper: mock factory
# =============================================================================

def make_mock_factory(rows):
    """
    Crea una ConnectionFactory mock che restituisce le righe specificate
    quando viene chiamato cursor().fetchall().
    """
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_cursor.fetchone.return_value = rows[0] if rows else None

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    mock_factory = MagicMock()
    mock_factory.create_connection.return_value = mock_conn

    return mock_factory, mock_cursor, mock_conn


def make_db_conn(type_: str) -> DatabaseConnection:
    return DatabaseConnection(
        type=type_, host="localhost", port=5432,
        database="testdb", username="user", password="pass"
    )


# =============================================================================
# SQLQueries — verifica query statiche
# =============================================================================

class TestSQLQueries:

    def test_postgres_columns_non_vuota(self):
        assert len(SQLQueries.POSTGRES_COLUMNS.strip()) > 50
        assert "information_schema" in SQLQueries.POSTGRES_COLUMNS.lower()

    def test_postgres_view_columns_non_vuota(self):
        assert "views" in SQLQueries.POSTGRES_VIEW_COLUMNS.lower()

    def test_mssql_columns_usa_sys(self):
        assert "sys.tables" in SQLQueries.MSSQL_COLUMNS.lower() or \
               "sys." in SQLQueries.MSSQL_COLUMNS.lower()

    def test_mssql_view_columns_usa_sys_views(self):
        assert "sys.views" in SQLQueries.MSSQL_VIEW_COLUMNS.lower()

    def test_oracle_columns_usa_user_tab(self):
        assert "USER_TAB_COLUMNS" in SQLQueries.ORACLE_COLUMNS

    def test_sqlite_tables_query(self):
        assert "sqlite_master" in SQLQueries.SQLITE_TABLES
        assert "table" in SQLQueries.SQLITE_TABLES

    def test_sqlite_table_info_contiene_pragma(self):
        q = SQLQueries.sqlite_table_info("mia_tabella")
        assert "PRAGMA" in q
        assert "mia_tabella" in q

    def test_sqlite_views_query(self):
        assert "view" in SQLQueries.SQLITE_VIEWS.lower()


# =============================================================================
# DatabaseInspector — get_tables_and_columns con mock
# =============================================================================

class TestGetTablesAndColumns:

    def test_postgres_con_mock(self):
        """Inspector PostgreSQL con mock: restituisce tabelle e tipi corretti."""
        rows = [
            ("utenti", "id",     "integer",           "int4",    None,    None),
            ("utenti", "nome",   "character varying", "varchar", None,    None),
            ("utenti", "geom",   "USER-DEFINED",      "geometry","Point", 4326),
            ("ordini", "id",     "integer",           "int4",    None,    None),
            ("ordini", "totale", "numeric",           "numeric", None,    None),
        ]
        factory, _, _ = make_mock_factory(rows)
        inspector = DatabaseInspector(connection_factory=factory)
        conn = make_db_conn("postgres")

        result = inspector.get_tables_and_columns(conn)

        assert "utenti" in result
        assert "ordini" in result
        # Tipi corretti
        utenti_types = {c["name"]: c["type"] for c in result["utenti"]}
        assert utenti_types["id"]   == "int"
        assert utenti_types["nome"] == "str"
        assert utenti_types["geom"] == "geometry:point:4326"
        # Verifica che factory sia stato chiamato
        factory.create_connection.assert_called_once_with(conn)

    def test_mssql_con_mock(self):
        rows = [
            ("prodotti", "id",          "int"),
            ("prodotti", "nome",        "nvarchar"),
            ("prodotti", "disponibile", "bit"),
        ]
        factory, _, _ = make_mock_factory(rows)
        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_tables_and_columns(make_db_conn("mssql"))

        assert "prodotti" in result
        types = {c["name"]: c["type"] for c in result["prodotti"]}
        assert types["id"]          == "int"
        assert types["nome"]        == "str"
        assert types["disponibile"] == "bool"

    def test_oracle_con_mock(self):
        rows = [
            ("TAB", "ID",     "NUMBER",   10, 0),
            ("TAB", "PREZZO", "NUMBER",   10, 2),
            ("TAB", "FLAG",   "NUMBER",    1, None),
            ("TAB", "NOME",   "VARCHAR2", None, None),
        ]
        factory, _, _ = make_mock_factory(rows)
        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_tables_and_columns(make_db_conn("oracle"))

        assert "TAB" in result
        types = {c["name"]: c["type"] for c in result["TAB"]}
        assert types["ID"]     == "int"
        assert types["PREZZO"] == "real"
        assert types["FLAG"]   == "bool"
        assert types["NOME"]   == "str"

    def test_mysql_con_mock(self):
        rows = [
            ("vendite", "id",      "int",     "int(11)",      None),
            ("vendite", "attivo",  "tinyint", "tinyint(1)",   None),
            ("vendite", "prezzo",  "decimal", "decimal(10,2)",None),
        ]
        factory, _, _ = make_mock_factory(rows)
        inspector = DatabaseInspector(connection_factory=factory)

        # MySQL richiede che la connessione risponda a SELECT VERSION()
        mock_cursor = factory.create_connection.return_value.cursor.return_value
        mock_cursor.fetchone.return_value = ("8.0.32",)
        mock_cursor.fetchall.return_value = rows

        result = inspector.get_tables_and_columns(make_db_conn("mysql"))

        assert "vendite" in result
        types = {c["name"]: c["type"] for c in result["vendite"]}
        assert types["id"]     == "int"
        assert types["attivo"] == "bool"   # tinyint(1) → bool
        assert types["prezzo"] == "real"

    def test_mysql_mariadb_con_mock(self):
        """MariaDB: VERSION() contiene 'mariadb' → usa query semplice."""
        rows = [
            ("tab", "id", "int", "int(11)", None),
        ]
        factory, _, _ = make_mock_factory(rows)
        mock_cursor = factory.create_connection.return_value.cursor.return_value
        mock_cursor.fetchone.return_value = ("10.11.2-MariaDB",)
        mock_cursor.fetchall.return_value = rows

        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_tables_and_columns(make_db_conn("mysql"))
        assert "tab" in result

    def test_sqlite_con_mock(self):
        """SQLite richiede 2 passaggi: elenco tabelle + PRAGMA per ogni tabella."""
        tables_rows = [("comuni",), ("strade",)]
        pragma_rows = [
            (0, "id",    "INTEGER", 1, None, 1),
            (1, "nome",  "TEXT",    0, None, 0),
            (2, "geom",  "BLOB",    0, None, 0),
        ]

        mock_cursor = MagicMock()
        # Prima chiamata: elenco tabelle; poi PRAGMA per ogni tabella
        mock_cursor.fetchall.side_effect = [
            tables_rows,   # sqlite_master
            pragma_rows,   # PRAGMA comuni
            pragma_rows,   # PRAGMA strade
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        factory = MagicMock()
        factory.create_connection.return_value = mock_conn

        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_tables_and_columns(make_db_conn("sqlite"))

        assert "comuni" in result
        assert "strade" in result
        types = {c["name"]: c["type"] for c in result["comuni"]}
        assert types["id"]   == "int"
        assert types["nome"] == "str"

    def test_tipo_non_supportato_raise(self):
        """DB non supportato → ValueError."""
        inspector = DatabaseInspector()
        with pytest.raises(ValueError):
            inspector.get_tables_and_columns(make_db_conn("db2_inesistente"))

    def test_tabelle_vuote_restituisce_dict_vuoto(self):
        factory, _, _ = make_mock_factory([])
        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_tables_and_columns(make_db_conn("postgres"))
        assert result == {}

    def test_connessione_chiusa_dopo_query(self):
        """Mutation target: la connessione DEVE essere chiusa nel finally."""
        rows = [("tab", "id", "integer", "int4", None, None)]
        factory, _, mock_conn = make_mock_factory(rows)
        inspector = DatabaseInspector(connection_factory=factory)
        inspector.get_tables_and_columns(make_db_conn("postgres"))
        mock_conn.close.assert_called_once()


# =============================================================================
# DatabaseInspector — get_views_and_columns con mock
# =============================================================================

class TestGetViewsAndColumns:

    def test_postgres_views_con_mock(self):
        rows = [
            ("vista_utenti", "id",   "integer",           "int4",    None, None),
            ("vista_utenti", "nome", "character varying", "varchar", None, None),
        ]
        factory, _, _ = make_mock_factory(rows)
        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_views_and_columns(make_db_conn("postgres"))

        assert "vista_utenti" in result
        types = {c["name"]: c["type"] for c in result["vista_utenti"]}
        assert types["id"]   == "int"
        assert types["nome"] == "str"

    def test_mssql_views_con_mock(self):
        rows = [("v_prodotti", "id", "int"), ("v_prodotti", "nome", "nvarchar")]
        factory, _, _ = make_mock_factory(rows)
        inspector = DatabaseInspector(connection_factory=factory)
        result = inspector.get_views_and_columns(make_db_conn("mssql"))
        assert "v_prodotti" in result

    def test_tipo_non_supportato_views_raise(self):
        inspector = DatabaseInspector()
        with pytest.raises(ValueError):
            inspector.get_views_and_columns(make_db_conn("db2_inesistente"))


# =============================================================================
# create_inspector factory function
# =============================================================================

class TestCreateInspector:

    def test_crea_inspector_default(self):
        inspector = create_inspector()
        assert isinstance(inspector, DatabaseInspector)

    def test_crea_inspector_con_factory_custom(self):
        factory = MagicMock()
        inspector = create_inspector(connection_factory=factory)
        assert isinstance(inspector, DatabaseInspector)


# =============================================================================
# Dependency Injection — verifica che il mock sia effettivamente usato
# =============================================================================

class TestDependencyInjection:

    def test_factory_custom_usata(self):
        """Verifica che DI funzioni: la factory custom viene chiamata, non DefaultConnectionFactory."""
        rows = [("tab", "id", "integer", "int4", None, None)]
        factory, _, _ = make_mock_factory(rows)

        inspector = DatabaseInspector(connection_factory=factory)
        inspector.get_tables_and_columns(make_db_conn("postgres"))

        # La factory custom DEVE essere stata chiamata
        factory.create_connection.assert_called_once()

    def test_default_factory_NON_usata_con_mock(self):
        """Con factory mock, DefaultConnectionFactory non deve essere istanziata."""
        rows = [("tab", "id", "integer", "int4", None, None)]
        factory, _, _ = make_mock_factory(rows)

        with patch("app.core.estrai.database_inspector.DefaultConnectionFactory") as mock_default:
            inspector = DatabaseInspector(connection_factory=factory)
            inspector.get_tables_and_columns(make_db_conn("postgres"))
            # DefaultConnectionFactory NON deve essere istanziata
            mock_default.assert_not_called()
