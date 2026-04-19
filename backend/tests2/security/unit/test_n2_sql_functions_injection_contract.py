"""Contract test N2 — SQL injection guards in sql_functions.py.

Pre-N2, ~50 metodi `_translate_*` costruivano frammenti SQL via
f-string senza validazione dei params numerici/identifier. Payload
tipo `distance='1); DROP TABLE x;--'` finiva concatenato letterale
nella query emessa verso il DB del cliente.

Post-N2 helpers:
- `_safe_number(v, kind)`: float/int cast con ValueError su non-numerico
- `_safe_identifier(v)`: regex whitelist [A-Za-z_][A-Za-z0-9_]{0,62}
- `_safe_qualified_identifier(v)`: idem + supporto "table.col"
- `_qualify_column`: applica whitelist su path identifier

Scope tests:
1. Helpers rifiutano payload malformati
2. Metodi numerici critici (buffer/simplify/transform/mod/power/left/right/
   substring) rifiutano injection numerici
3. `_qualify_column` rifiuta identifier invalidi (regression guard sul
   path piu' grave: column name user-controlled)
4. Regression: metodi gia' validati (DATE_ADD/DATE_FORMAT) restano tali

NB: questo contract NON converte il modulo a parameter binding (B-scope).
E' defense-in-depth via validation helpers (A-scope).
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# =============================================================================
# 1. Helpers rifiutano payload
# =============================================================================


@pytest.mark.parametrize("payload", [
    "1; DROP TABLE x;",
    "1) OR 1=1 --",
    "'; DELETE FROM orders; --",
    "1,0); SELECT version()--",
    "",
    None,
    "not_a_number",
    "1.5.2",  # malformed float
])
def test_safe_number_rejects_injection_and_garbage(payload):
    from app.db.sql_functions import _safe_number
    with pytest.raises(ValueError):
        _safe_number(payload, "float")


@pytest.mark.parametrize("valid", [
    "1", "1.5", "-3.14", "0", 42, 3.14, "1e3",
])
def test_safe_number_accepts_valid_float(valid):
    from app.db.sql_functions import _safe_number
    result = _safe_number(valid, "float")
    assert isinstance(result, float)


def test_safe_number_int_rejects_non_integer():
    from app.db.sql_functions import _safe_number
    with pytest.raises(ValueError):
        _safe_number("1.5", "int")


@pytest.mark.parametrize("payload", [
    "col); DROP TABLE x;--",
    "col' OR '1'='1",
    "col UNION SELECT password FROM users",
    "col.table.db",  # troppi livelli
    "col with spaces",
    "1col_starts_with_digit",
    "",
    "col;--",
    "col)",
    "col/*comment*/",
    "col\x00",
])
def test_safe_identifier_rejects_injection_payloads(payload):
    from app.db.sql_functions import _safe_identifier
    with pytest.raises(ValueError):
        _safe_identifier(payload)


@pytest.mark.parametrize("valid", [
    "col", "my_col", "COL_123", "_underscore", "geom",
    "a" * 63,  # max length
])
def test_safe_identifier_accepts_valid_names(valid):
    from app.db.sql_functions import _safe_identifier
    assert _safe_identifier(valid) == valid


def test_safe_identifier_rejects_over_max_length():
    from app.db.sql_functions import _safe_identifier
    with pytest.raises(ValueError):
        _safe_identifier("a" * 64)


def test_safe_qualified_identifier_allows_dot():
    from app.db.sql_functions import _safe_qualified_identifier
    assert _safe_qualified_identifier("t.geom") == "t.geom"


def test_safe_qualified_identifier_rejects_multilevel():
    """Massimo 2 parti: schema.table OR table.column. Non 3."""
    from app.db.sql_functions import _safe_qualified_identifier
    with pytest.raises(ValueError):
        _safe_qualified_identifier("a.b.c")


# =============================================================================
# 2. Metodi numerici rifiutano injection
# =============================================================================


def _translator(db="postgres"):
    from app.db.sql_functions import get_translator
    return get_translator(db)


@pytest.mark.parametrize("method,param_name,payload", [
    ("BUFFER", "distance", "100); DROP TABLE x;--"),
    ("SIMPLIFY", "tolerance", "0.1 OR 1=1"),
    ("TRANSFORM", "srid", "4326; DELETE FROM orders--"),
    ("MOD", "divisor", "5); UNION SELECT 1--"),
    ("POWER", "exponent", "2); DROP TABLE y;--"),
    ("LEFT", "length", "10); SELECT version()--"),
    ("RIGHT", "length", "10); TRUNCATE users--"),
])
def test_numeric_params_reject_injection(method, param_name, payload):
    tr = _translator()
    params = {"column": "geom", param_name: payload}
    with pytest.raises(ValueError):
        tr.translate(method, params, table_alias="t")


def test_substring_rejects_injection_on_start_and_length():
    tr = _translator()
    with pytest.raises(ValueError):
        tr.translate("SUBSTRING", {"column": "c", "start": "1); DROP x;--"}, table_alias="t")
    with pytest.raises(ValueError):
        tr.translate(
            "SUBSTRING",
            {"column": "c", "start": 1, "length": "5); DROP x;--"},
            table_alias="t",
        )


def test_numeric_params_accept_valid_inputs():
    """Controllo simmetrico: valori legittimi passano e generano SQL."""
    tr = _translator()
    sql = tr.translate("BUFFER", {"column": "geom", "distance": 100}, table_alias="t")
    assert "ST_Buffer" in sql
    assert "100" in sql

    sql = tr.translate("TRANSFORM", {"column": "geom", "srid": 4326}, table_alias="t")
    assert "4326" in sql


# =============================================================================
# 3. _qualify_column rifiuta identifier invalidi
# =============================================================================


def test_qualify_column_rejects_injection_in_simple_identifier():
    """Il vettore piu' grave: se un canvas passa column='col); DROP...'
    senza parentesi/dot, il whitelist del _qualify_column lo blocca."""
    tr = _translator()
    with pytest.raises(ValueError):
        tr._qualify_column("col_malicious;--", "t")
    with pytest.raises(ValueError):
        tr._qualify_column("col with space", "t")


def test_qualify_column_accepts_plain_identifier():
    tr = _translator()
    assert tr._qualify_column("geom", "t") == "t.geom"


def test_qualify_column_accepts_prequalified():
    tr = _translator()
    assert tr._qualify_column("t.geom", None) == "t.geom"


def test_qualify_column_passthrough_expressions():
    """Il path espressione (contiene parentesi) e' trust-based —
    alimentato solo da altri _translate_* validati. Accettato tal quale."""
    tr = _translator()
    expr = "ST_Transform(ST_Buffer(t.geom, 100), 4326)"
    assert tr._qualify_column(expr, "t") == expr


# =============================================================================
# 4. Regression: metodi gia' validati restano tali
# =============================================================================


def test_date_add_still_rejects_non_numeric_interval():
    tr = _translator()
    with pytest.raises(ValueError):
        tr.translate(
            "DATE_ADD",
            {"column": "d", "interval": "1); DROP x;--", "unit": "day"},
            table_alias="t",
        )


def test_date_add_still_rejects_bad_unit():
    tr = _translator()
    with pytest.raises(ValueError):
        tr.translate(
            "DATE_ADD",
            {"column": "d", "interval": 1, "unit": "century'); DROP--"},
            table_alias="t",
        )


def test_date_format_still_rejects_bad_format():
    tr = _translator()
    with pytest.raises(ValueError):
        tr.translate(
            "DATE_FORMAT",
            {"column": "d", "format": "YYYY'); DROP--"},
            table_alias="t",
        )


# =============================================================================
# 5. N2 residui — follow-up fix (MATH.value, EXTRACT.part)
# =============================================================================


@pytest.mark.parametrize("payload", [
    "0); DROP TABLE x;--",
    "1 OR 1=1",
    "'; DELETE FROM orders; --",
    "1; SELECT version()--",
])
def test_math_rejects_injection_on_value(payload):
    """MATH con `value` (senza column2) deve validare numericamente il
    secondo operando — altrimenti finisce raw in f-string."""
    tr = _translator()
    with pytest.raises(ValueError):
        tr.translate(
            "MATH",
            {"column1": "a", "operator": "+", "value": payload},
            table_alias="t",
        )


@pytest.mark.parametrize("valore", [10, 0.5, "3.14", -2, "1e3"])
def test_math_accepts_numeric_value(valore):
    tr = _translator()
    sql = tr.translate(
        "MATH",
        {"column1": "a", "operator": "+", "value": valore},
        table_alias="t",
    )
    assert "t.a" in sql


def test_math_division_protects_against_injection_on_value():
    """Il ramo divisione e' quello con piu' interpolazioni per-dialetto:
    `secondo_operando` finisce in NULLIF/CASE WHEN. Verifica specifica."""
    tr = _translator()
    with pytest.raises(ValueError):
        tr.translate(
            "MATH",
            {"column1": "a", "operator": "/", "value": "0); DROP TABLE x;--"},
            table_alias="t",
        )


@pytest.mark.parametrize("payload", [
    "year) FROM t); DROP TABLE x;--",
    "year; DELETE FROM orders--",
    "'; DROP TABLE x;--",
    "year/*comment*/",
    "",
])
def test_extract_rejects_injection_on_part(payload):
    """EXTRACT senza whitelist su `part` → interpolato in
    EXTRACT({part} FROM col) per Postgres/MSSQL/MySQL/Oracle."""
    tr = _translator()
    with pytest.raises(ValueError):
        tr.translate(
            "EXTRACT",
            {"column": "d", "part": payload},
            table_alias="t",
        )


@pytest.mark.parametrize("part", [
    "year", "month", "day", "hour", "minute", "second",
    "YEAR", "Month",
])
def test_extract_accepts_whitelisted_parts(part):
    tr = _translator()
    sql = tr.translate("EXTRACT", {"column": "d", "part": part}, table_alias="t")
    assert "t.d" in sql


def test_extract_rejects_unknown_part():
    """Una parte fuori dalla whitelist (es. 'century') deve essere
    rifiutata anche se grammaticalmente innocua."""
    tr = _translator()
    with pytest.raises(ValueError):
        tr.translate("EXTRACT", {"column": "d", "part": "century"}, table_alias="t")


# =============================================================================
# 6. N2 residui — defense-in-depth su _qualify_column (expression path)
# =============================================================================


@pytest.mark.parametrize("payload", [
    "ST_Buffer(col, 100); DROP TABLE x;--",   # semicolon
    "col) OR 1=1--",                          # line comment
    "col/*evil*/",                            # block comment open
    "evil*/col",                              # block comment close
    "foo(); -- trailing",                     # semi + comment
    "ST_Buffer(col, 100)\x00hidden",         # null byte
])
def test_qualify_column_expression_path_blocks_structural_markers(payload):
    """Il path espressione (contiene parentesi) ora rifiuta marker
    strutturali di injection (;, --, /*, */, NUL). Questi non compaiono
    mai negli output legittimi dei _translate_*, quindi bloccarli e'
    defense-in-depth senza falsi positivi."""
    tr = _translator()
    with pytest.raises(ValueError):
        tr._qualify_column(payload, "t")


@pytest.mark.parametrize("expr", [
    "ST_Transform(ST_Buffer(t.geom, 100), 4326)",
    "COALESCE(t.a, t.b, 'fallback')",
    "NULLIF(col, 'text with spaces')",
    "CONCAT(t.a, ' - ', t.b)",
    "EXTRACT(year FROM t.d)",
    "SUBSTRING(t.s, 1, 10)",
    "IF(t.c = 0, NULL, t.a / t.c)",
    "(CASE WHEN t.c = 0 THEN NULL ELSE t.a / t.c END)",
])
def test_qualify_column_expression_path_accepts_legitimate_outputs(expr):
    """Le espressioni tipicamente prodotte dai _translate_* passano.
    Se questo test inizia a fallire, il blocklist e' troppo stretto."""
    tr = _translator()
    assert tr._qualify_column(expr, "t") == expr


# =============================================================================
# 7. N2 residui — operator non validato in CASE_WHEN / IIF
# =============================================================================


@pytest.mark.parametrize("method,extra", [
    ("CASE_WHEN", {"compare_value": "x", "then_value": "y", "else_value": "z"}),
    ("IIF", {"compare_value": "x", "true_value": "y", "false_value": "z"}),
])
@pytest.mark.parametrize("payload", [
    "= '1' OR 1=1 --",
    "); DROP TABLE x;--",
    "= 'x' UNION SELECT password FROM users--",
    "<> '' OR 1=1 /*",
    "= 'x'; DELETE FROM orders--",
])
def test_case_when_and_iif_reject_operator_injection(method, extra, payload):
    """`operator` finiva raw in f-string (CASE WHEN / IIF).
    Whitelist su operatori di confronto obbligatoria."""
    tr = _translator()
    params = {"column": "a", "operator": payload, **extra}
    with pytest.raises(ValueError):
        tr.translate(method, params, table_alias="t")


@pytest.mark.parametrize("method,extra", [
    ("CASE_WHEN", {"compare_value": "x", "then_value": "y", "else_value": "z"}),
    ("IIF", {"compare_value": "x", "true_value": "y", "false_value": "z"}),
])
@pytest.mark.parametrize("op", ["=", "!=", "<>", "<", ">", "<=", ">="])
def test_case_when_and_iif_accept_whitelisted_operators(method, extra, op):
    tr = _translator()
    params = {"column": "a", "operator": op, **extra}
    sql = tr.translate(method, params, table_alias="t")
    assert f"t.a {op} " in sql


@pytest.mark.parametrize("method,extra", [
    ("CASE_WHEN", {"compare_value": "x", "then_value": "y", "else_value": "z"}),
    ("IIF", {"compare_value": "x", "true_value": "y", "false_value": "z"}),
])
def test_case_when_and_iif_default_operator_is_equal(method, extra):
    """Omettere operator → default '=' (retro-compatibilita')."""
    tr = _translator()
    params = {"column": "a", **extra}
    sql = tr.translate(method, params, table_alias="t")
    assert "t.a = " in sql
