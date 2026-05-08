import inspect

import pytest

from app.core.estrai import executor
from app.core.estrai.sql_errors import is_user_error_message, translate_sql_error


@pytest.mark.parametrize(
    ("technical", "friendly"),
    [
        ("Invalid column name 'foo'", "Colonna non trovata nel database"),
        ("Invalid object name 'table_x'", "Tabella non trovata nel database"),
        ("Ambiguous column name 'id'", "Nome colonna ambiguo — presente in più tabelle"),
        ("Conversion failed when converting", "Errore di conversione del tipo di dato"),
        ("division by zero", "Divisione per zero nella query"),
        ("Cannot insert duplicate key row", "Violazione chiave univoca — valore duplicato"),
        ("Timeout expired", "Timeout query superato"),
        ("Login failed for user", "Errore di autenticazione al database"),
        ("Cannot open database requested by login", "Impossibile aprire il database"),
        ("String or binary data would be truncated", "Valore troppo lungo per la colonna"),
        ("column name or number of supplied values does not match", "Numero di colonne non corrisponde"),
        (
            "operator does not exist: integer = text",
            "Tipi incompatibili nel JOIN — le colonne collegate devono avere lo stesso tipo di dato",
        ),
        (
            "undefinedfunction: function trim(bigint) does not exist",
            "Funzione SQL non applicabile al tipo della colonna — controlla le opzioni di JOIN o i campi calcolati",
        ),
        (
            "undefinedcolumn: column foo does not exist",
            "Colonna non trovata nel database — controlla i campi selezionati o gli alias",
        ),
        (
            "invalid input syntax for type integer: abc",
            "Valore del filtro incompatibile con il tipo della colonna — controlla i filtri applicati",
        ),
        (
            "invalid input syntax: abc",
            "Valore non valido per il tipo di dato atteso — controlla i filtri applicati",
        ),
        ("syntax error at or near FROM", "Errore di sintassi SQL"),
        ("relation public.x does not exist", "Oggetto non trovato nel database"),
        ("permission denied for relation x", "Permesso negato sul database"),
    ],
)
def test_translate_sql_error_preserves_public_messages(technical, friendly):
    translated = translate_sql_error(technical)

    assert translated == f"{friendly}. Dettaglio tecnico: {technical}"


def test_translate_sql_error_preserves_unknown_messages():
    message = "driver disconnected unexpectedly"

    assert translate_sql_error(message) == message


def test_translate_sql_error_truncates_technical_detail_to_existing_limit():
    message = "syntax error " + ("x" * 400)

    translated = translate_sql_error(message)

    assert translated.endswith(message[:300])
    assert len(translated) < len("Errore di sintassi SQL. Dettaglio tecnico: " + message)


@pytest.mark.parametrize(
    "message",
    [
        "invalid input syntax for type integer",
        "undefinedfunction",
        "undefinedcolumn",
        "no function matches",
        "operator does not exist",
        "ambiguous column name",
        "column name or number of supplied values",
        "conversion failed",
        "division by zero",
        "string or binary data would be truncated",
        "syntax error",
    ],
)
def test_is_user_error_message_matches_existing_patterns(message):
    assert is_user_error_message(message)


def test_executor_delegates_sql_error_translation_to_domain_module():
    source = inspect.getsource(executor)

    assert "from app.core.estrai.sql_errors import" in source
    assert "SQL_ERROR_TRANSLATIONS" not in source
    assert "USER_ERROR_PATTERNS" not in source
