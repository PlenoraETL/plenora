"""
Catalogo funzioni SQL astratte e traduttore multi-database per URBY-ETL.

Architettura
============

Definisce un sistema a due livelli:
    1. FUNCTION_CATALOG: catalogo di funzioni astratte (indipendenti dal DB)
    2. SqlFunctionTranslator: traduce ogni funzione nel dialetto SQL specifico

Categorie di funzioni:
    - Geometriche: CENTROID, BUFFER, TRANSFORM, AREA, LENGTH, PERIMETER,
                   SIMPLIFY, CONVEX_HULL, ENVELOPE
    - Numeriche: ROUND, ABS, CEIL, FLOOR, MATH (operazioni aritmetiche)
    - Stringa: UPPER, LOWER, TRIM, CONCAT, SUBSTRING
    - Data: YEAR, MONTH, DAY

Database supportati:
    - PostgreSQL (PostGIS): ST_* functions
    - SQL Server: .ST*() methods on geometry type
    - MySQL: ST_* functions (8.0+)
    - Oracle: SDO_GEOM.*, SDO_UTIL.*, SDO_CS.* packages
    - SQLite (SpatiaLite): funzioni native SpatiaLite

Sicurezza:
    La funzione MATH include protezione divisione per zero specifica
    per ogni database (NULLIF, CASE WHEN, IF).

Uso:
    from app.db.sql_functions import (
        get_function_catalog,
        get_translator,
        translate_calculated_column
    )

    # Catalogo per il frontend
    catalogo = get_function_catalog()

    # Traduzione singola funzione
    traduttore = get_translator("postgres")
    sql = traduttore.translate("CENTROID", {"column": "geom"}, table_alias="t")
    # → "ST_Centroid(t.geom)"

    # Traduzione colonna calcolata (con supporto espressioni annidate)
    sql = translate_calculated_column("postgres", "centro", {
        "function": "CENTROID", "params": {"column": "geom"}
    })
    # → "ST_AsText(ST_Centroid(geom)) AS centro"
"""

from typing import Dict, List, Optional, Any
from enum import Enum
from dataclasses import dataclass


# =============================================================================
# ENUMERAZIONI
# =============================================================================

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


# =============================================================================
# DEFINIZIONI FUNZIONI
# =============================================================================

@dataclass
class FunctionParam:
    """
    Definizione di un parametro di funzione.

    Tipi di parametro: "column", "number", "string", "srid",
    "column_list", "column_numeric", "operator".
    """
    name: str
    param_type: str     # Tipo del parametro (per validazione UI)
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
    name: str                       # Identificatore univoco (es. "CENTROID")
    label: str                      # Etichetta UI (es. "Centroide")
    category: FunctionCategory      # Categoria per raggruppamento UI
    description: str                # Descrizione per l'utente
    input_type: Optional[str]       # Tipo colonna richiesto ("geometry", "real", None = qualsiasi)
    output_type: str                # Tipo del risultato ("geometry:point", "real", "str", ...)
    params: List[FunctionParam]     # Parametri della funzione


# =============================================================================
# CATALOGO FUNZIONI DISPONIBILI
# =============================================================================

FUNCTION_CATALOG: Dict[str, FunctionDef] = {

    # --- FUNZIONI GEOMETRICHE ---

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

    # --- FUNZIONI NUMERICHE ---

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

    # --- FUNZIONI STRINGA ---

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

    # --- FUNZIONI DATA ---

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

    # --- OPERAZIONE MATEMATICA ---

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

    # --- FUNZIONI STRINGA (nuove) ---

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

    # --- FUNZIONI DATA (nuove) ---

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

    # --- FUNZIONI CONDIZIONALI ---

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

    # --- FUNZIONI CONVERSIONE (CAST) ---

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


# =============================================================================
# TRADUTTORE SQL MULTI-DATABASE
# =============================================================================

class SqlFunctionTranslator:
    """
    Traduce funzioni astratte nel dialetto SQL specifico del database.

    Usa il pattern method dispatch: ogni funzione ha un metodo _translate_<nome>
    che genera l'SQL corretto per il database configurato.

    Dialetti gestiti per le funzioni geometriche:
        - PostgreSQL: ST_Centroid(), ST_Buffer(), ST_Area(), ...
        - SQL Server: .STCentroid(), .STBuffer(), .STArea(), ... (metodi su geometry)
        - MySQL: ST_Centroid(), ST_Buffer(), ST_Area(), ... (stile OGC)
        - Oracle: SDO_GEOM.SDO_CENTROID(), SDO_GEOM.SDO_BUFFER(), ...
        - SQLite: Centroid(), Buffer(), Area(), ... (SpatiaLite)
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

        NON qualifica se:
            - La colonna è già un'espressione SQL (contiene parentesi)
            - La colonna è già qualificata (contiene un punto, es. "tabella.colonna")

        Args:
            column: Nome colonna o espressione SQL.
            table_alias: Alias tabella da anteporre.

        Returns:
            Colonna qualificata (es. "t.geom") o espressione originale.
        """
        if not isinstance(column, str):
            return str(column)

        # Se contiene parentesi, è un'espressione SQL complessa
        if "(" in column or ")" in column:
            return column

        # Se è già qualificata (contiene un punto), non modificare
        if "." in column:
            parti = column.split(".")
            if len(parti) == 2 and parti[0] and parti[1]:
                return column

        # Qualifica con l'alias della tabella
        if table_alias:
            return f"{table_alias}.{column}"
        return column

    # =========================================================================
    # FUNZIONI GEOMETRICHE
    # =========================================================================

    def _translate_centroid(self, params: Dict, table_alias: str = None) -> str:
        """Centroide — centro geometrico della geometria."""
        col = self._qualify_column(params["column"], table_alias)

        traduzioni = {
            DbType.POSTGRES: f"ST_Centroid({col})",
            DbType.MSSQL: f"{col}.STCentroid()",
            DbType.MYSQL: f"ST_Centroid({col})",
            DbType.ORACLE: f"SDO_GEOM.SDO_CENTROID({col}, 0.005)",
            DbType.SQLITE: f"Centroid({col})",
        }
        return self._ottieni_traduzione(traduzioni, "CENTROID")

    def _translate_buffer(self, params: Dict, table_alias: str = None) -> str:
        """Buffer — area di distanza attorno alla geometria."""
        col = self._qualify_column(params["column"], table_alias)
        distanza = params["distance"]

        traduzioni = {
            DbType.POSTGRES: f"ST_Buffer({col}, {distanza})",
            DbType.MSSQL: f"{col}.STBuffer({distanza})",
            DbType.MYSQL: f"ST_Buffer({col}, {distanza})",
            DbType.ORACLE: f"SDO_GEOM.SDO_BUFFER({col}, {distanza}, 0.005)",
            DbType.SQLITE: f"Buffer({col}, {distanza})",
        }
        return self._ottieni_traduzione(traduzioni, "BUFFER")

    def _translate_transform(self, params: Dict, table_alias: str = None) -> str:
        """Riproiezione — trasforma la geometria in un altro CRS."""
        col = self._qualify_column(params["column"], table_alias)
        srid = params["srid"]

        traduzioni = {
            DbType.POSTGRES: f"ST_Transform({col}, {srid})",
            DbType.MSSQL: f"{col}.STTransform({srid})",  # Richiede estensione
            DbType.MYSQL: f"ST_Transform({col}, {srid})",  # MySQL 8.0+
            DbType.ORACLE: f"SDO_CS.TRANSFORM({col}, {srid})",
            DbType.SQLITE: f"Transform({col}, {srid})",
        }
        return self._ottieni_traduzione(traduzioni, "TRANSFORM")

    def _translate_area(self, params: Dict, table_alias: str = None) -> str:
        """Area — superficie del poligono."""
        col = self._qualify_column(params["column"], table_alias)

        traduzioni = {
            DbType.POSTGRES: f"ST_Area({col})",
            DbType.MSSQL: f"{col}.STArea()",
            DbType.MYSQL: f"ST_Area({col})",
            DbType.ORACLE: f"SDO_GEOM.SDO_AREA({col}, 0.005)",
            DbType.SQLITE: f"Area({col})",
        }
        return self._ottieni_traduzione(traduzioni, "AREA")

    def _translate_length(self, params: Dict, table_alias: str = None) -> str:
        """Lunghezza — lunghezza della linea."""
        col = self._qualify_column(params["column"], table_alias)

        traduzioni = {
            DbType.POSTGRES: f"ST_Length({col})",
            DbType.MSSQL: f"{col}.STLength()",
            DbType.MYSQL: f"ST_Length({col})",
            DbType.ORACLE: f"SDO_GEOM.SDO_LENGTH({col}, 0.005)",
            DbType.SQLITE: f"GLength({col})",  # SpatiaLite usa GLength
        }
        return self._ottieni_traduzione(traduzioni, "LENGTH")

    def _translate_perimeter(self, params: Dict, table_alias: str = None) -> str:
        """Perimetro — lunghezza del contorno del poligono."""
        col = self._qualify_column(params["column"], table_alias)

        traduzioni = {
            DbType.POSTGRES: f"ST_Perimeter({col})",
            DbType.MSSQL: f"{col}.STBoundary().STLength()",  # MSSQL non ha STPerimeter
            DbType.MYSQL: f"ST_Length(ST_ExteriorRing({col}))",  # MySQL non ha ST_Perimeter
            DbType.ORACLE: f"SDO_GEOM.SDO_LENGTH({col}, 0.005)",  # SDO_LENGTH su polygon = perimetro
            DbType.SQLITE: f"Perimeter({col})",
        }
        return self._ottieni_traduzione(traduzioni, "PERIMETER")

    def _translate_simplify(self, params: Dict, table_alias: str = None) -> str:
        """Semplificazione — riduce i vertici della geometria."""
        col = self._qualify_column(params["column"], table_alias)
        tolleranza = params["tolerance"]

        traduzioni = {
            DbType.POSTGRES: f"ST_Simplify({col}, {tolleranza})",
            DbType.MSSQL: f"{col}.Reduce({tolleranza})",
            DbType.MYSQL: f"ST_Simplify({col}, {tolleranza})",
            DbType.ORACLE: f"SDO_UTIL.SIMPLIFY({col}, {tolleranza})",
            DbType.SQLITE: f"Simplify({col}, {tolleranza})",
        }
        return self._ottieni_traduzione(traduzioni, "SIMPLIFY")

    def _translate_convex_hull(self, params: Dict, table_alias: str = None) -> str:
        """Inviluppo convesso — poligono convesso minimo che contiene la geometria."""
        col = self._qualify_column(params["column"], table_alias)

        traduzioni = {
            DbType.POSTGRES: f"ST_ConvexHull({col})",
            DbType.MSSQL: f"{col}.STConvexHull()",
            DbType.MYSQL: f"ST_ConvexHull({col})",
            DbType.ORACLE: f"SDO_GEOM.SDO_CONVEXHULL({col}, 0.005)",
            DbType.SQLITE: f"ConvexHull({col})",
        }
        return self._ottieni_traduzione(traduzioni, "CONVEX_HULL")

    def _translate_envelope(self, params: Dict, table_alias: str = None) -> str:
        """Bounding Box — rettangolo di delimitazione minimo (MBR)."""
        col = self._qualify_column(params["column"], table_alias)

        traduzioni = {
            DbType.POSTGRES: f"ST_Envelope({col})",
            DbType.MSSQL: f"{col}.STEnvelope()",
            DbType.MYSQL: f"ST_Envelope({col})",
            DbType.ORACLE: f"SDO_GEOM.SDO_MBR({col})",
            DbType.SQLITE: f"Envelope({col})",
        }
        return self._ottieni_traduzione(traduzioni, "ENVELOPE")

    # =========================================================================
    # FUNZIONI NUMERICHE
    # =========================================================================

    def _translate_round(self, params: Dict, table_alias: str = None) -> str:
        """Arrotondamento — al numero di decimali specificato."""
        col = self._qualify_column(params["column"], table_alias)
        decimali = params.get("decimals")

        # Normalizza il valore decimali (può essere None, stringa vuota, etc.)
        if decimali is None or decimali == "":
            decimali = 0
        else:
            try:
                decimali = int(decimali)
            except (ValueError, TypeError):
                decimali = 0

        # PostgreSQL richiede cast a numeric per ROUND con decimali
        if self.db_type == DbType.POSTGRES:
            return f"ROUND({col}::numeric, {decimali})"
        return f"ROUND({col}, {decimali})"

    def _translate_abs(self, params: Dict, table_alias: str = None) -> str:
        """Valore assoluto — identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"ABS({col})"

    def _translate_ceil(self, params: Dict, table_alias: str = None) -> str:
        """Arrotondamento per eccesso — MSSQL usa CEILING, altri usano CEIL."""
        col = self._qualify_column(params["column"], table_alias)
        if self.db_type == DbType.MSSQL:
            return f"CEILING({col})"
        return f"CEIL({col})"

    def _translate_floor(self, params: Dict, table_alias: str = None) -> str:
        """Arrotondamento per difetto — identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"FLOOR({col})"

    # =========================================================================
    # FUNZIONI STRINGA
    # =========================================================================

    def _translate_upper(self, params: Dict, table_alias: str = None) -> str:
        """Conversione in maiuscolo — identica su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"UPPER({col})"

    def _translate_lower(self, params: Dict, table_alias: str = None) -> str:
        """Conversione in minuscolo — identica su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"LOWER({col})"

    def _translate_trim(self, params: Dict, table_alias: str = None) -> str:
        """Rimozione spazi — MSSQL usa LTRIM(RTRIM(...)), gli altri TRIM(...)."""
        col = self._qualify_column(params["column"], table_alias)
        if self.db_type == DbType.MSSQL:
            return f"LTRIM(RTRIM({col}))"
        return f"TRIM({col})"

    def _translate_concat(self, params: Dict, table_alias: str = None) -> str:
        """
        Concatenazione di più colonne con separatore opzionale.

        Oracle e SQLite non supportano CONCAT_WS nativo, quindi usano
        l'operatore || con il separatore inserito tra i valori.
        """
        valori = params["values"]  # Lista di nomi colonna (o stringa singola)
        # Normalizza a lista: il frontend può mandare una stringa singola o CSV
        if isinstance(valori, str):
            valori = [v.strip() for v in valori.split(',') if v.strip()]
        separatore = params.get("separator", "")
        valori_qualificati = [self._qualify_column(v, table_alias) for v in valori]

        if self.db_type in (DbType.POSTGRES, DbType.MSSQL, DbType.MYSQL):
            if separatore:
                return f"CONCAT_WS('{separatore}', {', '.join(valori_qualificati)})"
            return f"CONCAT({', '.join(valori_qualificati)})"

        elif self.db_type in (DbType.ORACLE, DbType.SQLITE):
            # Oracle/SQLite: usa operatore || per concatenazione
            if separatore:
                parti = [f"{v} || '{separatore}'" for v in valori_qualificati[:-1]]
                parti.append(valori_qualificati[-1])
                return " || ".join(parti)
            return " || ".join(valori_qualificati)

        return f"CONCAT({', '.join(valori_qualificati)})"

    def _translate_substring(self, params: Dict, table_alias: str = None) -> str:
        """
        Estrazione sottostringa.

        Ogni database ha una sintassi leggermente diversa:
            - PostgreSQL: SUBSTRING(col FROM start FOR length)
            - MSSQL: SUBSTRING(col, start, length) — richiede length
            - MySQL: SUBSTRING(col, start[, length])
            - Oracle/SQLite: SUBSTR(col, start[, length])
        """
        col = self._qualify_column(params["column"], table_alias)
        inizio = params.get("start", 1)
        lunghezza = params.get("length")

        if self.db_type == DbType.POSTGRES:
            if lunghezza:
                return f"SUBSTRING({col} FROM {inizio} FOR {lunghezza})"
            return f"SUBSTRING({col} FROM {inizio})"
        elif self.db_type == DbType.MSSQL:
            if lunghezza:
                return f"SUBSTRING({col}, {inizio}, {lunghezza})"
            return f"SUBSTRING({col}, {inizio}, LEN({col}))"
        elif self.db_type == DbType.MYSQL:
            if lunghezza:
                return f"SUBSTRING({col}, {inizio}, {lunghezza})"
            return f"SUBSTRING({col}, {inizio})"
        elif self.db_type in (DbType.ORACLE, DbType.SQLITE):
            if lunghezza:
                return f"SUBSTR({col}, {inizio}, {lunghezza})"
            return f"SUBSTR({col}, {inizio})"

        return f"SUBSTRING({col}, {inizio}, {lunghezza or 'NULL'})"

    # =========================================================================
    # FUNZIONI DATA
    # =========================================================================

    def _translate_year(self, params: Dict, table_alias: str = None) -> str:
        """Estrazione anno — PostgreSQL/Oracle: EXTRACT, MSSQL/MySQL: YEAR(), SQLite: STRFTIME."""
        col = self._qualify_column(params["column"], table_alias)
        traduzioni = {
            DbType.POSTGRES: f"EXTRACT(YEAR FROM {col})",
            DbType.MSSQL: f"YEAR({col})",
            DbType.MYSQL: f"YEAR({col})",
            DbType.ORACLE: f"EXTRACT(YEAR FROM {col})",
            DbType.SQLITE: f"CAST(STRFTIME('%Y', {col}) AS INTEGER)",
        }
        return self._ottieni_traduzione(traduzioni, "YEAR")

    def _translate_month(self, params: Dict, table_alias: str = None) -> str:
        """Estrazione mese — stessi dialetti di YEAR."""
        col = self._qualify_column(params["column"], table_alias)
        traduzioni = {
            DbType.POSTGRES: f"EXTRACT(MONTH FROM {col})",
            DbType.MSSQL: f"MONTH({col})",
            DbType.MYSQL: f"MONTH({col})",
            DbType.ORACLE: f"EXTRACT(MONTH FROM {col})",
            DbType.SQLITE: f"CAST(STRFTIME('%m', {col}) AS INTEGER)",
        }
        return self._ottieni_traduzione(traduzioni, "MONTH")

    def _translate_day(self, params: Dict, table_alias: str = None) -> str:
        """Estrazione giorno — stessi dialetti di YEAR."""
        col = self._qualify_column(params["column"], table_alias)
        traduzioni = {
            DbType.POSTGRES: f"EXTRACT(DAY FROM {col})",
            DbType.MSSQL: f"DAY({col})",
            DbType.MYSQL: f"DAY({col})",
            DbType.ORACLE: f"EXTRACT(DAY FROM {col})",
            DbType.SQLITE: f"CAST(STRFTIME('%d', {col}) AS INTEGER)",
        }
        return self._ottieni_traduzione(traduzioni, "DAY")

    # =========================================================================
    # OPERAZIONE MATEMATICA
    # =========================================================================

    def _translate_math(self, params: Dict, table_alias: str = None) -> str:
        """
        Operazione aritmetica tra colonne o con valore fisso.

        Supporta: +, -, *, /
        Include protezione divisione per zero specifica per database:
            - PostgreSQL/SQLite: NULLIF(divisore, 0)
            - MSSQL/Oracle: CASE WHEN divisore = 0 THEN NULL
            - MySQL: IF(divisore = 0, NULL, ...)
        """
        col1 = self._qualify_column(params["column1"], table_alias)
        operatore = params.get("operator") or "+"

        # Validazione operatore
        operatori_validi = {"+", "-", "*", "/"}
        if operatore not in operatori_validi:
            raise ValueError(f"Operatore non valido: {operatore}. Usa: +, -, *, /")

        # Secondo operando: colonna o valore fisso
        col2 = params.get("column2")
        valore = params.get("value")

        if col2:
            secondo_operando = self._qualify_column(col2, table_alias)
        elif valore is not None:
            secondo_operando = str(valore)
        else:
            raise ValueError("Specificare column2 o value per l'operazione matematica")

        # Gestione divisione per zero (database-specifica)
        if operatore == "/":
            protezioni = {
                DbType.POSTGRES: f"({col1} / NULLIF({secondo_operando}, 0))",
                DbType.MSSQL: f"CASE WHEN {secondo_operando} = 0 THEN NULL ELSE {col1} / {secondo_operando} END",
                DbType.MYSQL: f"IF({secondo_operando} = 0, NULL, {col1} / {secondo_operando})",
                DbType.ORACLE: f"CASE WHEN {secondo_operando} = 0 THEN NULL ELSE {col1} / {secondo_operando} END",
                DbType.SQLITE: f"({col1} / NULLIF({secondo_operando}, 0))",
            }
            return protezioni.get(self.db_type, f"({col1} / NULLIF({secondo_operando}, 0))")

        return f"({col1} {operatore} {secondo_operando})"

    # =========================================================================
    # FUNZIONI NUMERICHE (nuove)
    # =========================================================================

    def _translate_mod(self, params: Dict, table_alias: str = None) -> str:
        """Resto divisione — MSSQL/SQLite usano %, altri MOD()."""
        col = self._qualify_column(params["column"], table_alias)
        divisore = params["divisor"]
        traduzioni = {
            DbType.POSTGRES: f"MOD({col}, {divisore})",
            DbType.MSSQL: f"({col} % {divisore})",
            DbType.MYSQL: f"MOD({col}, {divisore})",
            DbType.ORACLE: f"MOD({col}, {divisore})",
            DbType.SQLITE: f"({col} % {divisore})",
        }
        return self._ottieni_traduzione(traduzioni, "MOD")

    def _translate_power(self, params: Dict, table_alias: str = None) -> str:
        """Potenza — POWER() su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        esponente = params["exponent"]
        return f"POWER({col}, {esponente})"

    def _translate_sqrt(self, params: Dict, table_alias: str = None) -> str:
        """Radice quadrata — SQRT() su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"SQRT({col})"

    def _translate_sign(self, params: Dict, table_alias: str = None) -> str:
        """Segno — SIGN() su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"SIGN({col})"

    def _translate_greatest(self, params: Dict, table_alias: str = None) -> str:
        """Massimo tra valori — MSSQL non ha GREATEST nativo (pre-2022)."""
        valori = params["values"]
        if isinstance(valori, str):
            valori = [v.strip() for v in valori.split(',') if v.strip()]
        valori_q = [self._qualify_column(v, table_alias) for v in valori]

        if self.db_type == DbType.MSSQL:
            # MSSQL < 2022: catena di IIF
            risultato = valori_q[0]
            for v in valori_q[1:]:
                risultato = f"IIF({risultato} > {v}, {risultato}, {v})"
            return risultato
        if self.db_type == DbType.SQLITE:
            return f"MAX({', '.join(valori_q)})"
        return f"GREATEST({', '.join(valori_q)})"

    def _translate_least(self, params: Dict, table_alias: str = None) -> str:
        """Minimo tra valori — MSSQL non ha LEAST nativo (pre-2022)."""
        valori = params["values"]
        if isinstance(valori, str):
            valori = [v.strip() for v in valori.split(',') if v.strip()]
        valori_q = [self._qualify_column(v, table_alias) for v in valori]

        if self.db_type == DbType.MSSQL:
            risultato = valori_q[0]
            for v in valori_q[1:]:
                risultato = f"IIF({risultato} < {v}, {risultato}, {v})"
            return risultato
        if self.db_type == DbType.SQLITE:
            return f"MIN({', '.join(valori_q)})"
        return f"LEAST({', '.join(valori_q)})"

    # =========================================================================
    # FUNZIONI STRINGA (nuove)
    # =========================================================================

    def _translate_replace(self, params: Dict, table_alias: str = None) -> str:
        """Sostituzione testo — REPLACE() identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        cerca = params["search"]
        sostituisci = params.get("replace", "")
        return f"REPLACE({col}, '{cerca}', '{sostituisci}')"

    def _translate_left(self, params: Dict, table_alias: str = None) -> str:
        """Primi N caratteri — Oracle/SQLite usano SUBSTR."""
        col = self._qualify_column(params["column"], table_alias)
        lunghezza = params["length"]
        traduzioni = {
            DbType.POSTGRES: f"LEFT({col}, {lunghezza})",
            DbType.MSSQL: f"LEFT({col}, {lunghezza})",
            DbType.MYSQL: f"LEFT({col}, {lunghezza})",
            DbType.ORACLE: f"SUBSTR({col}, 1, {lunghezza})",
            DbType.SQLITE: f"SUBSTR({col}, 1, {lunghezza})",
        }
        return self._ottieni_traduzione(traduzioni, "LEFT")

    def _translate_right(self, params: Dict, table_alias: str = None) -> str:
        """Ultimi N caratteri — Oracle/SQLite usano SUBSTR con indice negativo."""
        col = self._qualify_column(params["column"], table_alias)
        lunghezza = params["length"]
        traduzioni = {
            DbType.POSTGRES: f"RIGHT({col}, {lunghezza})",
            DbType.MSSQL: f"RIGHT({col}, {lunghezza})",
            DbType.MYSQL: f"RIGHT({col}, {lunghezza})",
            DbType.ORACLE: f"SUBSTR({col}, -{lunghezza})",
            DbType.SQLITE: f"SUBSTR({col}, -{lunghezza})",
        }
        return self._ottieni_traduzione(traduzioni, "RIGHT")

    def _translate_position(self, params: Dict, table_alias: str = None) -> str:
        """Trova posizione — sintassi diversa per ogni database."""
        col = self._qualify_column(params["column"], table_alias)
        cerca = params["search"]
        traduzioni = {
            DbType.POSTGRES: f"POSITION('{cerca}' IN {col})",
            DbType.MSSQL: f"CHARINDEX('{cerca}', {col})",
            DbType.MYSQL: f"LOCATE('{cerca}', {col})",
            DbType.ORACLE: f"INSTR({col}, '{cerca}')",
            DbType.SQLITE: f"INSTR({col}, '{cerca}')",
        }
        return self._ottieni_traduzione(traduzioni, "POSITION")

    def _translate_ltrim(self, params: Dict, table_alias: str = None) -> str:
        """Rimuovi spazi a sinistra — LTRIM() identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"LTRIM({col})"

    def _translate_rtrim(self, params: Dict, table_alias: str = None) -> str:
        """Rimuovi spazi a destra — RTRIM() identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"RTRIM({col})"

    # =========================================================================
    # FUNZIONI DATA (nuove)
    # =========================================================================

    def _translate_now(self, params: Dict, table_alias: str = None) -> str:
        """Data/ora corrente — sintassi specifica per ogni database."""
        traduzioni = {
            DbType.POSTGRES: "NOW()",
            DbType.MSSQL: "GETDATE()",
            DbType.MYSQL: "NOW()",
            DbType.ORACLE: "SYSDATE",
            DbType.SQLITE: "datetime('now')",
        }
        return self._ottieni_traduzione(traduzioni, "NOW")

    def _translate_current_date(self, params: Dict, table_alias: str = None) -> str:
        """Data corrente — sintassi specifica per ogni database."""
        traduzioni = {
            DbType.POSTGRES: "CURRENT_DATE",
            DbType.MSSQL: "CAST(GETDATE() AS DATE)",
            DbType.MYSQL: "CURDATE()",
            DbType.ORACLE: "TRUNC(SYSDATE)",
            DbType.SQLITE: "date('now')",
        }
        return self._ottieni_traduzione(traduzioni, "CURRENT_DATE")

    def _translate_date_diff(self, params: Dict, table_alias: str = None) -> str:
        """Differenza tra date in giorni."""
        col1 = self._qualify_column(params["column"], table_alias)
        col2 = self._qualify_column(params["column2"], table_alias)
        traduzioni = {
            DbType.POSTGRES: f"({col1}::date - {col2}::date)",
            DbType.MSSQL: f"DATEDIFF(day, {col2}, {col1})",
            DbType.MYSQL: f"DATEDIFF({col1}, {col2})",
            DbType.ORACLE: f"({col1} - {col2})",
            DbType.SQLITE: f"CAST(julianday({col1}) - julianday({col2}) AS INTEGER)",
        }
        return self._ottieni_traduzione(traduzioni, "DATE_DIFF")

    def _translate_date_add(self, params: Dict, table_alias: str = None) -> str:
        """Aggiunta intervallo a data — sintassi molto diversa per ogni DB."""
        col = self._qualify_column(params["column"], table_alias)
        intervallo = params["interval"]
        unita = params.get("unit", "day")
        traduzioni = {
            DbType.POSTGRES: f"{col} + INTERVAL '{intervallo} {unita}'",
            DbType.MSSQL: f"DATEADD({unita}, {intervallo}, {col})",
            DbType.MYSQL: f"DATE_ADD({col}, INTERVAL {intervallo} {unita})",
            DbType.ORACLE: f"{col} + INTERVAL '{intervallo}' {unita}",
            DbType.SQLITE: f"datetime({col}, '+{intervallo} {unita}')",
        }
        return self._ottieni_traduzione(traduzioni, "DATE_ADD")

    def _translate_date_trunc(self, params: Dict, table_alias: str = None) -> str:
        """Tronca data a unita — sintassi molto diversa per ogni DB."""
        col = self._qualify_column(params["column"], table_alias)
        unita = params["unit"].lower()
        # Mappa unita per Oracle TRUNC
        oracle_fmt = {"year": "YYYY", "month": "MM", "week": "IW", "day": "DD"}.get(unita, "MM")
        # Mappa unita per SQLite strftime
        sqlite_fmt = {"year": "%Y-01-01", "month": "%Y-%m-01", "week": "%Y-%m-%d", "day": "%Y-%m-%d"}.get(unita, "%Y-%m-01")
        traduzioni = {
            DbType.POSTGRES: f"DATE_TRUNC('{unita}', {col})",
            DbType.MSSQL: f"DATETRUNC({unita}, {col})",
            DbType.MYSQL: f"DATE_FORMAT({col}, '{{'%Y' if unita == 'year' else '%Y-%m' if unita == 'month' else '%Y-%m-%d'}}-01')" if False else self._mysql_date_trunc(col, unita),
            DbType.ORACLE: f"TRUNC({col}, '{oracle_fmt}')",
            DbType.SQLITE: f"date({col}, 'start of {unita}')" if unita in ("year", "month", "day") else f"date({col})",
        }
        return self._ottieni_traduzione(traduzioni, "DATE_TRUNC")

    def _mysql_date_trunc(self, col: str, unita: str) -> str:
        """Helper MySQL per DATE_TRUNC — MySQL non ha DATE_TRUNC nativo."""
        formati = {
            "year": f"DATE_FORMAT({col}, '%Y-01-01')",
            "month": f"DATE_FORMAT({col}, '%Y-%m-01')",
            "week": f"DATE(DATE_SUB({col}, INTERVAL WEEKDAY({col}) DAY))",
            "day": f"DATE({col})",
        }
        return formati.get(unita, f"DATE({col})")

    def _translate_date_format(self, params: Dict, table_alias: str = None) -> str:
        """Formatta data come stringa — ogni DB ha il suo sistema di formattazione."""
        col = self._qualify_column(params["column"], table_alias)
        fmt = params["format"]
        # Converte formato generico DD/MM/YYYY nei formati specifici per DB
        pg_fmt = fmt.replace("DD", "DD").replace("MM", "MM").replace("YYYY", "YYYY").replace("YY", "YY").replace("HH", "HH24").replace("mm", "MI").replace("ss", "SS")
        mysql_fmt = fmt.replace("YYYY", "%Y").replace("YY", "%y").replace("MM", "%m").replace("DD", "%d").replace("HH", "%H").replace("mm", "%i").replace("ss", "%s")
        mssql_fmt = fmt  # MSSQL FORMAT accetta formati .NET simili
        sqlite_fmt = fmt.replace("YYYY", "%Y").replace("YY", "%y").replace("MM", "%m").replace("DD", "%d").replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
        traduzioni = {
            DbType.POSTGRES: f"TO_CHAR({col}, '{pg_fmt}')",
            DbType.MSSQL: f"FORMAT({col}, '{mssql_fmt}')",
            DbType.MYSQL: f"DATE_FORMAT({col}, '{mysql_fmt}')",
            DbType.ORACLE: f"TO_CHAR({col}, '{pg_fmt}')",
            DbType.SQLITE: f"STRFTIME('{sqlite_fmt}', {col})",
        }
        return self._ottieni_traduzione(traduzioni, "DATE_FORMAT")

    def _translate_extract(self, params: Dict, table_alias: str = None) -> str:
        """Estrazione parte da data — generalizzazione di YEAR/MONTH/DAY."""
        col = self._qualify_column(params["column"], table_alias)
        parte = params["part"]
        sqlite_format_map = {
            "year": "%Y", "month": "%m", "day": "%d",
            "hour": "%H", "minute": "%M", "second": "%S"
        }
        sqlite_fmt = sqlite_format_map.get(parte.lower(), "%Y")
        traduzioni = {
            DbType.POSTGRES: f"EXTRACT({parte} FROM {col})",
            DbType.MSSQL: f"DATEPART({parte}, {col})",
            DbType.MYSQL: f"EXTRACT({parte} FROM {col})",
            DbType.ORACLE: f"EXTRACT({parte} FROM {col})",
            DbType.SQLITE: f"CAST(STRFTIME('{sqlite_fmt}', {col}) AS INTEGER)",
        }
        return self._ottieni_traduzione(traduzioni, "EXTRACT")

    # =========================================================================
    # FUNZIONI CONDIZIONALI (nuove)
    # =========================================================================

    def _translate_coalesce(self, params: Dict, table_alias: str = None) -> str:
        """Primo valore non NULL — COALESCE() identico su tutti i database."""
        valori = params["values"]
        if isinstance(valori, str):
            valori = [v.strip() for v in valori.split(',') if v.strip()]
        valori_q = [self._qualify_column(v, table_alias) for v in valori]
        return f"COALESCE({', '.join(valori_q)})"

    def _translate_nullif(self, params: Dict, table_alias: str = None) -> str:
        """NULL se uguale — NULLIF() identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        valore = params["value"]
        return f"NULLIF({col}, '{valore}')"

    def _translate_case_when(self, params: Dict, table_alias: str = None) -> str:
        """Espressione condizionale CASE WHEN — identica su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        operatore = params.get("operator", "=")
        confronto = params["compare_value"]
        valore_vero = params["then_value"]
        valore_falso = params.get("else_value", "NULL")
        return f"CASE WHEN {col} {operatore} '{confronto}' THEN '{valore_vero}' ELSE '{valore_falso}' END"

    def _translate_iif(self, params: Dict, table_alias: str = None) -> str:
        """If inline — MSSQL/SQLite hanno IIF, altri usano CASE WHEN."""
        col = self._qualify_column(params["column"], table_alias)
        operatore = params.get("operator", "=")
        confronto = params["compare_value"]
        valore_vero = params["true_value"]
        valore_falso = params["false_value"]
        condizione = f"{col} {operatore} '{confronto}'"
        traduzioni = {
            DbType.POSTGRES: f"CASE WHEN {condizione} THEN '{valore_vero}' ELSE '{valore_falso}' END",
            DbType.MSSQL: f"IIF({condizione}, '{valore_vero}', '{valore_falso}')",
            DbType.MYSQL: f"IF({condizione}, '{valore_vero}', '{valore_falso}')",
            DbType.ORACLE: f"CASE WHEN {condizione} THEN '{valore_vero}' ELSE '{valore_falso}' END",
            DbType.SQLITE: f"IIF({condizione}, '{valore_vero}', '{valore_falso}')",
        }
        return self._ottieni_traduzione(traduzioni, "IIF")

    # =========================================================================
    # FUNZIONI CONVERSIONE / CAST (nuove)
    # =========================================================================

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

    # =========================================================================
    # HELPER INTERNO
    # =========================================================================

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


# =============================================================================
# FUNZIONI HELPER PUBBLICHE
# =============================================================================

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

    # Verifica se il risultato è una geometria (per wrappare con conversione WKT)
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
        - SQL Server: (...).STAsText()
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

    # Valore letterale (numero, etc.)
    if not isinstance(expression, dict):
        return str(expression)

    # Espressione funzione: risolvi parametri ricorsivamente
    nome_funzione = expression.get("function")
    parametri = expression.get("params", {})

    parametri_risolti = {}
    for chiave, valore in parametri.items():
        if isinstance(valore, dict) and "function" in valore:
            # Parametro è a sua volta un'espressione funzione → ricorsione
            parametri_risolti[chiave] = _translate_expression(traduttore, valore, table_alias)
        else:
            parametri_risolti[chiave] = valore

    return traduttore.translate(nome_funzione, parametri_risolti, table_alias)
