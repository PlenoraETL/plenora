"""
Golden test sui writer di output (CSV / JSON / XLSX / GeoJSON / GPKG).

Costruisce un DataFrame "torturato" che riproduce i casi reali dove
la sessione di audit aveva trovato bug silenziosi (trailing ``.0`` su
int promossi a float, Decimal con 16 zeri da PG NUMERIC, Unicode/emoji,
virgolette interne in CSV) e congela il comportamento atteso byte per
byte cosi' che future regressioni nei writer si manifestino con un
diff esplicito, non con dati visivamente "sospetti" lato utente.

Coverage:
- ``app.core.files.dataframe_io.tabular_dataframe_to_bytes`` (csv/json/xlsx)
- ``app.core.files.dataframe_io._compact_whole_number_floats`` (Decimal+float)
- ``app.core.files.geospatial_io.geospatial_dataframe_to_bytes`` (geojson)
"""

from __future__ import annotations

import io
import json
from decimal import Decimal

import pandas as pd
import pytest


@pytest.fixture
def tortured_tabular_df() -> pd.DataFrame:
    """DataFrame che esercita TUTTI i casi-edge del writer tabulare.

    - ``id``: int puro
    - ``age``: int promosso a float per NaN (deve uscire ``"34"`` non ``"34.0"``)
    - ``salary``: float misto (interi + decimali reali, ``"52000"`` ma ``"45000.5"``)
    - ``avg_decimal``: Decimal (simula PG NUMERIC, ``"36.5"`` non ``"36.5000000000000000"``)
    - ``name``: stringhe con Unicode + emoji + virgolette interne
    - ``notes``: stringhe con virgole interne (test CSV quoting)
    - ``flag``: boolean
    """
    return pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "age": [34, 41, None, 52],  # pandas -> float64
            "salary": [45000.5, 52000.0, 38500.75, 41200.0],  # float64 misto
            "avg_decimal": [
                Decimal("36.5"),
                Decimal("36.5000000000000000"),
                Decimal("42.7777777777777777"),
                Decimal("100"),
            ],
            "name": [
                "Anna Rossi",
                "Marco Müller",
                "北京 用户",
                "Emoji 🎉 user",
            ],
            "notes": [
                "Cliente VIP, premium",
                'Note con "virgolette" interne',
                "",
                None,
            ],
            "flag": [True, False, True, False],
        }
    )


def test_csv_writer_no_trailing_dot_zero_on_int_promoted_to_float(
    tortured_tabular_df: pd.DataFrame,
):
    """``age`` ha 1 NaN -> pandas promuove a float64 -> writer ingenuo
    scriveva ``34.0``. Dopo il fix scrive ``34`` (vuoto per NaN).
    Regression-guard sul commit 525c4340."""
    from app.core.files.dataframe_io import tabular_dataframe_to_bytes

    raw = tabular_dataframe_to_bytes(tortured_tabular_df, "csv").decode("utf-8")
    rows = [r for r in raw.splitlines() if r]

    # header sanity
    assert rows[0].split(",")[:3] == ["id", "age", "salary"]

    # row 1 (id=1): age=34, salary=45000.5
    cells_r1 = rows[1].split(",")
    assert cells_r1[1] == "34", f"age trailing .0 regression: got {cells_r1[1]!r}"
    assert cells_r1[2] == "45000.5", f"salary decimal lost: got {cells_r1[2]!r}"

    # row 2 (id=2): age=41, salary=52000 (NO .0 — float-intero)
    cells_r2 = rows[2].split(",")
    assert cells_r2[1] == "41"
    assert cells_r2[2] == "52000", (
        f"salary intero promosso a float deve perdere .0: got {cells_r2[2]!r}"
    )

    # row 3 (id=3): age NaN -> stringa vuota
    cells_r3 = rows[3].split(",")
    assert cells_r3[1] == "", f"NaN deve uscire vuoto in CSV: got {cells_r3[1]!r}"
    assert cells_r3[2] == "38500.75"


def test_csv_writer_decimal_from_postgres_numeric_compact(
    tortured_tabular_df: pd.DataFrame,
):
    """psycopg2 mappa PG NUMERIC a ``Decimal``. Senza normalizzazione lo
    writer scrive ``str(Decimal("36.5000000000000000"))`` con 16 zeri.
    Regression-guard sul commit 666fa513 (streaming) +
    ``_compact_whole_number_floats`` (non-streaming)."""
    from app.core.files.dataframe_io import tabular_dataframe_to_bytes

    raw = tabular_dataframe_to_bytes(tortured_tabular_df, "csv").decode("utf-8")
    rows = [r for r in raw.splitlines() if r]
    # Posizione `avg_decimal` nell'header
    header = rows[0].split(",")
    idx = header.index("avg_decimal")

    # Decimal("36.5") -> "36.5"
    assert rows[1].split(",")[idx] == "36.5"
    # Decimal("36.5000000000000000") -> "36.5" (coda zeri compattata)
    assert rows[2].split(",")[idx] == "36.5"
    # Decimal("100") (intero) -> "100" (no .0)
    assert rows[4].split(",")[idx] == "100"


def test_csv_writer_unicode_and_emoji_preserved(tortured_tabular_df: pd.DataFrame):
    from app.core.files.dataframe_io import tabular_dataframe_to_bytes

    raw = tabular_dataframe_to_bytes(tortured_tabular_df, "csv").decode("utf-8")
    assert "Marco Müller" in raw
    assert "北京 用户" in raw
    assert "Emoji 🎉 user" in raw


def test_csv_writer_internal_commas_and_quotes_escaped(
    tortured_tabular_df: pd.DataFrame,
):
    """pandas to_csv deve quotare valori con virgole/virgolette interne."""
    from app.core.files.dataframe_io import tabular_dataframe_to_bytes

    raw = tabular_dataframe_to_bytes(tortured_tabular_df, "csv").decode("utf-8")
    # virgola interna -> wrapping con virgolette
    assert '"Cliente VIP, premium"' in raw
    # virgolette interne -> raddoppiate
    assert '"Note con ""virgolette"" interne"' in raw


def test_json_writer_decimal_and_unicode(tortured_tabular_df: pd.DataFrame):
    """JSON: Decimal -> numero / stringa compatti, Unicode preservato."""
    from app.core.files.dataframe_io import tabular_dataframe_to_bytes

    raw = tabular_dataframe_to_bytes(tortured_tabular_df, "json").decode("utf-8")
    parsed = json.loads(raw)
    assert isinstance(parsed, list)
    assert len(parsed) == 4

    # age=NaN -> None
    assert parsed[2]["age"] is None
    # salary intero post-compaction
    assert parsed[1]["salary"] in (52000, "52000")  # Int64 -> int o str-numeric
    # avg_decimal coerente
    avg_r1 = parsed[0]["avg_decimal"]
    # Compatible sia con str ("36.5") sia con numero (36.5). Mai con 16 zeri.
    assert str(avg_r1) == "36.5"
    # Unicode
    assert parsed[2]["name"] == "北京 用户"
    assert "🎉" in parsed[3]["name"]


def test_xlsx_writer_produces_valid_excel(tortured_tabular_df: pd.DataFrame):
    """XLSX: verifica che il blob sia un ZIP valido + readable con openpyxl.
    Non sanity-checkiamo i valori cell-by-cell (Excel ha la sua gestione
    tipi che pandas rispetta), solo che la pipeline non crasha."""
    from app.core.files.dataframe_io import tabular_dataframe_to_bytes

    raw = tabular_dataframe_to_bytes(tortured_tabular_df, "xlsx")
    assert len(raw) > 0
    # XLSX = ZIP container (signature `PK\x03\x04`)
    assert raw[:4] == b"PK\x03\x04", "XLSX deve essere un ZIP valido"

    # Re-read con pandas
    df_back = pd.read_excel(io.BytesIO(raw))
    assert len(df_back) == 4
    assert "id" in df_back.columns
    assert "name" in df_back.columns
    # Verifica una stringa con caratteri speciali
    assert df_back.iloc[2]["name"] == "北京 用户"


def test_csv_writer_empty_dataframe_does_not_crash():
    """Un DF vuoto deve produrre solo l'header senza errori."""
    from app.core.files.dataframe_io import tabular_dataframe_to_bytes

    df = pd.DataFrame(columns=["a", "b"])
    raw = tabular_dataframe_to_bytes(df, "csv").decode("utf-8")
    assert raw.strip().splitlines() == ["a,b"]


def test_compact_whole_number_floats_preserves_real_decimals():
    """Guard: la compaction NON deve trasformare 3.14 in 3 (loss of data).
    Solo float dove TUTTI i non-NaN sono interi vengono compattati."""
    from app.core.files.dataframe_io import _compact_whole_number_floats

    df = pd.DataFrame(
        {
            "int_with_nan": [1.0, 2.0, None, 4.0],         # compactabile -> Int64
            "real_floats": [1.5, 2.7, 3.14, 4.0],          # NON compactabile (3.14)
            "decimals_int": [
                Decimal("10"), Decimal("20"), Decimal("30"), None,
            ],                                              # tutti interi -> Int64
            "decimals_mixed": [
                Decimal("36.5"), Decimal("42.0"), Decimal("100"), None,
            ],                                              # 36.5 -> float64
        }
    )

    out = _compact_whole_number_floats(df)
    # int_with_nan: tutti interi non-nan -> Int64
    assert str(out["int_with_nan"].dtype) == "Int64"
    # real_floats: contiene 3.14 -> resta float
    assert "float" in str(out["real_floats"].dtype).lower()
    # decimals_int: object con tutti Decimal interi -> Int64
    assert str(out["decimals_int"].dtype) == "Int64"
    # decimals_mixed: contiene 36.5 -> float64 (no data loss)
    assert "float" in str(out["decimals_mixed"].dtype).lower()


def test_geojson_writer_compacts_int_properties():
    """Mirror del fix tabular ma su GeoJSON: le ``properties`` devono
    avere ``"age": 34`` non ``34.0`` quando l'int era promosso a float."""
    geopandas = pytest.importorskip("geopandas")
    shapely = pytest.importorskip("shapely")
    pytest.importorskip("psycopg2")  # geospatial_io tira la chain DB
    from shapely.geometry import Point

    from app.core.files.geospatial_io import geospatial_dataframe_to_bytes

    gdf = geopandas.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "age": [34, 41, None],          # promosso a float per NaN
            "salary": [45000.5, 52000.0, 38500.75],
            "geom": [
                Point(9.19, 45.46),
                Point(12.50, 41.90),
                Point(14.27, 40.85),
            ],
        },
        geometry="geom",
        crs="EPSG:4326",
    )

    raw = geospatial_dataframe_to_bytes(gdf, "geojson", "geom", "EPSG:4326").decode(
        "utf-8"
    )
    parsed = json.loads(raw)
    feats = parsed["features"]
    assert len(feats) == 3

    # row 1: age=34 (no .0)
    age_r1 = feats[0]["properties"]["age"]
    assert age_r1 == 34 or age_r1 == "34"
    # row 2: salary intero deve mantenere precisione (no trailing junk)
    sal_r2 = feats[1]["properties"]["salary"]
    assert sal_r2 == 52000 or sal_r2 == 52000.0
    # row 3: age NaN -> None
    assert feats[2]["properties"]["age"] is None or pd.isna(
        feats[2]["properties"]["age"]
    )
    # Geometry sanity
    assert feats[0]["geometry"]["type"] == "Point"
    assert feats[0]["geometry"]["coordinates"] == [9.19, 45.46]
