"""
tests/carica/unit/test_ddl_column_extra.py
==========================================
Test aggiuntivi per DDLGenerator, ColumnMapper e ConflictChecker.

Copre:
  - DDLGenerator.generate_spatial_index: tutti i db_type
  - DDLGenerator.generate_update: SET + WHERE, geometria, parametri
  - DDLGenerator.generate_create_table: tutti i db_type
  - DDLGenerator.query_list_schemas/tables/exists/columns: tutti i db_type
  - ColumnMapper.validate_mapping: duplicati dest, tutte skip, >1 geom
  - ColumnMapper.suggest_match_columns: id/pk/codice/nessuno
  - ColumnMapper.auto_map: mapping automatico base
  - ConflictChecker._qualified_name e _quote_col: mariadb
"""

import pytest
import pandas as pd

pytestmark = pytest.mark.unit

from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.column_mapper import ColumnMapper
from app.core.carica.conflict_checker import ConflictChecker
from app.core.carica.models import ColumnMapping


def col(src, dest=None, dest_type="str", is_geom=False, srid=None, skip=False, pk=False):
    return ColumnMapping(
        source_column=src, dest_column=dest or src,
        dest_type=dest_type, is_geometry=is_geom,
        srid=srid, is_primary_key=pk, skip=skip,
    )


# =============================================================================
# DDLGenerator.generate_spatial_index
# =============================================================================

class TestGenerateSpatialIndex:

    def test_postgres_gist(self):
        gen = DDLGenerator("postgres")
        sqls = gen.generate_spatial_index("public", "test", "geometry", 4326)
        assert len(sqls) == 1
        assert "GIST" in sqls[0].upper()
        assert "geometry" in sqls[0].lower()

    def test_oracle_due_istruzioni(self):
        """Oracle restituisce 2 istruzioni: metadata + index."""
        gen = DDLGenerator("oracle")
        sqls = gen.generate_spatial_index("myschema", "mytable", "geom", 4326)
        assert len(sqls) == 2
        assert any("SDO_GEOM_METADATA" in s for s in sqls)
        assert any("SPATIAL_INDEX" in s for s in sqls)

    def test_mssql_spatial_index(self):
        gen = DDLGenerator("mssql")
        sqls = gen.generate_spatial_index("dbo", "test", "shape")
        assert len(sqls) == 1
        assert "SPATIAL INDEX" in sqls[0].upper()

    def test_mysql_spatial_index(self):
        gen = DDLGenerator("mysql")
        sqls = gen.generate_spatial_index("mydb", "test", "geom")
        assert len(sqls) == 1
        assert "SPATIAL INDEX" in sqls[0].upper()

    def test_strip_spazi_nel_nome_indice(self):
        """Spazi in table/geometry_column → rimossi nel nome indice."""
        gen = DDLGenerator("postgres")
        sqls = gen.generate_spatial_index("public", " test_c01 ", " geometry ")
        idx_name = "idx_test_c01_geometry_spatial"
        assert idx_name in sqls[0]

    def test_index_name_contiene_table_e_geom(self):
        gen = DDLGenerator("postgres")
        sqls = gen.generate_spatial_index("public", "immobili", "geom_wgs84")
        assert "immobili" in sqls[0]
        assert "geom_wgs84" in sqls[0]


# =============================================================================
# DDLGenerator.generate_update
# =============================================================================

class TestGenerateUpdate:

    def test_struttura_base(self):
        gen = DDLGenerator("postgres")
        cols = [col("nome", dest_type="str"), col("valore", dest_type="real")]
        sql, params = gen.generate_update("public", "test", cols, ["id"])
        assert "UPDATE" in sql.upper()
        assert "SET" in sql.upper()
        assert "WHERE" in sql.upper()

    def test_set_contiene_colonne(self):
        gen = DDLGenerator("postgres")
        cols = [col("nome"), col("valore")]
        sql, params = gen.generate_update("public", "test", cols, ["id"])
        assert '"nome"' in sql
        assert '"valore"' in sql

    def test_where_contiene_match_col(self):
        gen = DDLGenerator("postgres")
        cols = [col("nome")]
        sql, params = gen.generate_update("public", "test", cols, ["id"])
        assert "id" in sql.lower()
        assert "match_id" in params

    def test_match_multiplo(self):
        gen = DDLGenerator("postgres")
        cols = [col("valore")]
        sql, params = gen.generate_update("public", "test", cols, ["comune", "anno"])
        assert "comune" in sql.lower()
        assert "anno" in sql.lower()

    def test_geometry_in_update(self):
        """Colonna geometry → usa placeholder specifico."""
        gen = DDLGenerator("postgres")
        cols = [col("geom", is_geom=True, srid=4326)]
        sql, params = gen.generate_update("public", "test", cols, ["id"])
        assert "geom" in sql.lower()

    def test_oracle_uppercase(self):
        gen = DDLGenerator("oracle")
        cols = [col("nome")]
        sql, params = gen.generate_update("myschema", "mytable", cols, ["ID"])
        assert "MYTABLE" in sql


# =============================================================================
# DDLGenerator.generate_create_table — tutti i db_type
# =============================================================================

class TestGenerateCreateTableAllDb:

    def _cols(self):
        return [col("id", dest_type="int"), col("nome", dest_type="str"),
                col("geom", is_geom=True, srid=4326)]

    def test_oracle_create(self):
        gen = DDLGenerator("oracle")
        sql = gen.generate_create_table("myschema", "mytable", self._cols())
        assert "CREATE TABLE" in sql.upper()
        assert "MYTABLE" in sql

    def test_mssql_create(self):
        gen = DDLGenerator("mssql")
        sql = gen.generate_create_table("dbo", "test", self._cols())
        assert "CREATE TABLE" in sql.upper()
        assert '"dbo"."test"' in sql

    def test_mysql_create(self):
        gen = DDLGenerator("mysql")
        sql = gen.generate_create_table("mydb", "test", self._cols())
        assert "CREATE TABLE" in sql.upper()
        assert "`mydb`" in sql

    def test_tutti_db_non_crashano(self):
        for db in ["postgres", "oracle", "mssql", "mysql", "mariadb"]:
            gen = DDLGenerator(db)
            sql = gen.generate_create_table("public", "test", [col("id", dest_type="int")])
            assert "CREATE TABLE" in sql.upper()


# =============================================================================
# DDLGenerator query helpers — tutti i db_type
# =============================================================================

class TestQueryHelpersAllDb:

    def test_list_schemas_tutti_db(self):
        for db in ["postgres", "oracle", "mssql", "mysql"]:
            gen = DDLGenerator(db)
            sql = gen.query_list_schemas()
            assert isinstance(sql, str) and len(sql) > 0

    def test_list_tables_tutti_db(self):
        for db in ["postgres", "oracle", "mssql", "mysql"]:
            gen = DDLGenerator(db)
            sql = gen.query_list_tables("myschema")
            assert isinstance(sql, str) and len(sql) > 0

    def test_table_exists_tutti_db(self):
        for db in ["postgres", "oracle", "mssql", "mysql"]:
            gen = DDLGenerator(db)
            sql = gen.query_table_exists("public", "test")
            assert isinstance(sql, str) and len(sql) > 0

    def test_table_columns_tutti_db(self):
        for db in ["postgres", "oracle", "mssql", "mysql"]:
            gen = DDLGenerator(db)
            sql = gen.query_table_columns("public", "test")
            assert isinstance(sql, str) and len(sql) > 0

    def test_list_tables_oracle_uppercase(self):
        """Oracle → schema uppercase nella query."""
        gen = DDLGenerator("oracle")
        sql = gen.query_list_tables("myschema")
        assert "MYSCHEMA" in sql

    def test_table_exists_contiene_nome_tabella(self):
        gen = DDLGenerator("postgres")
        sql = gen.query_table_exists("public", "immobili")
        assert "immobili" in sql.lower()


# =============================================================================
# ColumnMapper.validate_mapping — casi edge
# =============================================================================

class TestValidateMappingExtra:

    def test_duplicati_destinazione(self):
        """Due colonne mappate allo stesso nome dest → errore."""
        m = ColumnMapper("postgres")
        df = pd.DataFrame({"a": [1], "b": [2]})
        mappings = [
            col("a", dest="nome"),
            col("b", dest="nome"),  # duplicato!
        ]
        valid, errors = m.validate_mapping(mappings, df)
        assert valid is False
        assert any("duplicato" in e.lower() or "nome" in e.lower() for e in errors)

    def test_tutte_skip_errore(self):
        """Tutte le colonne skip → errore."""
        m = ColumnMapper("postgres")
        df = pd.DataFrame({"a": [1], "b": [2]})
        mappings = [col("a", skip=True), col("b", skip=True)]
        valid, errors = m.validate_mapping(mappings, df)
        assert valid is False

    def test_due_geometrie_errore(self):
        """Più di una colonna geometry → errore."""
        m = ColumnMapper("postgres")
        df = pd.DataFrame({"geom1": ["POINT(1 2)"], "geom2": ["POINT(3 4)"]})
        mappings = [
            col("geom1", is_geom=True, srid=4326),
            col("geom2", is_geom=True, srid=4326),
        ]
        valid, errors = m.validate_mapping(mappings, df)
        assert valid is False
        assert any("geometria" in e.lower() or "geometry" in e.lower() for e in errors)

    def test_mapping_valido_con_geometria(self):
        m = ColumnMapper("postgres")
        df = pd.DataFrame({"id": [1], "geom": ["POINT(1 2)"]})
        mappings = [col("id", dest_type="int"), col("geom", is_geom=True, srid=4326)]
        valid, errors = m.validate_mapping(mappings, df)
        assert valid is True
        assert errors == []


# =============================================================================
# ColumnMapper.suggest_match_columns
# =============================================================================

class TestSuggestMatchColumnsExtra:

    def test_colonna_id_priorita(self):
        """Colonna 'id' esatta → primo suggerimento."""
        m = ColumnMapper("postgres")
        mappings = [col("id"), col("nome"), col("valore")]
        result = m.suggest_match_columns(mappings)
        assert result[0] == "id"

    def test_colonna_pk(self):
        m = ColumnMapper("postgres")
        mappings = [col("pk"), col("nome")]
        result = m.suggest_match_columns(mappings)
        assert "pk" in result

    def test_colonna_con_id_finale(self):
        """comune_id → suggerita."""
        m = ColumnMapper("postgres")
        mappings = [col("comune_id"), col("nome")]
        result = m.suggest_match_columns(mappings)
        assert "comune_id" in result

    def test_colonna_codice(self):
        m = ColumnMapper("postgres")
        mappings = [col("codice_fiscale"), col("nome")]
        result = m.suggest_match_columns(mappings)
        assert "codice_fiscale" in result

    def test_nessuna_candidata(self):
        m = ColumnMapper("postgres")
        mappings = [col("nome"), col("valore")]
        result = m.suggest_match_columns(mappings)
        assert isinstance(result, list)

    def test_max_3_suggerimenti(self):
        """Mutation target: max 3 suggerimenti."""
        m = ColumnMapper("postgres")
        mappings = [col("id"), col("pk"), col("key"), col("codice_id"), col("item_id")]
        result = m.suggest_match_columns(mappings)
        assert len(result) <= 3

    def test_geometria_esclusa(self):
        """Colonne geometry non suggerite."""
        m = ColumnMapper("postgres")
        mappings = [col("geom_id", is_geom=True), col("id")]
        result = m.suggest_match_columns(mappings)
        assert "geom_id" not in result

    def test_skip_escluso(self):
        m = ColumnMapper("postgres")
        mappings = [col("id", skip=True), col("nome")]
        result = m.suggest_match_columns(mappings)
        assert "id" not in result


# =============================================================================
# ColumnMapper.auto_map
# =============================================================================

class TestAutoMap:

    def test_auto_map_base(self):
        m = ColumnMapper("postgres")
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"], "valore": [1.0, 2.0]})
        mappings = m.auto_map(df)
        assert len(mappings) == 3
        sources = [m.source_column for m in mappings]
        assert "id" in sources
        assert "nome" in sources
        assert "valore" in sources

    def test_auto_map_tipi_inferiti(self):
        m = ColumnMapper("postgres")
        df = pd.DataFrame({
            "id":     pd.array([1, 2], dtype="int64"),
            "nome":   ["a", "b"],
            "prezzo": [1.5, 2.5],
            "attivo": [True, False],
        })
        mappings = m.auto_map(df)
        by_col = {mp.source_column: mp for mp in mappings}
        assert by_col["id"].dest_type == "int"
        assert by_col["prezzo"].dest_type == "real"
        assert by_col["attivo"].dest_type == "bool"

    def test_auto_map_geometria_rilevata(self):
        m = ColumnMapper("postgres")
        df = pd.DataFrame({"id": [1], "geometry": ["POINT(12.4 41.9)"]})
        mappings = m.auto_map(df, canvas_types={"geometry": "geometry:point:4326"})
        geom_mapping = next((mp for mp in mappings if mp.source_column == "geometry"), None)
        assert geom_mapping is not None
        assert geom_mapping.is_geometry is True

    def test_auto_map_match_colonne_esistenti(self):
        """Match case-insensitive con tabella esistente."""
        m = ColumnMapper("postgres")
        df = pd.DataFrame({"Nome": ["a"], "ID": [1]})
        existing = [
            {"name": "nome", "type": "VARCHAR"},
            {"name": "id",   "type": "INTEGER"},
        ]
        mappings = m.auto_map(df, existing_columns=existing)
        by_col = {mp.source_column: mp for mp in mappings}
        # "Nome" deve matchare "nome" esistente
        assert by_col["Nome"].dest_column == "nome"

    def test_auto_map_srid_da_canvas(self):
        m = ColumnMapper("postgres")
        df = pd.DataFrame({"geom": ["POINT(1 2)"]})
        mappings = m.auto_map(df, canvas_types={"geom": "geometry:point:32632"}, srid=32632)
        geom_m = next(mp for mp in mappings if mp.source_column == "geom")
        assert geom_m.srid == 32632


# =============================================================================
# ConflictChecker — mariadb
# =============================================================================

class TestConflictCheckerMariadb:

    def test_qualified_name_mariadb_backtick(self):
        cc = ConflictChecker(None, "mariadb")
        result = cc._qualified_name("mydb", "test")
        assert "`mydb`" in result
        assert "`test`" in result

    def test_quote_col_mariadb_backtick(self):
        cc = ConflictChecker(None, "mariadb")
        assert cc._quote_col("nome") == "`nome`"

    def test_check_internal_duplicates_singola_colonna(self):
        from unittest.mock import MagicMock
        cc = ConflictChecker(MagicMock(), "postgres")
        df = pd.DataFrame({"codice": ["A", "A", "B", "C"], "val": [1, 2, 3, 4]})
        conflicts = cc._check_internal_duplicates(df, ["codice"])
        assert conflicts > 0

    def test_check_internal_no_duplicates_multicolonna(self):
        from unittest.mock import MagicMock
        cc = ConflictChecker(MagicMock(), "postgres")
        df = pd.DataFrame({
            "comune": ["Roma", "Roma", "Milano"],
            "anno":   [2023,   2024,   2023],
        })
        # Combinazioni uniche → nessun conflitto
        conflicts = cc._check_internal_duplicates(df, ["comune", "anno"])
        assert conflicts == 0
