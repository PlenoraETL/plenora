"""
tests/manipola/unit/test_transforms_base2.py
=============================================
Unit test per le funzioni op_* non coperte in test_transforms_base.py:
  - op_join: inner/left/right/outer, < 2 input, dedup colonne, type mismatch
  - op_union: concatenazione verticale, singolo input, vuoto
  - op_split_column: delimiter, new_columns custom, max_splits, col inesistente
  - op_mask_data: cf/email/phone/iban/custom, overwrite, col inesistente
  - op_text_normalize: trim/lower/upper/title/strip_accents/full, overwrite
  - op_table_diff: ADDED/DELETED/MODIFIED/UNCHANGED, chiavi mancanti, < 2 input
  - op_geo_from_coords: lat/lon → geometry WKT
"""

import pytest
import pandas as pd
import numpy as np

pytestmark = pytest.mark.unit

from app.core.manipola.transforms.base import (
    op_join,
    op_union,
    op_split_column,
    op_mask_data,
    op_text_normalize,
    op_table_diff,
    op_geo_from_coords,
)


# =============================================================================
# op_join
# =============================================================================

class TestOpJoin:

    def _dfs(self):
        left = pd.DataFrame({"id": [1, 2, 3], "nome": ["a", "b", "c"]})
        right = pd.DataFrame({"id": [1, 2, 4], "valore": [10, 20, 40]})
        return left, right

    def test_inner_join(self):
        left, right = self._dfs()
        result = op_join([left, right], left_on="id", right_on="id", how="inner")
        assert len(result) == 2
        assert "nome" in result.columns or "nome_L" in result.columns
        assert "valore" in result.columns or "valore_R" in result.columns

    def test_left_join_preserva_tutti(self):
        left, right = self._dfs()
        result = op_join([left, right], left_on="id", right_on="id", how="left")
        assert len(result) == 3

    def test_right_join(self):
        left, right = self._dfs()
        result = op_join([left, right], left_on="id", right_on="id", how="right")
        assert len(result) == 3

    def test_outer_join(self):
        left, right = self._dfs()
        result = op_join([left, right], left_on="id", right_on="id", how="outer")
        assert len(result) == 4

    def test_meno_di_2_input_passthrough(self):
        """Mutation target: < 2 input → restituisce il primo."""
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        result = op_join([df], left_on="id", right_on="id")
        pd.testing.assert_frame_equal(result, df)

    def test_zero_input_dataframe_vuoto(self):
        result = op_join([], left_on="id", right_on="id")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_suffissi_L_R_colonne_duplicate(self):
        """Colonne duplicate rinominate con _L e _R."""
        left  = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        right = pd.DataFrame({"id": [1, 2], "nome": ["x", "y"]})
        result = op_join([left, right], left_on="id", right_on="id", how="inner")
        cols = list(result.columns)
        # deve avere "nome_L" e "nome_R" o "nome_x" e "nome_y"
        assert any("nome" in c for c in cols)
        nome_cols = [c for c in cols if "nome" in c]
        assert len(nome_cols) == 2

    def test_chiavi_diverse_left_right(self):
        """left_on e right_on possono essere colonne diverse."""
        left  = pd.DataFrame({"codice": [1, 2], "nome": ["a", "b"]})
        right = pd.DataFrame({"id_ente": [1, 2], "desc": ["x", "y"]})
        result = op_join([left, right], left_on="codice", right_on="id_ente", how="inner")
        assert len(result) == 2

    def test_type_mismatch_convertiti(self):
        """Tipi diversi sulle chiavi → auto-conversione a stringa."""
        left  = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        right = pd.DataFrame({"id": ["1", "2"], "valore": [10, 20]})
        result = op_join([left, right], left_on="id", right_on="id", how="inner")
        assert len(result) == 2


# =============================================================================
# op_union
# =============================================================================

class TestOpUnion:

    def test_union_due_df(self):
        df1 = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        df2 = pd.DataFrame({"id": [3, 4], "nome": ["c", "d"]})
        result = op_union([df1, df2])
        assert len(result) == 4
        assert list(result["id"]) == [1, 2, 3, 4]

    def test_union_tre_df(self):
        dfs = [pd.DataFrame({"x": [i]}) for i in range(3)]
        result = op_union(dfs)
        assert len(result) == 3

    def test_singolo_input_passthrough(self):
        df = pd.DataFrame({"id": [1, 2]})
        result = op_union([df])
        pd.testing.assert_frame_equal(result, df)

    def test_lista_vuota_dataframe_vuoto(self):
        result = op_union([])
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_ignore_index_true(self):
        """Mutation target: indici resettati da 0."""
        df1 = pd.DataFrame({"id": [1, 2]}, index=[10, 11])
        df2 = pd.DataFrame({"id": [3, 4]}, index=[20, 21])
        result = op_union([df1, df2], ignore_index=True)
        assert list(result.index) == [0, 1, 2, 3]

    def test_ignore_index_false(self):
        df1 = pd.DataFrame({"id": [1, 2]}, index=[10, 11])
        df2 = pd.DataFrame({"id": [3, 4]}, index=[20, 21])
        result = op_union([df1, df2], ignore_index=False)
        assert list(result.index) == [10, 11, 20, 21]


# =============================================================================
# op_split_column
# =============================================================================

class TestOpSplitColumn:

    def test_split_base(self):
        df = pd.DataFrame({"indirizzo": ["Via Roma,12,00100", "Via Napoli,5,80100"]})
        result = op_split_column(df, "indirizzo", delimiter=",")
        assert "indirizzo_part1" in result.columns
        assert "indirizzo_part2" in result.columns
        assert "indirizzo_part3" in result.columns

    def test_new_columns_custom(self):
        df = pd.DataFrame({"nome_completo": ["Mario Rossi", "Luigi Bianchi"]})
        result = op_split_column(df, "nome_completo", delimiter=" ",
                                  new_columns=["nome", "cognome"])
        assert "nome" in result.columns
        assert "cognome" in result.columns
        assert result["nome"].iloc[0] == "Mario"
        assert result["cognome"].iloc[0] == "Rossi"

    def test_max_splits(self):
        """max_splits=1 → al massimo 2 parti."""
        df = pd.DataFrame({"tag": ["a,b,c,d"]})
        result = op_split_column(df, "tag", delimiter=",", max_splits=1)
        assert "tag_part1" in result.columns
        assert "tag_part2" in result.columns
        assert "tag_part3" not in result.columns

    def test_colonna_inesistente_restituisce_invariato(self):
        df = pd.DataFrame({"id": [1, 2]})
        result = op_split_column(df, "COLONNA_XYZ", delimiter=",")
        assert list(result.columns) == ["id"]

    def test_delimiter_diverso(self):
        df = pd.DataFrame({"data": ["2024-01-15", "2024-06-30"]})
        result = op_split_column(df, "data", delimiter="-",
                                  new_columns=["anno", "mese", "giorno"])
        assert result["anno"].iloc[0] == "2024"
        assert result["mese"].iloc[0] == "01"
        assert result["giorno"].iloc[0] == "15"

    def test_colonna_originale_preservata(self):
        df = pd.DataFrame({"tag": ["a,b", "c,d"]})
        result = op_split_column(df, "tag", delimiter=",")
        assert "tag" in result.columns


# =============================================================================
# op_mask_data
# =============================================================================

class TestOpMaskData:

    def test_mask_cf(self):
        df = pd.DataFrame({"cf": ["RSSMRA80A01H501Z", "BNCGPP75B12F205X"]})
        result = op_mask_data(df, [{"column": "cf", "mask_type": "cf"}])
        assert "cf_masked" in result.columns
        assert result["cf_masked"].iloc[0] != "RSSMRA80A01H501Z"

    def test_mask_email(self):
        df = pd.DataFrame({"email": ["mario@example.com", "luigi@test.it"]})
        result = op_mask_data(df, [{"column": "email", "mask_type": "email"}])
        assert "email_masked" in result.columns
        assert "@" in result["email_masked"].iloc[0]  # mantiene struttura email

    def test_mask_phone(self):
        df = pd.DataFrame({"tel": ["3331234567", "0615678901"]})
        result = op_mask_data(df, [{"column": "tel", "mask_type": "phone"}])
        assert "tel_masked" in result.columns

    def test_mask_iban(self):
        df = pd.DataFrame({"iban": ["IT60X0542811101000000123456"]})
        result = op_mask_data(df, [{"column": "iban", "mask_type": "iban"}])
        assert "iban_masked" in result.columns

    def test_mask_custom(self):
        df = pd.DataFrame({"codice": ["ABCDE12345"]})
        result = op_mask_data(df, [{
            "column": "codice", "mask_type": "custom",
            "chars_start": 2, "chars_end": 2, "mask_char": "*"
        }])
        assert "codice_masked" in result.columns
        masked = result["codice_masked"].iloc[0]
        assert masked.startswith("AB")
        assert masked.endswith("45")
        assert "*" in masked

    def test_overwrite_true(self):
        """overwrite=True → sovrascrive colonna originale."""
        df = pd.DataFrame({"cf": ["RSSMRA80A01H501Z"]})
        result = op_mask_data(df, [{"column": "cf", "mask_type": "cf"}], overwrite=True)
        assert "cf_masked" not in result.columns
        assert "cf" in result.columns
        assert result["cf"].iloc[0] != "RSSMRA80A01H501Z"

    def test_nessuna_maschera_restituisce_invariato(self):
        df = pd.DataFrame({"cf": ["RSSMRA80A01H501Z"]})
        result = op_mask_data(df, [])
        assert list(result.columns) == ["cf"]

    def test_colonna_inesistente_skippata(self):
        df = pd.DataFrame({"id": [1, 2]})
        result = op_mask_data(df, [{"column": "COLONNA_XYZ", "mask_type": "cf"}])
        assert list(result.columns) == ["id"]


# =============================================================================
# op_text_normalize
# =============================================================================

class TestOpTextNormalize:

    def test_trim(self):
        df = pd.DataFrame({"nome": ["  Mario  ", " Luigi "]})
        result = op_text_normalize(df, columns=["nome"], operations="trim")
        assert result["nome"].iloc[0] == "Mario"
        assert result["nome"].iloc[1] == "Luigi"

    def test_lower(self):
        df = pd.DataFrame({"nome": ["MARIO ROSSI", "LUIGI BIANCHI"]})
        result = op_text_normalize(df, columns=["nome"], operations="lower")
        assert result["nome"].iloc[0] == "mario rossi"

    def test_upper(self):
        df = pd.DataFrame({"nome": ["mario rossi"]})
        result = op_text_normalize(df, columns=["nome"], operations="upper")
        assert result["nome"].iloc[0] == "MARIO ROSSI"

    def test_title(self):
        df = pd.DataFrame({"nome": ["mario rossi"]})
        result = op_text_normalize(df, columns=["nome"], operations="title")
        assert result["nome"].iloc[0] == "Mario Rossi"

    def test_strip_accents(self):
        df = pd.DataFrame({"nome": ["Città", "caffè", "über"]})
        result = op_text_normalize(df, columns=["nome"], operations="strip_accents")
        assert result["nome"].iloc[0] == "Citta"
        assert result["nome"].iloc[1] == "caffe"
        assert result["nome"].iloc[2] == "uber"

    def test_full(self):
        """full = trim + lower + strip_accents + strip_double_spaces."""
        df = pd.DataFrame({"nome": ["  CITTÀ  DI  ROMA  "]})
        result = op_text_normalize(df, columns=["nome"], operations="full")
        assert result["nome"].iloc[0] == "citta di roma"

    def test_strip_double_spaces(self):
        df = pd.DataFrame({"testo": ["ciao   mondo"]})
        result = op_text_normalize(df, columns=["testo"], operations="strip_double_spaces")
        assert result["testo"].iloc[0] == "ciao mondo"

    def test_overwrite_false_crea_colonna_norm(self):
        """overwrite=False → crea colonna _norm, non sovrascrive."""
        df = pd.DataFrame({"nome": ["MARIO"]})
        result = op_text_normalize(df, columns=["nome"], operations="lower", overwrite=False)
        assert "nome" in result.columns
        assert "nome_norm" in result.columns
        assert result["nome"].iloc[0] == "MARIO"
        assert result["nome_norm"].iloc[0] == "mario"

    def test_colonne_vuote_restituisce_invariato(self):
        df = pd.DataFrame({"nome": ["MARIO"]})
        result = op_text_normalize(df, columns=[], operations="lower")
        assert result["nome"].iloc[0] == "MARIO"

    def test_colonna_inesistente_ignorata(self):
        df = pd.DataFrame({"nome": ["MARIO"]})
        result = op_text_normalize(df, columns=["COLONNA_XYZ"], operations="lower")
        assert list(result.columns) == ["nome"]

    def test_valori_null_gestiti(self):
        """Null non devono crashare."""
        df = pd.DataFrame({"nome": ["Mario", None, "Luigi"]})
        result = op_text_normalize(df, columns=["nome"], operations="lower")
        assert result["nome"].iloc[0] == "mario"
        assert result["nome"].iloc[2] == "luigi"


# =============================================================================
# op_table_diff
# =============================================================================

class TestOpTableDiff:

    def _base_dfs(self):
        old = pd.DataFrame({
            "id":    [1, 2, 3, 4],
            "nome":  ["a", "b", "c", "d"],
            "valore":[10, 20, 30, 40],
        })
        new = pd.DataFrame({
            "id":    [1, 2, 3, 5],       # 4 eliminato, 5 aggiunto
            "nome":  ["a", "B", "c", "e"], # 2.nome modificato
            "valore":[10, 20, 30, 50],
        })
        return old, new

    def test_added(self):
        old, new = self._base_dfs()
        result = op_table_diff([old, new], left_keys="id", right_keys="id")
        added = result[result["_diff_status"] == "ADDED"]
        assert len(added) == 1
        assert added["id"].iloc[0] == 5

    def test_deleted(self):
        old, new = self._base_dfs()
        result = op_table_diff([old, new], left_keys="id", right_keys="id")
        deleted = result[result["_diff_status"] == "DELETED"]
        assert len(deleted) == 1
        assert deleted["id"].iloc[0] == 4

    def test_modified(self):
        old, new = self._base_dfs()
        result = op_table_diff([old, new], left_keys="id", right_keys="id")
        modified = result[result["_diff_status"] == "MODIFIED"]
        assert len(modified) == 1
        assert modified["id"].iloc[0] == 2

    def test_unchanged(self):
        old, new = self._base_dfs()
        result = op_table_diff([old, new], left_keys="id", right_keys="id",
                                include_unchanged="yes")
        unchanged = result[result["_diff_status"] == "UNCHANGED"]
        assert len(unchanged) == 2   # id 1 e 3

    def test_unchanged_escluso_di_default(self):
        """Mutation target: di default include_unchanged='no' → UNCHANGED non nel risultato."""
        old, new = self._base_dfs()
        result = op_table_diff([old, new], left_keys="id", right_keys="id")
        assert "UNCHANGED" not in result["_diff_status"].values

    def test_diff_columns_presenti(self):
        """Colonne di diff sempre presenti nel risultato."""
        old, new = self._base_dfs()
        result = op_table_diff([old, new], left_keys="id", right_keys="id")
        assert "_diff_status" in result.columns
        assert "_diff_columns" in result.columns
        assert "_diff_old_values" in result.columns

    def test_meno_di_2_input_passthrough(self):
        old, _ = self._base_dfs()
        result = op_table_diff([old], left_keys="id", right_keys="id")
        assert len(result) == len(old)

    def test_chiavi_mancanti_restituisce_vecchio(self):
        old, new = self._base_dfs()
        result = op_table_diff([old, new], left_keys="", right_keys="")
        pd.testing.assert_frame_equal(result, old)

    def test_chiave_inesistente_restituisce_vecchio(self):
        old, new = self._base_dfs()
        result = op_table_diff([old, new], left_keys="COLONNA_XYZ", right_keys="id")
        pd.testing.assert_frame_equal(result, old)

    def test_separatore_custom(self):
        old, new = self._base_dfs()
        result = op_table_diff([old, new], left_keys="id", right_keys="id", separator="|")
        modified = result[result["_diff_status"] == "MODIFIED"]
        if len(modified) > 0 and modified["_diff_columns"].iloc[0]:
            # Se ci sono più colonne modificate, sono separate da |
            assert "#" not in modified["_diff_columns"].iloc[0]


# =============================================================================
# op_geo_from_coords
# =============================================================================

class TestOpGeoFromCoords:

    def test_lat_lon_a_geometry(self):
        df = pd.DataFrame({
            "id":  [1, 2, 3],
            "lat": [41.9028, 45.4654, 40.8518],
            "lon": [12.4964, 9.1895,  14.2681],
        })
        result = op_geo_from_coords(df, lat_column="lat", lon_column="lon")
        assert "geometry" in result.columns
        assert len(result) == 3

    def test_geometry_e_wkt(self):
        """Geometria prodotta deve essere WKT POINT."""
        df = pd.DataFrame({"lat": [41.9028], "lon": [12.4964]})
        result = op_geo_from_coords(df, lat_column="lat", lon_column="lon")
        geom = result["geometry"].iloc[0]
        geom_str = str(geom)
        assert "POINT" in geom_str.upper()

    def test_output_column_custom(self):
        df = pd.DataFrame({"lat": [41.9028], "lon": [12.4964]})
        result = op_geo_from_coords(df, lat_column="lat", lon_column="lon",
                                     output_column="punto")
        assert "punto" in result.columns
        assert "geometry" not in result.columns

    def test_colonna_lat_inesistente_restituisce_invariato(self):
        df = pd.DataFrame({"lat": [41.9], "lon": [12.4]})
        result = op_geo_from_coords(df, lat_column="COLONNA_XYZ", lon_column="lon")
        assert "geometry" not in result.columns
        assert len(result) == len(df)

    def test_valori_null_gestiti(self):
        """Righe con lat/lon null non devono crashare."""
        df = pd.DataFrame({"lat": [41.9, None, 40.8], "lon": [12.4, 9.1, 14.2]})
        result = op_geo_from_coords(df, lat_column="lat", lon_column="lon")
        assert len(result) == 3
