"""User-facing SQL error translation for EstrAi."""

SQL_ERROR_TRANSLATIONS = (
    ("Invalid column name", "Colonna non trovata nel database"),
    ("Invalid object name", "Tabella non trovata nel database"),
    ("Ambiguous column name", "Nome colonna ambiguo — presente in più tabelle"),
    ("Conversion failed", "Errore di conversione del tipo di dato"),
    ("division by zero", "Divisione per zero nella query"),
    ("Cannot insert duplicate", "Violazione chiave univoca — valore duplicato"),
    ("Timeout expired", "Timeout query superato"),
    ("Login failed", "Errore di autenticazione al database"),
    ("Cannot open database", "Impossibile aprire il database"),
    ("String or binary data would be truncated", "Valore troppo lungo per la colonna"),
    ("column name or number of supplied values", "Numero di colonne non corrisponde"),
    ("operator does not exist", "Tipi incompatibili nel JOIN — le colonne collegate devono avere lo stesso tipo di dato"),
    ("undefinedfunction", "Funzione SQL non applicabile al tipo della colonna — controlla le opzioni di JOIN o i campi calcolati"),
    ("no function matches", "Funzione SQL non applicabile al tipo della colonna — controlla le opzioni di JOIN o i campi calcolati"),
    ("undefinedcolumn", "Colonna non trovata nel database — controlla i campi selezionati o gli alias"),
    ("invalid input syntax for type", "Valore del filtro incompatibile con il tipo della colonna — controlla i filtri applicati"),
    ("invalid input syntax", "Valore non valido per il tipo di dato atteso — controlla i filtri applicati"),
    ("syntax error", "Errore di sintassi SQL"),
    ("does not exist", "Oggetto non trovato nel database"),
    ("permission denied", "Permesso negato sul database"),
)

USER_ERROR_PATTERNS = (
    "invalid input syntax",
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
)


def translate_sql_error(message: str) -> str:
    """Translate technical SQL errors into EstrAi user-facing messages."""
    msg_lower = message.lower()
    for pattern, friendly in SQL_ERROR_TRANSLATIONS:
        if pattern.lower() in msg_lower:
            return f"{friendly}. Dettaglio tecnico: {message[:300]}"
    return message


def is_user_error_message(message: str) -> bool:
    """Return True when the SQL error is caused by query/user configuration."""
    msg_lower = message.lower()
    return any(pattern in msg_lower for pattern in USER_ERROR_PATTERNS)
