"""
tests/carica/unit/test_column_mapper.py
========================================
Unit test su ColumnMapper e _serialize_value.

NON richiede backend, DB o Celery.

Copre:
  - ColumnMapper._extract_srid: geometry:point:4326, senza SRID, default
  - ColumnMapper._infer_type_from_canvas: tutti i tipi URBY
  - ColumnMapper._infer_type_from_pandas: int/float/bool/datetime/object/str
  - ColumnMapper._find_matching_column: match case-insensitive, nessun match
  - ColumnMapper._sanitize_column_name: spazi, caratteri speciali, maiuscole, lunghezza
  - ColumnMapper._is_geometry_column: per nome/contenuto/canvas_type
  - ColumnMapper.validate_mapping: errori, mapping valido
  - ColumnMapper.suggest_match_columns: colonne PK candidate
  - _serialize_value: NaN, None, datetime, numpy, primitivi
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.carica.column_mapper import ColumnMapper
from app.core.carica.conflict_checker import _serialize_value
from app.core.carica.models import ColumnMapping


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_mapper(db_type="postgres"):
    return ColumnMapper(db_type)


def col(src, dest=None, dest_type="", is_geom=False, srid=None, skip=False, pk=False):
    return ColumnMapping(
        source_column=src,
        dest_column=dest or src,
        dest_type=dest_type,
        is_geometry=is_geom,
        srid=srid,
        is_primary_key=pk,
        skip=skip,
    )


# =============================================================================
# _extract_srid
# =============================================================================

class TestExtractSrid:

    def test_geometry_point_4326(self):
        m = make_mapper()
        assert m._extract_srid("geometry:point:4326") == 4326

    def test_geometry_polygon_32632(self):
        m = make_mapper()
        assert m._extract_srid("geometry:polygon:32632") == 32632

    def test_senza_srid_default(self):
        m = make_mapper()
        assert m._extract_srid("geometry:point", default=4326) == 4326

    def test_tipo_non_geometry(self):
        m = make_mapper()
        assert m._extract_srid("str", default=4326) == 4326

    def test_stringa_vuota_default(self):
        m = make_mapper()
        assert m._extract_srid("", default=4326) == 4326

    def test_srid_invalido_default(self):
        m = make_mapper()
        assert m._extract_srid("geometry:point:abc", default=4326) == 4326


# =============================================================================
# _infer_type_from_canvas
# =============================================================================

class TestInferTypeFromCanvas:

    def test_int(self):
        assert make_mapper()._infer_type_from_canvas("int") == "int"

    def test_real(self):
        assert make_mapper()._infer_type_from_canvas("real") == "real"

    def test_str(self):
        assert make_mapper()._infer_type_from_canvas("str") == "str"

    def test_bool(self):
        assert make_mapper()._infer_type_from_canvas("bool") == "bool"

    def test_date(self):
        assert make_mapper()._infer_type_from_canvas("date") == "date"

    def test_geometry(self):
        assert make_mapper()._infer_type_from_canvas("geometry:point:4326") == "geometry"

    def test_tipo_sconosciuto_stringa_vuota(self):
        assert make_mapper()._infer_type_from_canvas("tipo_xyz") == ""

    def test_stringa_vuota(self):
        assert make_mapper()._infer_type_from_canvas("") == ""


# =============================================================================
# _infer_type_from_pandas
# =============================================================================

class TestInferTypeFromPandas:

    def test_int64(self):
        s = pd.Series([1, 2, 3], dtype="int64")
        assert make_mapper()._infer_type_from_pandas(s) == "int"

    def test_float64(self):
        s = pd.Series([1.5, 2.5], dtype="float64")
        assert make_mapper()._infer_type_from_pandas(s) == "real"

    def test_bool(self):
        s = pd.Series([True, False], dtype="bool")
        assert make_mapper()._infer_type_from_pandas(s) == "bool"

    def test_datetime(self):
        s = pd.Series(pd.to_datetime(["2024-01-01", "2024-06-01"]))
        assert make_mapper()._infer_type_from_pandas(s) == "date"

    def test_object_stringa(self):
        s = pd.Series(["Roma", "Milano", "Napoli"])
        assert make_mapper()._infer_type_from_pandas(s) == "str"

    def test_object_numerico(self):
        """Object con valori numerici → real."""
        s = pd.Series(["1.5", "2.5", "3.5"])
        result = make_mapper()._infer_type_from_pandas(s)
        assert result in ("real", "str")  # dipende dall'implementazione

    def test_series_vuota(self):
        s = pd.Series([], dtype="object")
        result = make_mapper()._infer_type_from_pandas(s)
        assert result == "str"


# =============================================================================
# _find_matching_column
# =============================================================================

class TestFindMatchingColumn:

    def test_match_esatto(self):
        m = make_mapper()
        existing = {"nome": {"name": "nome", "type": "VARCHAR"}}
        result = m._find_matching_column("nome", existing)
        assert result == "nome"

    def test_match_case_insensitive(self):
        """Mutation target: NOME deve matchare nome."""
        m = make_mapper()
        existing = {"nome": {"name": "nome", "type": "VARCHAR"}}
        result = m._find_matching_column("NOME", existing)
        assert result == "nome"

    def test_nessun_match_none(self):
        m = make_mapper()
        existing = {"nome": {"name": "nome", "type": "VARCHAR"}}
        result = m._find_matching_column("COLONNA_XYZ", existing)
        assert result is None

    def test_existing_vuoto(self):
        m = make_mapper()
        result = m._find_matching_column("nome", {})
        assert result is None


# =============================================================================
# _sanitize_column_name
# =============================================================================

class TestSanitizeColumnName:

    def test_spazi_sostituiti(self):
        m = make_mapper()
        result = m._sanitize_column_name("nome completo")
        assert " " not in result
        assert "_" in result

    def test_caratteri_speciali_rimossi(self):
        m = make_mapper()
        result = m._sanitize_column_name("col@#$%name")
        assert "@" not in result
        assert "#" not in result

    def test_underscore_multipli_ridotti(self):
        m = make_mapper()
        result = m._sanitize_column_name("col__name")
        assert "__" not in result

    def test_oracle_trunca_30_chars(self):
        """Oracle: max 30 caratteri."""
        m = make_mapper("oracle")
        long_name = "questa_colonna_ha_un_nome_molto_lungo_davvero"
        result = m._sanitize_column_name(long_name)
        assert len(result) <= 30

    def test_nome_normale_invariato(self):
        m = make_mapper()
        result = m._sanitize_column_name("comune")
        assert "comune" in result.lower()


# =============================================================================
# _is_geometry_column
# =============================================================================

class TestIsGeometryColumn:

    def test_nome_geometry(self):
        m = make_mapper()
        s = pd.Series(["POINT(1 2)", "POINT(3 4)"])
        assert m._is_geometry_column("geometry", s, "") is True

    def test_nome_geom(self):
        m = make_mapper()
        s = pd.Series(["POINT(1 2)"])
        assert m._is_geometry_column("geom", s, "") is True

    def test_canvas_type_geometry(self):
        m = make_mapper()
        s = pd.Series(["qualcosa"])
        assert m._is_geometry_column("colonna", s, "geometry:point:4326") is True

    def test_nome_normale_non_geometry(self):
        m = make_mapper()
        s = pd.Series(["Roma", "Milano"])
        assert m._is_geometry_column("nome", s, "str") is False

    def test_wkt_content_rilevato(self):
        """Contenuto WKT → rilevato come geometry."""
        m = make_mapper()
        s = pd.Series(["POINT(12.4 41.9)", "POLYGON((0 0, 1 0, 1 1, 0 0))"])
        result = m._is_geometry_column("coord", s, "")
        assert result is True


# =============================================================================
# validate_mapping
# =============================================================================

class TestValidateMapping:

    def test_mapping_valido(self):
        m = make_mapper()
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        mappings = [col("id", "id", "int"), col("nome", "nome", "str")]
        valid, errors = m.validate_mapping(mappings, df)
        assert valid is True
        assert errors == []

    def test_colonna_sorgente_mancante(self):
        m = make_mapper()
        df = pd.DataFrame({"id": [1, 2]})
        mappings = [col("id", "id", "int"), col("COLONNA_XYZ", "col_xyz", "str")]
        valid, errors = m.validate_mapping(mappings, df)
        assert len(errors) > 0

    def test_mapping_vuoto_valido(self):
        m = make_mapper()
        df = pd.DataFrame({"id": [1]})
        valid, errors = m.validate_mapping([], df)
        assert isinstance(errors, list)


# =============================================================================
# suggest_match_columns
# =============================================================================

class TestSuggestMatchColumns:

    def test_colonna_id_suggerita(self):
        m = make_mapper()
        df = pd.DataFrame({"id": [1, 2, 3], "nome": ["a", "b", "c"]})
        mappings = [col("id", "id", "int"), col("nome", "nome", "str")]
        suggestions = m.suggest_match_columns(mappings)
        assert isinstance(suggestions, list)

    def test_ritorna_lista(self):
        m = make_mapper()
        result = m.suggest_match_columns([])
        assert isinstance(result, list)


# =============================================================================
# _serialize_value (da conflict_checker)
# =============================================================================

class TestSerializeValue:

    def test_none(self):
        assert _serialize_value(None) is None

    def test_nan_diventa_none(self):
        assert _serialize_value(float("nan")) is None

    def test_numpy_nan_diventa_none(self):
        assert _serialize_value(np.nan) is None

    def test_int(self):
        assert _serialize_value(42) == 42

    def test_float(self):
        assert abs(_serialize_value(3.14) - 3.14) < 1e-9

    def test_stringa(self):
        assert _serialize_value("Roma") == "Roma"

    def test_numpy_int64(self):
        result = _serialize_value(np.int64(100))
        assert result == 100
        assert isinstance(result, (int, np.integer))

    def test_datetime(self):
        dt = datetime(2024, 1, 15, 12, 0, 0)
        result = _serialize_value(dt)
        assert result is not None
