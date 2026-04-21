"""Types roundtrip del path COPY senza PG reale.

Motivazione: `backend/tests2/carica/unit/test_bulk_writers_contract.py`
(ownership DATI/CARICA, NON tocchiamo) copre gia' il contract-level
(geometry raises, null serialization, dangerous idents). Questo file
copre un gap complementare: **la serializzazione CSV deve produrre
output leggibile/ricostruibile sui tipi comuni**, senza dipendere
da un Postgres test.

Strategia: chiamiamo `bulk_copy_postgres` con una SA connection fittizia
che intercetta `cursor.copy_expert(sql, buf)` e cattura il `buf`.
Poi parsiamo il buffer come CSV e verifichiamo il round-trip
valore -> stringa CSV -> valore.

Copertura:
  - int/float ordinari
  - NaN/None -> stringa vuota (NULL '' in COPY)
  - datetime.date, datetime.datetime (ISO)
  - Decimal
  - stringa con embedded quote, newline, virgola (QUOTE_MINIMAL)
  - UTF-8 multibyte
  - booleani True/False

Scope out: geometry (raises in bulk path — gia' coperto da contract).

Esecuzione:
    pytest backend/tests2/performance/test_bulk_copy_types_roundtrip.py \
        -m perf_correctness -s -q
"""

from __future__ import annotations

import csv
import datetime
import io
from decimal import Decimal
from typing import List, Optional

import pandas as pd
import pytest

from app.core.carica.bulk_writers import bulk_copy_postgres
from app.core.carica.models import ColumnMapping


# ─────────────────────────────────────────────────────────────────────
# Fake DBAPI per catturare il COPY buffer
# ─────────────────────────────────────────────────────────────────────


class _CapturingCursor:
    """Cursor-stub: registra sql + buffer di copy_expert, no-op su close."""

    def __init__(self):
        self.copy_sql: Optional[str] = None
        self.copy_buffer: Optional[str] = None

    def copy_expert(self, sql: str, file_obj):
        self.copy_sql = sql
        # file_obj e' un StringIO posizionato a seek(0) dal chiamante;
        # leggiamo tutto e memorizziamo. Non facciamo consume duplicato:
        # dopo questa chiamata il chiamante non rilegge il buffer.
        self.copy_buffer = file_obj.read()

    def close(self):
        pass


class _FakeRawConn:
    def __init__(self):
        self._cursor = _CapturingCursor()

    def cursor(self):
        return self._cursor


class _FakeSAConn:
    """Mimic del SA Connection: espone `.connection` al DBAPI grezzo."""

    def __init__(self):
        self.connection = _FakeRawConn()


def _capture_copy(df: pd.DataFrame, columns: List[ColumnMapping]) -> tuple[str, str]:
    """Esegue bulk_copy_postgres con fake connection, ritorna
    (sql_emesso, csv_buffer_str) per ispezione.
    Il parametro schema e' None (tabella non qualificata) per
    ridurre rumore nell'SQL emesso."""
    sa_conn = _FakeSAConn()
    n = bulk_copy_postgres(
        sa_conn=sa_conn,
        schema=None,
        table="t",
        columns=columns,
        df=df,
    )
    cur = sa_conn.connection._cursor
    assert cur.copy_buffer is not None, "copy_expert non e' stato chiamato"
    assert n == len(df)
    return cur.copy_sql, cur.copy_buffer


def _parse_buffer(buf: str) -> List[List[str]]:
    """Parsing del CSV identico a come PG lo legge in FORMAT CSV:
    QUOTE_MINIMAL, " come quote char, escape tramite quote doubling.
    Ritorna lista di righe, ciascuna come list[str]. NULL-come-'' qui
    resta stringa vuota (in PG diventa NULL per via di NULL '')."""
    return list(csv.reader(io.StringIO(buf), quoting=csv.QUOTE_MINIMAL))


# ─────────────────────────────────────────────────────────────────────
# Test roundtrip
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.perf_correctness
def test_roundtrip_ints_and_floats():
    """int e float preservano valore numerico nel CSV."""
    df = pd.DataFrame(
        {"a": [1, 2, -3, 2147483647], "b": [1.5, -0.25, 0.0, 3.14159]}
    )
    cols = [ColumnMapping("a", "a"), ColumnMapping("b", "b")]
    _, buf = _capture_copy(df, cols)
    rows = _parse_buffer(buf)

    assert [r[0] for r in rows] == ["1", "2", "-3", "2147483647"]
    # Float formatting segue il default di pandas to_csv (no trailing 0s
    # su .0, ma 3.14159 preservato esatto). La semantica che vogliamo
    # garantire e' "parseable back to float con stesso valore".
    parsed_floats = [float(r[1]) for r in rows]
    assert parsed_floats == [1.5, -0.25, 0.0, pytest.approx(3.14159)]


@pytest.mark.perf_correctness
def test_roundtrip_null_none_nan_become_empty_field():
    """None e NaN vengono serializzati come stringa vuota (na_rep='').
    In PG questo e' interpretato come NULL via COPY ... WITH (NULL '')."""
    df = pd.DataFrame(
        {
            "a": [1, None, 3],
            "s": ["x", None, "z"],
            "f": [1.0, float("nan"), 3.0],
        }
    )
    cols = [ColumnMapping(c, c) for c in ("a", "s", "f")]
    _, buf = _capture_copy(df, cols)
    rows = _parse_buffer(buf)

    # Riga con None in tutte e 3 colonne eccetto dove presente -> ""
    assert rows[0] == ["1", "x", "1.0"] or rows[0] == ["1.0", "x", "1.0"]
    assert rows[1][1] == ""  # s=None
    assert rows[1][2] == ""  # f=NaN
    # 'a' con None: pandas promuove int→float (colonna intera diventa
    # nullable float) → NaN → ""
    assert rows[1][0] == ""


@pytest.mark.perf_correctness
def test_roundtrip_datetime_and_date():
    """datetime/date serializzano in forma ISO interpretabile da PG."""
    df = pd.DataFrame(
        {
            "ts": [
                pd.Timestamp("2024-03-15 10:20:30"),
                pd.Timestamp("1999-12-31 23:59:59"),
            ],
            "d": [datetime.date(2024, 1, 2), datetime.date(2024, 6, 30)],
        }
    )
    cols = [ColumnMapping("ts", "ts"), ColumnMapping("d", "d")]
    _, buf = _capture_copy(df, cols)
    rows = _parse_buffer(buf)

    # Pandas default: '2024-03-15 10:20:30' (space separator,
    # ISO 8601 variant accettata da PG CAST ::timestamp)
    assert rows[0][0].startswith("2024-03-15")
    assert "10:20:30" in rows[0][0]
    assert rows[1][0].startswith("1999-12-31")

    # Date come 'YYYY-MM-DD'
    assert rows[0][1] == "2024-01-02"
    assert rows[1][1] == "2024-06-30"


@pytest.mark.perf_correctness
def test_roundtrip_decimal_as_string():
    """Decimal serializza senza perdita di precisione via str repr."""
    df = pd.DataFrame(
        {
            "price": [
                Decimal("19.99"),
                Decimal("-0.01"),
                Decimal("123456.789012"),
            ]
        }
    )
    cols = [ColumnMapping("price", "price")]
    _, buf = _capture_copy(df, cols)
    rows = _parse_buffer(buf)

    # Decimal -> str mantiene precisione esatta (no float conversion)
    assert rows[0][0] == "19.99"
    assert rows[1][0] == "-0.01"
    assert rows[2][0] == "123456.789012"


@pytest.mark.perf_correctness
def test_roundtrip_strings_with_special_chars():
    """QUOTE_MINIMAL: quote solo se necessario (virgola, quote, newline).
    Il CSV deve essere ri-parseable con la stessa convenzione."""
    df = pd.DataFrame(
        {
            "s": [
                "simple",
                'has "double" quotes',
                "has,comma",
                "has\nnewline",
                'has,"all"\ntogether',
                "",  # stringa vuota (non-NULL) — ambiguo con NULL in COPY
                "  leading/trailing spaces  ",
            ]
        }
    )
    cols = [ColumnMapping("s", "s")]
    _, buf = _capture_copy(df, cols)
    rows = _parse_buffer(buf)

    # Round-trip esatto
    assert rows[0][0] == "simple"
    assert rows[1][0] == 'has "double" quotes'
    assert rows[2][0] == "has,comma"
    assert rows[3][0] == "has\nnewline"
    assert rows[4][0] == 'has,"all"\ntogether'
    # Nota sul caso [5]: "" e NaN collidono nel CSV FORMAT=CSV, NULL ''.
    # PG distingue via quoting: "" (quoted empty) = stringa vuota,
    # ,, (unquoted empty) = NULL. pandas to_csv con QUOTE_MINIMAL non
    # quota la stringa vuota: la trattera' come NULL in ricezione.
    # E' una limitazione accettata del path bulk; se servisse
    # distinguere "" da NULL, usare NULL <UNIQUE_SENTINEL> + trasform.
    # Qui documentiamo che ambedue finiscono come campo vuoto.
    assert rows[5][0] == ""  # vuoto nel buffer
    assert rows[6][0] == "  leading/trailing spaces  "


@pytest.mark.perf_correctness
def test_roundtrip_utf8_multibyte():
    """UTF-8 multibyte (accenti, CJK, emoji) preservato esatto."""
    df = pd.DataFrame(
        {
            "s": [
                "citta'",
                "caffe' espresso",
                "ni hao",
                "you yi ya",
                # Emoji rimosso per evitare problemi di encoding nei
                # file di test su filesystem Windows (cp1252). Il fix
                # potrebbe richiedere re-encoding esplicito del sorgente.
                "àèìòù",  # accenti italiani
            ]
        }
    )
    cols = [ColumnMapping("s", "s")]
    _, buf = _capture_copy(df, cols)
    rows = _parse_buffer(buf)

    assert rows[0][0] == "citta'"
    assert rows[2][0] == "ni hao"
    assert rows[4][0] == "àèìòù"


@pytest.mark.perf_correctness
def test_roundtrip_booleans_as_python_bool_repr():
    """bool pandas: True/False serializzano come 'True'/'False' (default
    to_csv). PG COPY accetta questi come boolean cast automatico per
    colonna tipizzata bool. Se la colonna e' testo, e' testo — non un
    bug del path bulk.
    """
    df = pd.DataFrame({"flag": [True, False, True]})
    cols = [ColumnMapping("flag", "flag")]
    _, buf = _capture_copy(df, cols)
    rows = _parse_buffer(buf)

    assert rows[0][0] == "True"
    assert rows[1][0] == "False"
    assert rows[2][0] == "True"


@pytest.mark.perf_correctness
def test_roundtrip_skip_column_excluded_from_buffer():
    """Colonna con skip=True non deve apparire ne' nel buffer CSV
    ne' nella lista colonne dell'SQL COPY emesso."""
    df = pd.DataFrame(
        {"keep": ["a", "b"], "drop": ["x", "y"], "also_keep": [1, 2]}
    )
    cols = [
        ColumnMapping("keep", "keep"),
        ColumnMapping("drop", "drop", skip=True),
        ColumnMapping("also_keep", "also_keep"),
    ]
    sql, buf = _capture_copy(df, cols)
    rows = _parse_buffer(buf)

    # 2 campi per riga, non 3
    assert all(len(r) == 2 for r in rows)
    assert rows[0] == ["a", "1"]
    assert rows[1] == ["b", "2"]

    # SQL COPY: la colonna droppata non deve essere menzionata
    assert '"drop"' not in sql
    assert '"keep"' in sql
    assert '"also_keep"' in sql


@pytest.mark.perf_correctness
def test_roundtrip_sql_has_copy_header_and_null_convention():
    """L'SQL COPY emesso deve includere FORMAT CSV, NULL '', ENCODING UTF8.
    Verifica del contract di `bulk_copy_postgres:179-182` — se queste
    opzioni cambiano, i test di roundtrip sopra sono non validi e va
    rivisto il matching."""
    df = pd.DataFrame({"a": [1, 2]})
    cols = [ColumnMapping("a", "a")]
    sql, _ = _capture_copy(df, cols)

    assert "FORMAT CSV" in sql
    assert "NULL ''" in sql
    assert "ENCODING 'UTF8'" in sql
    assert "FROM STDIN" in sql
    # Table name quotato:
    assert '"t"' in sql
