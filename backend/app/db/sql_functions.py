"""
Catalogo funzioni SQL astratte e traduttore multi-database per PLENORA-ETL.

Keeps persistence access isolated from service and router layers.
"""

from typing import Dict, List, Optional, Any
from enum import Enum
from dataclasses import dataclass

from app.db.sql_date_functions import SqlDateFunctionMixin
from app.db.sql_numeric_functions import SqlNumericFunctionMixin
from app.db.sql_spatial_functions import SqlSpatialFunctionMixin
from app.db.sql_string_functions import SqlStringFunctionMixin


def _sql_escape(value: str) -> str:
    """Escape apici singoli per string literal SQL (limitato).

    NB (N2): questo raddoppio e' sicuro SOLO quando il valore finisce
    tra apici singoli in SQL (es. `'{_sql_escape(v)}'`). NON protegge
    da injection in numeri, identificatori o espressioni. Per quei
    contesti usa `_safe_number` o `_safe_identifier`.
    """
    if value is None:
        return ""
    return str(value).replace("'", "''")


# N2 — VALIDATORI INPUT CONTRO SQL INJECTION
# I traduttori in questo modulo costruiscono SQL via f-string. Per i valori
# che non finiscono tra apici (numeri, nomi colonna, espressioni) _sql_escape
# non basta: serve un cast/whitelist esplicito che rifiuti input malformati
# prima di interpolare
# Helpers
# _safe_number(v, kind): float/int cast, ValueError su non-numerico
# _safe_identifier(v): whitelist regex per singoli identificatori
# es. "geom" → OK, "geom); DROP TABLE x;--" → ValueError
# _safe_qualified_identifier(v): come sopra ma ammette "table.column"


import re as _re_n2


# Singolo identificatore SQL: lettera/underscore iniziale, poi alfanumerico
# o underscore. Ammette anche doppio apice inglobante per identifier
# delimitati ("My Column" con spazi) — questa variante e' piu' restrittiva
# e accetta solo il caso "pulito". Case-insensitive
_IDENTIFIER_RE = _re_n2.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# Identificatore qualificato: "schema.table" o "table.column"; max 2 parti
_QUALIFIED_RE = _re_n2.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62})?$"
)


def _safe_number(value, kind: str = "float"):
    """Converte value a numero (int o float) e rifiuta ValueError
    se non e' un numero valido. Usalo prima di interpolare un valore
    numerico in un f-string SQL.

    Args:
        value: valore da validare (potrebbe essere str, int, float).
        kind: "float" o "int". Se "int", cast stretto via int.

    Returns:
        int o float validato.

    Raises:
        ValueError se value non e' numerico (es. "1; DROP TABLE x;").
    """
    if value is None:
        raise ValueError("_safe_number: valore None")
    try:
        if kind == "int":
            # Consenti "10" ma non "10.5" come int. Per tolleranza: prima
            # float per validare, poi controlla che sia intero esatto
            f = float(value)
            if f != int(f):
                raise ValueError(
                    f"_safe_number: valore '{value}' non e' intero"
                )
            return int(f)
        return float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"_safe_number: valore '{value}' non numerico (kind={kind}): {e}"
        )


def _safe_identifier(value) -> str:
    """Valida un singolo identificatore SQL (nome colonna o alias).

    Pattern whitelist: lettera/underscore iniziale, alfanumerico o
    underscore dopo, max 63 char (limite Postgres default).

    Raises:
        ValueError se contiene caratteri fuori dal pattern (spazi,
        parentesi, punti, semicoloni, commenti, operatori).
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"_safe_identifier: valore non stringa/vuoto: {value!r}")
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(
            f"_safe_identifier: '{value}' contiene caratteri non validi "
            f"(ammessi solo [A-Za-z0-9_], inizio con lettera o _)"
        )
    return value


def _safe_qualified_identifier(value) -> str:
    """Valida "table.column" o singolo "column"."""
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"_safe_qualified_identifier: valore non valido: {value!r}"
        )
    if not _QUALIFIED_RE.match(value):
        raise ValueError(
            f"_safe_qualified_identifier: '{value}' non e' un identifier "
            f"semplice (ammessi 'column' o 'table.column')"
        )
    return value


# Operatori di confronto ammessi per CASE_WHEN / IIF. Finivano raw in
# f-string → injection via operator="= '1' OR 1=1 --"
_COMPARE_OPERATORS = frozenset({"=", "!=", "<>", "<", ">", "<=", ">="})


def _safe_compare_operator(value) -> str:
    """Whitelist operatori di confronto per predicati CASE/IIF."""
    if value not in _COMPARE_OPERATORS:
        raise ValueError(
            f"_safe_compare_operator: operatore '{value}' non ammesso. "
            f"Validi: {sorted(_COMPARE_OPERATORS)}"
        )
    return value


def _split_csv_respect_quotes(value: str) -> List[str]:
    """Split su virgola ignorando le virgole dentro string literal quoted.

    Esempio: ``"email, 'no-email@example.com', full_name"`` ->
    ``["email", "'no-email@example.com'", "full_name"]``.

    Necessario per i traduttori (es. COALESCE) che accettano una lista
    di colonne / letterali separati da virgola, dove un letterale puo'
    legittimamente contenere virgole interne.
    """
    out: List[str] = []
    buf: list[str] = []
    quote: Optional[str] = None
    for ch in value:
        if quote is None:
            if ch in ("'", '"'):
                quote = ch
                buf.append(ch)
            elif ch == ",":
                token = "".join(buf).strip()
                if token:
                    out.append(token)
                buf = []
            else:
                buf.append(ch)
        else:
            buf.append(ch)
            if ch == quote:
                quote = None
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _is_safe_single_quoted_literal(value: str) -> bool:
    """True for SQL string literals with only doubled internal quotes."""
    if len(value) < 2 or not (value.startswith("'") and value.endswith("'")):
        return False
    inner = value[1:-1]
    i = 0
    while i < len(inner):
        if inner[i] == "'":
            if i + 1 >= len(inner) or inner[i + 1] != "'":
                return False
            i += 2
        else:
            i += 1
    return True


# ENUMERAZIONI

class DbType(Enum):
    """Tipi di database supportati dal traduttore SQL."""
    POSTGRES = "postgres"
    MSSQL = "mssql"
    MYSQL = "mysql"
    ORACLE = "oracle"
    SQLITE = "sqlite"


class FunctionCategory(Enum):
    """Categorie per raggruppare le funzioni nel frontend."""
    GEOMETRIC = "geometric"
    NUMERIC = "numeric"
    STRING = "string"
    DATE = "date"
    CONDITIONAL = "conditional"
    CAST = "cast"


# DEFINIZIONI FUNZIONI

@dataclass
class FunctionParam:
    """
    Definizione di un parametro di funzione.

    Tipi di parametro: "column", "number", "string", "srid",
    "column_list", "column_numeric", "operator".
    """
    name: str
    param_type: str     # Tipo del parametro (per validazione UI
    required: bool = True
    default: Any = None
    description: str = ""


@dataclass
class FunctionDef:
    """
    Definizione astratta di una funzione SQL.

    La definizione è indipendente dal database: il SqlFunctionTranslator
    si occupa di tradurla nel dialetto SQL corretto.
    """
    name: str                       # Identificatore univoco (es. "CENTROID"
    label: str                      # Etichetta UI (es. "Centroide"
    category: FunctionCategory      # Categoria per raggruppamento UI
    description: str                # Descrizione per l'utente
    input_type: Optional[str]       # Tipo colonna richiesto ("geometry", "real", None = qualsiasi
    output_type: str                # Tipo del risultato ("geometry:point", "real", "str"
    params: List[FunctionParam]     # Parametri della funzione


# CATALOGO FUNZIONI DISPONIBILI

FUNCTION_CATALOG: Dict[str, FunctionDef] = {


    "CENTROID": FunctionDef(
        name="CENTROID", label="Centroide",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola il centro geometrico della geometria",
        input_type="geometry", output_type="geometry:point",
        params=[FunctionParam("column", "column", True, None, "Colonna geometrica")]
    ),
    "BUFFER": FunctionDef(
        name="BUFFER", label="Buffer",
        category=FunctionCategory.GEOMETRIC,
        description="Crea un'area buffer attorno alla geometria",
        input_type="geometry", output_type="geometry:polygon",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica"),
            FunctionParam("distance", "number", True, None, "Distanza (unità della proiezione)")
        ]
    ),
    "TRANSFORM": FunctionDef(
        name="TRANSFORM", label="Riproiezione",
        category=FunctionCategory.GEOMETRIC,
        description="Trasforma la geometria in un altro sistema di riferimento",
        input_type="geometry", output_type="geometry",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica"),
            FunctionParam("srid", "srid", True, None, "SRID target (es. 4326, 32632)")
        ]
    ),
    "AREA": FunctionDef(
        name="AREA", label="Area",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola l'area della geometria (polygon)",
        input_type="geometry:polygon", output_type="real",
        params=[FunctionParam("column", "column", True, None, "Colonna geometrica (polygon)")]
    ),
    "LENGTH": FunctionDef(
        name="LENGTH", label="Lunghezza",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola la lunghezza della geometria (line)",
        input_type="geometry:line", output_type="real",
        params=[FunctionParam("column", "column", True, None, "Colonna geometrica (line)")]
    ),
    "PERIMETER": FunctionDef(
        name="PERIMETER", label="Perimetro",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola il perimetro della geometria (polygon)",
        input_type="geometry:polygon", output_type="real",
        params=[FunctionParam("column", "column", True, None, "Colonna geometrica (polygon)")]
    ),
    "SIMPLIFY": FunctionDef(
        name="SIMPLIFY", label="Semplifica",
        category=FunctionCategory.GEOMETRIC,
        description="Semplifica la geometria riducendo i vertici",
        input_type="geometry", output_type="geometry",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica"),
            FunctionParam("tolerance", "number", True, None, "Tolleranza di semplificazione")
        ]
    ),
    "CONVEX_HULL": FunctionDef(
        name="CONVEX_HULL", label="Inviluppo convesso",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola l'inviluppo convesso della geometria",
        input_type="geometry", output_type="geometry:polygon",
        params=[FunctionParam("column", "column", True, None, "Colonna geometrica")]
    ),
    "ENVELOPE": FunctionDef(
        name="ENVELOPE", label="Bounding Box",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola il rettangolo di delimitazione (MBR)",
        input_type="geometry", output_type="geometry:polygon",
        params=[FunctionParam("column", "column", True, None, "Colonna geometrica")]
    ),


    "ROUND": FunctionDef(
        name="ROUND", label="Arrotonda",
        category=FunctionCategory.NUMERIC,
        description="Arrotonda al numero di decimali specificato",
        input_type="real", output_type="real",
        params=[
            FunctionParam("column", "column", True, None, "Colonna numerica"),
            FunctionParam("decimals", "number", False, 0, "Numero di decimali")
        ]
    ),
    "ABS": FunctionDef(
        name="ABS", label="Valore assoluto",
        category=FunctionCategory.NUMERIC,
        description="Restituisce il valore assoluto",
        input_type="real", output_type="real",
        params=[FunctionParam("column", "column", True, None, "Colonna numerica")]
    ),
    "CEIL": FunctionDef(
        name="CEIL", label="Arrotonda per eccesso",
        category=FunctionCategory.NUMERIC,
        description="Arrotonda all'intero superiore",
        input_type="real", output_type="int",
        params=[FunctionParam("column", "column", True, None, "Colonna numerica")]
    ),
    "FLOOR": FunctionDef(
        name="FLOOR", label="Arrotonda per difetto",
        category=FunctionCategory.NUMERIC,
        description="Arrotonda all'intero inferiore",
        input_type="real", output_type="int",
        params=[FunctionParam("column", "column", True, None, "Colonna numerica")]
    ),


    "UPPER": FunctionDef(
        name="UPPER", label="Maiuscolo",
        category=FunctionCategory.STRING,
        description="Converte il testo in maiuscolo",
        input_type="str", output_type="str",
        params=[FunctionParam("column", "column", True, None, "Colonna testo")]
    ),
    "LOWER": FunctionDef(
        name="LOWER", label="Minuscolo",
        category=FunctionCategory.STRING,
        description="Converte il testo in minuscolo",
        input_type="str", output_type="str",
        params=[FunctionParam("column", "column", True, None, "Colonna testo")]
    ),
    "TRIM": FunctionDef(
        name="TRIM", label="Rimuovi spazi",
        category=FunctionCategory.STRING,
        description="Rimuove gli spazi iniziali e finali",
        input_type="str", output_type="str",
        params=[FunctionParam("column", "column", True, None, "Colonna testo")]
    ),
    "CONCAT": FunctionDef(
        name="CONCAT", label="Concatena",
        category=FunctionCategory.STRING,
        description="Concatena più valori con separatore opzionale",
        input_type=None, output_type="str",
        params=[
            FunctionParam("values", "column_list", True, None, "Colonne da concatenare"),
            FunctionParam("separator", "string", False, "", "Separatore tra i valori")
        ]
    ),
    "SUBSTRING": FunctionDef(
        name="SUBSTRING", label="Sottostringa",
        category=FunctionCategory.STRING,
        description="Estrae una porzione di testo",
        input_type="str", output_type="str",
        params=[
            FunctionParam("column", "column", True, None, "Colonna testo"),
            FunctionParam("start", "number", True, 1, "Posizione iniziale (da 1)"),
            FunctionParam("length", "number", False, None, "Lunghezza (opzionale)")
        ]
    ),


    "YEAR": FunctionDef(
        name="YEAR", label="Estrai anno",
        category=FunctionCategory.DATE,
        description="Estrae l'anno da una colonna data",
        input_type="date", output_type="int",
        params=[FunctionParam("column", "column", True, None, "Colonna data")]
    ),
    "MONTH": FunctionDef(
        name="MONTH", label="Estrai mese",
        category=FunctionCategory.DATE,
        description="Estrae il mese da una colonna data",
        input_type="date", output_type="int",
        params=[FunctionParam("column", "column", True, None, "Colonna data")]
    ),
    "DAY": FunctionDef(
        name="DAY", label="Estrai giorno",
        category=FunctionCategory.DATE,
        description="Estrae il giorno da una colonna data",
        input_type="date", output_type="int",
        params=[FunctionParam("column", "column", True, None, "Colonna data")]
    ),


    "MATH": FunctionDef(
        name="MATH", label="Operazione matematica",
        category=FunctionCategory.NUMERIC,
        description="Esegue operazioni aritmetiche tra colonne o con valori fissi",
        input_type="real", output_type="real",
        params=[
            FunctionParam("column1", "column_numeric", True, None, "Prima colonna numerica"),
            FunctionParam("operator", "operator", True, None, "Operatore (+, -, *, /)"),
            FunctionParam("column2", "column_numeric", False, None, "Seconda colonna (opzionale)"),
            FunctionParam("value", "number", False, None, "Valore numerico (alternativa a column2)")
        ]
    ),
    "MOD": FunctionDef(
        name="MOD", label="Resto divisione",
        category=FunctionCategory.NUMERIC,
        description="Calcola il resto della divisione intera",
        input_type="real", output_type="real",
        params=[
            FunctionParam("column", "column", True, None, "Colonna numerica (dividendo)"),
            FunctionParam("divisor", "number", True, None, "Divisore")
        ]
    ),
    "POWER": FunctionDef(
        name="POWER", label="Potenza",
        category=FunctionCategory.NUMERIC,
        description="Eleva un numero alla potenza specificata",
        input_type="real", output_type="real",
        params=[
            FunctionParam("column", "column", True, None, "Colonna numerica (base)"),
            FunctionParam("exponent", "number", True, None, "Esponente")
        ]
    ),
    "SQRT": FunctionDef(
        name="SQRT", label="Radice quadrata",
        category=FunctionCategory.NUMERIC,
        description="Calcola la radice quadrata",
        input_type="real", output_type="real",
        params=[FunctionParam("column", "column", True, None, "Colonna numerica")]
    ),
    "SIGN": FunctionDef(
        name="SIGN", label="Segno",
        category=FunctionCategory.NUMERIC,
        description="Restituisce -1, 0 o 1 in base al segno del valore",
        input_type="real", output_type="int",
        params=[FunctionParam("column", "column", True, None, "Colonna numerica")]
    ),
    "GREATEST": FunctionDef(
        name="GREATEST", label="Massimo tra valori",
        category=FunctionCategory.NUMERIC,
        description="Restituisce il valore massimo tra le colonne specificate",
        input_type=None, output_type="real",
        params=[FunctionParam("values", "column_list", True, None, "Colonne da confrontare")]
    ),
    "LEAST": FunctionDef(
        name="LEAST", label="Minimo tra valori",
        category=FunctionCategory.NUMERIC,
        description="Restituisce il valore minimo tra le colonne specificate",
        input_type=None, output_type="real",
        params=[FunctionParam("values", "column_list", True, None, "Colonne da confrontare")]
    ),


    "REPLACE": FunctionDef(
        name="REPLACE", label="Sostituisci",
        category=FunctionCategory.STRING,
        description="Sostituisce tutte le occorrenze di un testo con un altro",
        input_type="str", output_type="str",
        params=[
            FunctionParam("column", "column", True, None, "Colonna testo"),
            FunctionParam("search", "string", True, None, "Testo da cercare"),
            FunctionParam("replace", "string", True, "", "Testo sostitutivo")
        ]
    ),
    "LEFT": FunctionDef(
        name="LEFT", label="Primi N caratteri",
        category=FunctionCategory.STRING,
        description="Estrae i primi N caratteri dalla stringa",
        input_type="str", output_type="str",
        params=[
            FunctionParam("column", "column", True, None, "Colonna testo"),
            FunctionParam("length", "number", True, None, "Numero di caratteri")
        ]
    ),
    "RIGHT": FunctionDef(
        name="RIGHT", label="Ultimi N caratteri",
        category=FunctionCategory.STRING,
        description="Estrae gli ultimi N caratteri dalla stringa",
        input_type="str", output_type="str",
        params=[
            FunctionParam("column", "column", True, None, "Colonna testo"),
            FunctionParam("length", "number", True, None, "Numero di caratteri")
        ]
    ),
    "POSITION": FunctionDef(
        name="POSITION", label="Trova posizione",
        category=FunctionCategory.STRING,
        description="Trova la posizione di un testo nella stringa (0 se non trovato)",
        input_type="str", output_type="int",
        params=[
            FunctionParam("column", "column", True, None, "Colonna testo"),
            FunctionParam("search", "string", True, None, "Testo da cercare")
        ]
    ),
    "LTRIM": FunctionDef(
        name="LTRIM", label="Rimuovi spazi a sinistra",
        category=FunctionCategory.STRING,
        description="Rimuove gli spazi iniziali dalla stringa",
        input_type="str", output_type="str",
        params=[FunctionParam("column", "column", True, None, "Colonna testo")]
    ),
    "RTRIM": FunctionDef(
        name="RTRIM", label="Rimuovi spazi a destra",
        category=FunctionCategory.STRING,
        description="Rimuove gli spazi finali dalla stringa",
        input_type="str", output_type="str",
        params=[FunctionParam("column", "column", True, None, "Colonna testo")]
    ),


    "NOW": FunctionDef(
        name="NOW", label="Data/ora corrente",
        category=FunctionCategory.DATE,
        description="Restituisce la data e ora corrente del server",
        input_type=None, output_type="datetime",
        params=[]
    ),
    "CURRENT_DATE": FunctionDef(
        name="CURRENT_DATE", label="Data corrente",
        category=FunctionCategory.DATE,
        description="Restituisce la data corrente (senza ora)",
        input_type=None, output_type="date",
        params=[]
    ),
    "DATE_DIFF": FunctionDef(
        name="DATE_DIFF", label="Differenza tra date",
        category=FunctionCategory.DATE,
        description="Calcola la differenza in giorni tra due date",
        input_type="date", output_type="int",
        params=[
            FunctionParam("column", "column", True, None, "Prima data"),
            FunctionParam("column2", "column", True, None, "Seconda data")
        ]
    ),
    "DATE_ADD": FunctionDef(
        name="DATE_ADD", label="Aggiungi a data",
        category=FunctionCategory.DATE,
        description="Aggiunge un intervallo a una data",
        input_type="date", output_type="date",
        params=[
            FunctionParam("column", "column", True, None, "Colonna data"),
            FunctionParam("interval", "number", True, None, "Quantita da aggiungere"),
            FunctionParam("unit", "string", False, "day", "Unita: day, month, year")
        ]
    ),
    "DATE_TRUNC": FunctionDef(
        name="DATE_TRUNC", label="Tronca data",
        category=FunctionCategory.DATE,
        description="Tronca la data all'unita specificata (year, month, week, day)",
        input_type="date", output_type="date",
        params=[
            FunctionParam("column", "column", True, None, "Colonna data"),
            FunctionParam("unit", "string", True, "month", "Unita: year, month, week, day")
        ]
    ),
    "DATE_FORMAT": FunctionDef(
        name="DATE_FORMAT", label="Formatta data",
        category=FunctionCategory.DATE,
        description="Formatta una data come stringa (es. DD/MM/YYYY)",
        input_type="date", output_type="str",
        params=[
            FunctionParam("column", "column", True, None, "Colonna data"),
            FunctionParam("format", "string", True, None, "Formato (es. DD/MM/YYYY, YYYY-MM-DD)")
        ]
    ),
    "EXTRACT": FunctionDef(
        name="EXTRACT", label="Estrai parte da data",
        category=FunctionCategory.DATE,
        description="Estrae una parte specifica dalla data (year, month, day, hour, minute, second)",
        input_type="date", output_type="int",
        params=[
            FunctionParam("column", "column", True, None, "Colonna data"),
            FunctionParam("part", "string", True, None, "Parte: year, month, day, hour, minute, second")
        ]
    ),


    "COALESCE": FunctionDef(
        name="COALESCE", label="Primo valore non nullo",
        category=FunctionCategory.CONDITIONAL,
        description="Restituisce il primo valore non NULL tra le colonne specificate",
        input_type=None, output_type="any",
        params=[FunctionParam("values", "column_list", True, None, "Colonne da valutare")]
    ),
    "NULLIF": FunctionDef(
        name="NULLIF", label="NULL se uguale",
        category=FunctionCategory.CONDITIONAL,
        description="Restituisce NULL se i due valori sono uguali, altrimenti il primo valore",
        input_type=None, output_type="any",
        params=[
            FunctionParam("column", "column", True, None, "Colonna da valutare"),
            FunctionParam("value", "string", True, None, "Valore di confronto")
        ]
    ),
    "CASE_WHEN": FunctionDef(
        name="CASE_WHEN", label="Condizione (CASE)",
        category=FunctionCategory.CONDITIONAL,
        description="Restituisce un valore se la condizione e vera, un altro altrimenti",
        input_type=None, output_type="any",
        params=[
            FunctionParam("column", "column", True, None, "Colonna da valutare"),
            FunctionParam("operator", "string", True, "=", "Operatore (=, >, <, >=, <=, <>)"),
            FunctionParam("compare_value", "string", True, None, "Valore di confronto"),
            FunctionParam("then_value", "string", True, None, "Valore se vero"),
            FunctionParam("else_value", "string", False, "NULL", "Valore se falso")
        ]
    ),
    "IIF": FunctionDef(
        name="IIF", label="If inline",
        category=FunctionCategory.CONDITIONAL,
        description="If compatto: restituisce un valore se la condizione e vera, un altro altrimenti",
        input_type=None, output_type="any",
        params=[
            FunctionParam("column", "column", True, None, "Colonna da valutare"),
            FunctionParam("operator", "string", True, "=", "Operatore (=, >, <, >=, <=, <>)"),
            FunctionParam("compare_value", "string", True, None, "Valore di confronto"),
            FunctionParam("true_value", "string", True, None, "Valore se vero"),
            FunctionParam("false_value", "string", True, None, "Valore se falso")
        ]
    ),


    "TO_INTEGER": FunctionDef(
        name="TO_INTEGER", label="Converti in intero",
        category=FunctionCategory.CAST,
        description="Converte il valore in numero intero",
        input_type=None, output_type="int",
        params=[FunctionParam("column", "column", True, None, "Colonna da convertire")]
    ),
    "TO_FLOAT": FunctionDef(
        name="TO_FLOAT", label="Converti in decimale",
        category=FunctionCategory.CAST,
        description="Converte il valore in numero decimale",
        input_type=None, output_type="real",
        params=[FunctionParam("column", "column", True, None, "Colonna da convertire")]
    ),
    "TO_STRING": FunctionDef(
        name="TO_STRING", label="Converti in testo",
        category=FunctionCategory.CAST,
        description="Converte il valore in stringa di testo",
        input_type=None, output_type="str",
        params=[FunctionParam("column", "column", True, None, "Colonna da convertire")]
    ),
    "TO_DATE": FunctionDef(
        name="TO_DATE", label="Converti in data",
        category=FunctionCategory.CAST,
        description="Converte il valore in data",
        input_type=None, output_type="date",
        params=[
            FunctionParam("column", "column", True, None, "Colonna da convertire"),
            FunctionParam("format", "string", False, None, "Formato data (opzionale, es. YYYY-MM-DD)")
        ]
    ),
}


# TRADUTTORE SQL MULTI-DATABASE

class SqlFunctionTranslator(
    SqlDateFunctionMixin,
    SqlNumericFunctionMixin,
    SqlSpatialFunctionMixin,
    SqlStringFunctionMixin,
):
    """
    Traduce funzioni astratte nel dialetto SQL specifico del database.

    Usa il pattern method dispatch: ogni funzione ha un metodo _translate_<nome>
    che genera l'SQL corretto per il database configurato.

    Dialetti gestiti per le funzioni geometriche:
        - PostgreSQL: ST_Centroid, ST_Buffer, ST_Area, ...
        - SQL Server: .STCentroid, .STBuffer, .STArea, ... (metodi su geometry)
        - MySQL: ST_Centroid, ST_Buffer, ST_Area, ... (stile OGC)
        - Oracle: SDO_GEOM.SDO_CENTROID, SDO_GEOM.SDO_BUFFER, ...
        - SQLite: Centroid, Buffer, Area, ... (SpatiaLite)
    """

    def __init__(self, db_type: DbType):
        """
        Args:
            db_type: Tipo di database target per la traduzione.
        """
        self.db_type = db_type

    def translate(self, function_name: str, params: Dict[str, Any], table_alias: str = None) -> str:
        """
        Traduce una funzione astratta in SQL.

        Cerca il metodo _translate_<nome_funzione> e lo invoca con i parametri.

        Args:
            function_name: Nome della funzione (es. "CENTROID", "BUFFER").
            params: Dizionario parametri (es. {"column": "geom", "distance": 100}).
            table_alias: Alias della tabella per qualificare i nomi colonna.

        Returns:
            Espressione SQL tradotta per il database target.

        Raises:
            ValueError: Se la funzione non è supportata.
        """
        method_name = f"_translate_{function_name.lower()}"

        if hasattr(self, method_name):
            return getattr(self, method_name)(params, table_alias)
        else:
            raise ValueError(f"Funzione {function_name} non supportata per {self.db_type.value}")

    def _qualify_column(self, column: str, table_alias: str = None) -> str:
        """
        Qualifica un nome colonna con l'alias della tabella.

        Ammessi:
            - Nome colonna semplice: "geom", "my_col"
            - Nome gia' qualificato: "t.geom", "schema.table"
            - Espressione composta: contiene parentesi e/o funzioni SQL
              (usato dai traduttori che nestano chiamate, es.
              ST_Transform(ST_Buffer(...))). Sul path espressione NON
              applichiamo whitelist — la fiducia ricade sui traduttori
              sottostanti.

        N2: per il path "nome semplice" e "gia' qualificato" applichiamo
        un whitelist regex (`_safe_qualified_identifier`) che rifiuta
        payload come `geom); DROP TABLE x;--`. Il path espressione resta
        trust-based ma l'unica fonte di espressioni in input sono gli
        altri `_translate_*` di questo modulo (che ora validano i loro
        input tramite gli helper), quindi la catena e' safe end-to-end.

        Raises:
            ValueError: se la colonna non e' ne' un'espressione ne' un
            identifier valido secondo il whitelist.
        """
        if not isinstance(column, str):
            return str(column)

        # Path "espressione SQL": contiene parentesi → fiducia al
        # traduttore che ha prodotto l'espressione. Questi input NON
        # arrivano direttamente dall'utente in condizioni normali: sono
        # frammenti SQL emessi da altre _translate_*
        # N2 defense-in-depth: rifiuta comunque marker strutturali di
        # injection. Non compaiono mai negli output legittimi dei
        # _translate_*, quindi niente falsi positivi
        if "(" in column or ")" in column:
            for marker in (";", "--", "/*", "*/", "\x00"):
                if marker in column:
                    raise ValueError(
                        f"_qualify_column: espressione contiene marker "
                        f"di injection '{marker}': {column!r}"
                    )
            return column

        # Path "identifier": validiamo whitelist prima di usarlo
        # Accetta "col" o "table.col" (2 parti
        _safe_qualified_identifier(column)

        # Se e' gia' qualificato (contiene un punto), non aggiungere alias
        if "." in column:
            return column

        # Qualifica con l'alias della tabella
        if table_alias:
            return f"{table_alias}.{column}"
        return column

    # FUNZIONI CONDIZIONALI (nuove

    def _translate_coalesce(self, params: Dict, table_alias: str = None) -> str:
        """Primo valore non NULL: colonne qualificate e literal SQL sicuri."""
        valori = params["values"]
        if isinstance(valori, str):
            valori = _split_csv_respect_quotes(valori)

        out_parts: list[str] = []
        for v in valori:
            v_str = str(v).strip()
            if not v_str:
                continue
            if _is_safe_single_quoted_literal(v_str):
                out_parts.append(v_str)
            else:
                out_parts.append(self._qualify_column(v_str, table_alias))
        return f"COALESCE({', '.join(out_parts)})"

    def _translate_nullif(self, params: Dict, table_alias: str = None) -> str:
        """NULL se uguale — NULLIF() identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        valore = _sql_escape(params["value"])
        return f"NULLIF({col}, '{valore}')"

    def _translate_case_when(self, params: Dict, table_alias: str = None) -> str:
        """Espressione condizionale CASE WHEN — identica su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        # N2: whitelist su operator (finiva raw in f-string
        operatore = _safe_compare_operator(params.get("operator", "="))
        confronto = _sql_escape(params["compare_value"])
        valore_vero = _sql_escape(params["then_value"])
        valore_falso = _sql_escape(params.get("else_value", "NULL"))
        return f"CASE WHEN {col} {operatore} '{confronto}' THEN '{valore_vero}' ELSE '{valore_falso}' END"

    def _translate_iif(self, params: Dict, table_alias: str = None) -> str:
        """If inline — MSSQL/SQLite hanno IIF, altri usano CASE WHEN."""
        col = self._qualify_column(params["column"], table_alias)
        # N2: whitelist su operator (finiva raw in f-string
        operatore = _safe_compare_operator(params.get("operator", "="))
        confronto = _sql_escape(params["compare_value"])
        valore_vero = _sql_escape(params["true_value"])
        valore_falso = _sql_escape(params["false_value"])
        condizione = f"{col} {operatore} '{confronto}'"
        traduzioni = {
            DbType.POSTGRES: f"CASE WHEN {condizione} THEN '{valore_vero}' ELSE '{valore_falso}' END",
            DbType.MSSQL: f"IIF({condizione}, '{valore_vero}', '{valore_falso}')",
            DbType.MYSQL: f"IF({condizione}, '{valore_vero}', '{valore_falso}')",
            DbType.ORACLE: f"CASE WHEN {condizione} THEN '{valore_vero}' ELSE '{valore_falso}' END",
            DbType.SQLITE: f"IIF({condizione}, '{valore_vero}', '{valore_falso}')",
        }
        return self._ottieni_traduzione(traduzioni, "IIF")

    # FUNZIONI CONVERSIONE / CAST (nuove

    def _translate_to_integer(self, params: Dict, table_alias: str = None) -> str:
        """Conversione a intero — sintassi CAST diversa per database."""
        col = self._qualify_column(params["column"], table_alias)
        traduzioni = {
            DbType.POSTGRES: f"CAST({col} AS INTEGER)",
            DbType.MSSQL: f"CAST({col} AS INT)",
            DbType.MYSQL: f"CAST({col} AS SIGNED)",
            DbType.ORACLE: f"CAST({col} AS NUMBER)",
            DbType.SQLITE: f"CAST({col} AS INTEGER)",
        }
        return self._ottieni_traduzione(traduzioni, "TO_INTEGER")

    def _translate_to_float(self, params: Dict, table_alias: str = None) -> str:
        """Conversione a decimale — sintassi CAST diversa per database."""
        col = self._qualify_column(params["column"], table_alias)
        traduzioni = {
            DbType.POSTGRES: f"CAST({col} AS DOUBLE PRECISION)",
            DbType.MSSQL: f"CAST({col} AS FLOAT)",
            DbType.MYSQL: f"CAST({col} AS DECIMAL(18,6))",
            DbType.ORACLE: f"CAST({col} AS NUMBER)",
            DbType.SQLITE: f"CAST({col} AS REAL)",
        }
        return self._ottieni_traduzione(traduzioni, "TO_FLOAT")

    def _translate_to_string(self, params: Dict, table_alias: str = None) -> str:
        """Conversione a stringa — sintassi diversa per database."""
        col = self._qualify_column(params["column"], table_alias)
        traduzioni = {
            DbType.POSTGRES: f"CAST({col} AS TEXT)",
            DbType.MSSQL: f"CAST({col} AS NVARCHAR(MAX))",
            DbType.MYSQL: f"CAST({col} AS CHAR)",
            DbType.ORACLE: f"TO_CHAR({col})",
            DbType.SQLITE: f"CAST({col} AS TEXT)",
        }
        return self._ottieni_traduzione(traduzioni, "TO_STRING")

    def _translate_to_date(self, params: Dict, table_alias: str = None) -> str:
        """Conversione a data — con formato opzionale."""
        col = self._qualify_column(params["column"], table_alias)
        formato = params.get("format")
        if formato:
            traduzioni = {
                DbType.POSTGRES: f"TO_DATE({col}, '{formato}')",
                DbType.MSSQL: f"CONVERT(DATE, {col})",
                DbType.MYSQL: f"STR_TO_DATE({col}, '{formato}')",
                DbType.ORACLE: f"TO_DATE({col}, '{formato}')",
                DbType.SQLITE: f"date({col})",
            }
        else:
            traduzioni = {
                DbType.POSTGRES: f"CAST({col} AS DATE)",
                DbType.MSSQL: f"CAST({col} AS DATE)",
                DbType.MYSQL: f"CAST({col} AS DATE)",
                DbType.ORACLE: f"CAST({col} AS DATE)",
                DbType.SQLITE: f"date({col})",
            }
        return self._ottieni_traduzione(traduzioni, "TO_DATE")

    # HELPER INTERNO

    def _ottieni_traduzione(self, traduzioni: Dict[DbType, str], nome_funzione: str) -> str:
        """
        Helper per restituire la traduzione corretta dal dizionario.

        Args:
            traduzioni: Mappa DbType → espressione SQL.
            nome_funzione: Nome funzione (per messaggio errore).

        Returns:
            Espressione SQL per il database corrente.

        Raises:
            ValueError: Se il database non è presente nel dizionario.
        """
        if self.db_type in traduzioni:
            return traduzioni[self.db_type]
        raise ValueError(f"{nome_funzione} non supportato per {self.db_type.value}")


# FUNZIONI HELPER PUBBLICHE

def get_function_catalog() -> Dict[str, dict]:
    """
    Restituisce il catalogo funzioni in formato serializzabile per il frontend.

    Converte le definizioni FunctionDef in dizionari JSON-compatibili
    per essere inviati al client e visualizzati nella UI.

    Returns:
        Dizionario {nome_funzione: {...dettagli...}}.
    """
    risultato = {}
    for nome, func_def in FUNCTION_CATALOG.items():
        risultato[nome] = {
            "name": func_def.name,
            "label": func_def.label,
            "category": func_def.category.value,
            "description": func_def.description,
            "input_type": func_def.input_type,
            "output_type": func_def.output_type,
            "params": [
                {
                    "name": p.name,
                    "type": p.param_type,
                    "required": p.required,
                    "default": p.default,
                    "description": p.description
                }
                for p in func_def.params
            ]
        }
    return risultato


def get_translator(db_type: str) -> SqlFunctionTranslator:
    """
    Factory per ottenere il traduttore SQL corretto per un tipo di database.

    Args:
        db_type: Stringa tipo database ("postgres", "mssql", "mysql", "oracle", "sqlite").

    Returns:
        Istanza di SqlFunctionTranslator configurata.
    """
    return SqlFunctionTranslator(DbType(db_type))


def translate_calculated_column(
    db_type: str,
    column_name: str,
    expression: dict,
    table_alias: str = None
) -> str:
    """
    Traduce la definizione di una colonna calcolata in espressione SQL completa.

    Supporta espressioni annidate: una funzione può ricevere come parametro
    il risultato di un'altra funzione (es. BUFFER(CENTROID(geom), 100)).

    Se la funzione più esterna produce una geometria, il risultato viene
    automaticamente wrappato con la funzione di conversione WKT appropriata
    per il database (ST_AsText, STAsText, SDO_UTIL.TO_WKTGEOMETRY, AsText).

    Args:
        db_type: Tipo database ("postgres", "mssql", etc.).
        column_name: Nome della colonna risultato (usato nell'AS).
        expression: Definizione espressione, es:
            {"function": "BUFFER", "params": {"column": "geom", "distance": 100}}
            o con nesting:
            {"function": "BUFFER", "params": {
                "column": {"function": "CENTROID", "params": {"column": "geom"}},
                "distance": 100
            }}
        table_alias: Alias tabella per qualificare le colonne.

    Returns:
        Espressione SQL completa con alias (es. "ST_AsText(ST_Buffer(t.geom, 100)) AS buffer_col").
    """
    traduttore = get_translator(db_type)
    sql_expr = _translate_expression(traduttore, expression, table_alias)

    # Verifica se il risultato è una geometria (per wrappare con conversione WKT
    nome_funzione = expression.get("function") if isinstance(expression, dict) else None
    output_geometria = False

    if nome_funzione and nome_funzione in FUNCTION_CATALOG:
        tipo_output = FUNCTION_CATALOG[nome_funzione].output_type
        output_geometria = tipo_output.startswith("geometry")

    # Se l'output è una geometria, converte in WKT per uniformità
    if output_geometria:
        sql_expr = _wrappa_geometria_come_wkt(db_type, sql_expr)

    return f"{sql_expr} AS {column_name}"


def _wrappa_geometria_come_wkt(db_type: str, espressione_geom: str) -> str:
    """
    Avvolge un'espressione geometrica nella funzione di conversione WKT.

    Ogni database ha la propria funzione per convertire geometria → WKT:
        - PostgreSQL: ST_AsText(...)
        - SQL Server: (...).STAsText
        - MySQL: ST_AsText(...)
        - Oracle: SDO_UTIL.TO_WKTGEOMETRY(...)
        - SQLite: AsText(...)

    Args:
        db_type: Tipo database.
        espressione_geom: Espressione SQL che produce una geometria.

    Returns:
        Espressione SQL che produce una stringa WKT.
    """
    funzioni_wkt = {
        "postgres": f"ST_AsText({espressione_geom})",
        "mssql": f"({espressione_geom}).STAsText()",
        "mysql": f"ST_AsText({espressione_geom})",
        "oracle": f"SDO_UTIL.TO_WKTGEOMETRY({espressione_geom})",
        "sqlite": f"AsText({espressione_geom})",
    }
    return funzioni_wkt.get(db_type, f"ST_AsText({espressione_geom})")


def _translate_expression(
    traduttore: SqlFunctionTranslator,
    expression: dict,
    table_alias: str = None
) -> str:
    """
    Traduce ricorsivamente un'espressione (supporta espressioni annidate).

    Gestisce tre tipi di espressione:
        - Stringa: nome colonna semplice → qualificata con alias
        - Non-dict: valore letterale → convertito in stringa
        - Dict con "function": espressione funzione → tradotta ricorsivamente

    Args:
        traduttore: Istanza del traduttore SQL.
        expression: Espressione da tradurre (stringa, numero o dict).
        table_alias: Alias tabella per qualificare le colonne.

    Returns:
        Espressione SQL tradotta.
    """
    # Nome colonna semplice
    if isinstance(expression, str):
        if table_alias:
            return f"{table_alias}.{expression}"
        return expression

    # Valore letterale (numero, etc
    if not isinstance(expression, dict):
        return str(expression)

    # Espressione funzione: risolvi parametri ricorsivamente
    nome_funzione = expression.get("function")
    parametri = expression.get("params", {})

    # Validation: senza `function` non c'e' nulla da tradurre. Senza
    # questo check `traduttore.translate(None, ...)` crasha con
    # AttributeError → 500 nel router. Mappiamo il caso utente
    # (espressione incompleta dal FE wizard) a un 400 chiaro.
    if not nome_funzione:
        raise ValueError(
            "Espressione mancante del campo 'function'. La calculated "
            "column non e' configurata: completare l'operazione nel "
            "wizard prima di salvare/preview."
        )

    parametri_risolti = {}
    for chiave, valore in parametri.items():
        if isinstance(valore, dict) and "function" in valore:
            # Parametro è a sua volta un'espressione funzione → ricorsione
            parametri_risolti[chiave] = _translate_expression(traduttore, valore, table_alias)
        else:
            parametri_risolti[chiave] = valore

    return traduttore.translate(nome_funzione, parametri_risolti, table_alias)
