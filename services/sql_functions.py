"""
sql_functions.py
Definizione funzioni geometriche astratte e traduttore SQL per ogni tipo di database.

Le funzioni sono definite in modo "agnostico" e tradotte nel dialetto SQL specifico.
"""

from typing import Dict, List, Optional, Any
from enum import Enum
from dataclasses import dataclass


class DbType(Enum):
    """Tipi di database supportati"""
    POSTGRES = "postgres"
    MSSQL = "mssql"
    MYSQL = "mysql"
    ORACLE = "oracle"
    SQLITE = "sqlite"


class FunctionCategory(Enum):
    """Categorie di funzioni"""
    GEOMETRIC = "geometric"
    NUMERIC = "numeric"
    STRING = "string"
    DATE = "date"


@dataclass
class FunctionParam:
    """Definizione parametro di una funzione"""
    name: str
    param_type: str  # "column", "number", "string", "srid"
    required: bool = True
    default: Any = None
    description: str = ""


@dataclass
class FunctionDef:
    """Definizione di una funzione astratta"""
    name: str
    label: str
    category: FunctionCategory
    description: str
    input_type: Optional[str]  # Tipo colonna richiesto (es. "geometry", "numeric", None per any)
    output_type: str  # Tipo risultato
    params: List[FunctionParam]
    

# ============================================================
# CATALOGO FUNZIONI DISPONIBILI
# ============================================================

FUNCTION_CATALOG: Dict[str, FunctionDef] = {
    # --- GEOMETRICHE ---
    "CENTROID": FunctionDef(
        name="CENTROID",
        label="Centroide",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola il centro geometrico",
        input_type="geometry",
        output_type="geometry:point",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica")
        ]
    ),
    "BUFFER": FunctionDef(
        name="BUFFER",
        label="Buffer",
        category=FunctionCategory.GEOMETRIC,
        description="Crea un'area buffer attorno alla geometria",
        input_type="geometry",
        output_type="geometry:polygon",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica"),
            FunctionParam("distance", "number", True, None, "Distanza in unità della proiezione")
        ]
    ),
    "TRANSFORM": FunctionDef(
        name="TRANSFORM",
        label="Riproiezione",
        category=FunctionCategory.GEOMETRIC,
        description="Trasforma la geometria in un altro sistema di riferimento",
        input_type="geometry",
        output_type="geometry",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica"),
            FunctionParam("srid", "srid", True, None, "SRID target (es. 4326, 32632)")
        ]
    ),
    "AREA": FunctionDef(
        name="AREA",
        label="Area",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola l'area della geometria",
        input_type="geometry:polygon",
        output_type="real",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica (polygon)")
        ]
    ),
    "LENGTH": FunctionDef(
        name="LENGTH",
        label="Lunghezza",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola la lunghezza della geometria",
        input_type="geometry:line",
        output_type="real",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica (line)")
        ]
    ),
    "PERIMETER": FunctionDef(
        name="PERIMETER",
        label="Perimetro",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola il perimetro della geometria",
        input_type="geometry:polygon",
        output_type="real",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica (polygon)")
        ]
    ),
    "SIMPLIFY": FunctionDef(
        name="SIMPLIFY",
        label="Semplifica",
        category=FunctionCategory.GEOMETRIC,
        description="Semplifica la geometria riducendo i vertici",
        input_type="geometry",
        output_type="geometry",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica"),
            FunctionParam("tolerance", "number", True, None, "Tolleranza di semplificazione")
        ]
    ),
    "CONVEX_HULL": FunctionDef(
        name="CONVEX_HULL",
        label="Inviluppo convesso",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola l'inviluppo convesso della geometria",
        input_type="geometry",
        output_type="geometry:polygon",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica")
        ]
    ),
    "ENVELOPE": FunctionDef(
        name="ENVELOPE",
        label="Bounding Box",
        category=FunctionCategory.GEOMETRIC,
        description="Calcola il rettangolo di delimitazione",
        input_type="geometry",
        output_type="geometry:polygon",
        params=[
            FunctionParam("column", "column", True, None, "Colonna geometrica")
        ]
    ),
    
    # --- NUMERICHE ---
    "ROUND": FunctionDef(
        name="ROUND",
        label="Arrotonda",
        category=FunctionCategory.NUMERIC,
        description="Arrotonda al numero di decimali specificato",
        input_type="real",
        output_type="real",
        params=[
            FunctionParam("column", "column", True, None, "Colonna numerica"),
            FunctionParam("decimals", "number", False, 0, "Numero di decimali")
        ]
    ),
    "ABS": FunctionDef(
        name="ABS",
        label="Valore assoluto",
        category=FunctionCategory.NUMERIC,
        description="Restituisce il valore assoluto",
        input_type="real",
        output_type="real",
        params=[
            FunctionParam("column", "column", True, None, "Colonna numerica")
        ]
    ),
    "CEIL": FunctionDef(
        name="CEIL",
        label="Arrotonda per eccesso",
        category=FunctionCategory.NUMERIC,
        description="Arrotonda all'intero superiore",
        input_type="real",
        output_type="int",
        params=[
            FunctionParam("column", "column", True, None, "Colonna numerica")
        ]
    ),
    "FLOOR": FunctionDef(
        name="FLOOR",
        label="Arrotonda per difetto",
        category=FunctionCategory.NUMERIC,
        description="Arrotonda all'intero inferiore",
        input_type="real",
        output_type="int",
        params=[
            FunctionParam("column", "column", True, None, "Colonna numerica")
        ]
    ),
    
    # --- STRINGA ---
    "UPPER": FunctionDef(
        name="UPPER",
        label="Maiuscolo",
        category=FunctionCategory.STRING,
        description="Converte in maiuscolo",
        input_type="str",
        output_type="str",
        params=[
            FunctionParam("column", "column", True, None, "Colonna testo")
        ]
    ),
    "LOWER": FunctionDef(
        name="LOWER",
        label="Minuscolo",
        category=FunctionCategory.STRING,
        description="Converte in minuscolo",
        input_type="str",
        output_type="str",
        params=[
            FunctionParam("column", "column", True, None, "Colonna testo")
        ]
    ),
    "TRIM": FunctionDef(
        name="TRIM",
        label="Rimuovi spazi",
        category=FunctionCategory.STRING,
        description="Rimuove spazi iniziali e finali",
        input_type="str",
        output_type="str",
        params=[
            FunctionParam("column", "column", True, None, "Colonna testo")
        ]
    ),
    "CONCAT": FunctionDef(
        name="CONCAT",
        label="Concatena",
        category=FunctionCategory.STRING,
        description="Concatena più valori",
        input_type=None,
        output_type="str",
        params=[
            FunctionParam("values", "column_list", True, None, "Colonne da concatenare"),
            FunctionParam("separator", "string", False, "", "Separatore")
        ]
    ),
    "SUBSTRING": FunctionDef(
        name="SUBSTRING",
        label="Sottostringa",
        category=FunctionCategory.STRING,
        description="Estrae una porzione di testo",
        input_type="str",
        output_type="str",
        params=[
            FunctionParam("column", "column", True, None, "Colonna testo"),
            FunctionParam("start", "number", True, 1, "Posizione iniziale (da 1)"),
            FunctionParam("length", "number", False, None, "Lunghezza (opzionale)")
        ]
    ),
    
    # --- DATA ---
    "YEAR": FunctionDef(
        name="YEAR",
        label="Estrai Anno",
        category=FunctionCategory.DATE,
        description="Estrae l'anno da una data",
        input_type="date",
        output_type="int",
        params=[
            FunctionParam("column", "column", True, None, "Colonna data")
        ]
    ),
    "MONTH": FunctionDef(
        name="MONTH",
        label="Estrai Mese",
        category=FunctionCategory.DATE,
        description="Estrae il mese da una data",
        input_type="date",
        output_type="int",
        params=[
            FunctionParam("column", "column", True, None, "Colonna data")
        ]
    ),
    "DAY": FunctionDef(
        name="DAY",
        label="Estrai Giorno",
        category=FunctionCategory.DATE,
        description="Estrae il giorno da una data",
        input_type="date",
        output_type="int",
        params=[
            FunctionParam("column", "column", True, None, "Colonna data")
        ]
    ),
    
    # --- MATEMATICHE ---
    "MATH": FunctionDef(
        name="MATH",
        label="Operazione Matematica",
        category=FunctionCategory.NUMERIC,
        description="Esegue operazioni aritmetiche tra colonne numeriche",
        input_type="real",
        output_type="real",
        params=[
            FunctionParam("column1", "column_numeric", True, None, "Prima colonna numerica"),
            FunctionParam("operator", "operator", True, None, "Operatore (+, -, *, /)"),
            FunctionParam("column2", "column_numeric", False, None, "Seconda colonna numerica (opzionale)"),
            FunctionParam("value", "number", False, None, "Valore numerico (alternativo a column2)")
        ]
    ),
}


# ============================================================
# TRADUTTORI SQL PER DATABASE
# ============================================================

class SqlFunctionTranslator:
    """Traduce funzioni astratte in SQL specifico per ogni database"""
    
    def __init__(self, db_type: DbType):
        self.db_type = db_type
    
    def translate(self, function_name: str, params: Dict[str, Any], table_alias: str = None) -> str:
        """
        Traduce una funzione astratta in SQL.
        
        Args:
            function_name: Nome della funzione (es. "CENTROID")
            params: Dizionario parametri (es. {"column": "geom", "distance": 100})
            table_alias: Alias della tabella per qualificare le colonne
            
        Returns:
            Stringa SQL per quella funzione
        """
        method_name = f"_translate_{function_name.lower()}"
        
        if hasattr(self, method_name):
            return getattr(self, method_name)(params, table_alias)
        else:
            raise ValueError(f"Funzione {function_name} non supportata per {self.db_type.value}")
    
    def _qualify_column(self, column: str, table_alias: str = None) -> str:
        """
        Qualifica una colonna con l'alias della tabella se presente.
        NON qualifica se column è già un'espressione SQL (contiene parentesi o è già qualificata).
        """
        # Se column non è una stringa, restituisci come stringa
        if not isinstance(column, str):
            return str(column)
        
        # Se contiene parentesi, è già un'espressione SQL (es. ST_Centroid(...))
        if '(' in column or ')' in column:
            return column
        
        # Se contiene già un punto e non è solo table.column, potrebbe essere già qualificata
        # o essere un'espressione complessa - non modificare
        if '.' in column and column.count('.') > 0:
            # Controlla se sembra già un nome qualificato semplice (es. "table.column")
            parts = column.split('.')
            if len(parts) == 2 and parts[0] and parts[1]:
                # È già qualificato, non aggiungere altro
                return column
        
        # Qualifica solo nomi colonna semplici
        if table_alias:
            return f"{table_alias}.{column}"
        return column
    
    # ============================================================
    # FUNZIONI GEOMETRICHE
    # ============================================================
    
    def _translate_centroid(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.POSTGRES:
            return f"ST_Centroid({col})"
        elif self.db_type == DbType.MSSQL:
            return f"{col}.STCentroid()"
        elif self.db_type == DbType.MYSQL:
            return f"ST_Centroid({col})"
        elif self.db_type == DbType.ORACLE:
            return f"SDO_GEOM.SDO_CENTROID({col}, 0.005)"
        elif self.db_type == DbType.SQLITE:
            return f"Centroid({col})"  # SpatiaLite
        else:
            raise ValueError(f"CENTROID non supportato per {self.db_type.value}")
    
    def _translate_buffer(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        distance = params["distance"]
        
        if self.db_type == DbType.POSTGRES:
            return f"ST_Buffer({col}, {distance})"
        elif self.db_type == DbType.MSSQL:
            return f"{col}.STBuffer({distance})"
        elif self.db_type == DbType.MYSQL:
            return f"ST_Buffer({col}, {distance})"
        elif self.db_type == DbType.ORACLE:
            # Oracle SDO_BUFFER richiede tolleranza come terzo parametro
            return f"SDO_GEOM.SDO_BUFFER({col}, {distance}, 0.005)"
        elif self.db_type == DbType.SQLITE:
            return f"Buffer({col}, {distance})"
        else:
            raise ValueError(f"BUFFER non supportato per {self.db_type.value}")
    
    def _translate_transform(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        srid = params["srid"]
        
        if self.db_type == DbType.POSTGRES:
            return f"ST_Transform({col}, {srid})"
        elif self.db_type == DbType.MSSQL:
            # SQL Server non ha ST_Transform diretto, serve una funzione custom
            return f"{col}.STTransform({srid})"  # Richiede estensione
        elif self.db_type == DbType.MYSQL:
            return f"ST_Transform({col}, {srid})"  # MySQL 8.0+
        elif self.db_type == DbType.ORACLE:
            return f"SDO_CS.TRANSFORM({col}, {srid})"
        elif self.db_type == DbType.SQLITE:
            return f"Transform({col}, {srid})"
        else:
            raise ValueError(f"TRANSFORM non supportato per {self.db_type.value}")
    
    def _translate_area(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.POSTGRES:
            return f"ST_Area({col})"
        elif self.db_type == DbType.MSSQL:
            return f"{col}.STArea()"
        elif self.db_type == DbType.MYSQL:
            return f"ST_Area({col})"
        elif self.db_type == DbType.ORACLE:
            return f"SDO_GEOM.SDO_AREA({col}, 0.005)"
        elif self.db_type == DbType.SQLITE:
            return f"Area({col})"
        else:
            raise ValueError(f"AREA non supportato per {self.db_type.value}")
    
    def _translate_length(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.POSTGRES:
            return f"ST_Length({col})"
        elif self.db_type == DbType.MSSQL:
            return f"{col}.STLength()"
        elif self.db_type == DbType.MYSQL:
            return f"ST_Length({col})"
        elif self.db_type == DbType.ORACLE:
            return f"SDO_GEOM.SDO_LENGTH({col}, 0.005)"
        elif self.db_type == DbType.SQLITE:
            return f"GLength({col})"  # SpatiaLite usa GLength
        else:
            raise ValueError(f"LENGTH non supportato per {self.db_type.value}")
    
    def _translate_perimeter(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.POSTGRES:
            return f"ST_Perimeter({col})"
        elif self.db_type == DbType.MSSQL:
            # SQL Server non ha STPerimeter, usa STLength sul boundary
            return f"{col}.STBoundary().STLength()"
        elif self.db_type == DbType.MYSQL:
            # MySQL non ha ST_Perimeter diretto
            return f"ST_Length(ST_ExteriorRing({col}))"
        elif self.db_type == DbType.ORACLE:
            # Oracle: SDO_GEOM.SDO_LENGTH su un poligono restituisce il perimetro
            return f"SDO_GEOM.SDO_LENGTH({col}, 0.005)"
        elif self.db_type == DbType.SQLITE:
            return f"Perimeter({col})"
        else:
            raise ValueError(f"PERIMETER non supportato per {self.db_type.value}")
    
    def _translate_simplify(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        tolerance = params["tolerance"]
        
        if self.db_type == DbType.POSTGRES:
            return f"ST_Simplify({col}, {tolerance})"
        elif self.db_type == DbType.MSSQL:
            return f"{col}.Reduce({tolerance})"
        elif self.db_type == DbType.MYSQL:
            return f"ST_Simplify({col}, {tolerance})"
        elif self.db_type == DbType.ORACLE:
            return f"SDO_UTIL.SIMPLIFY({col}, {tolerance})"
        elif self.db_type == DbType.SQLITE:
            return f"Simplify({col}, {tolerance})"
        else:
            raise ValueError(f"SIMPLIFY non supportato per {self.db_type.value}")
    
    def _translate_convex_hull(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.POSTGRES:
            return f"ST_ConvexHull({col})"
        elif self.db_type == DbType.MSSQL:
            return f"{col}.STConvexHull()"
        elif self.db_type == DbType.MYSQL:
            return f"ST_ConvexHull({col})"
        elif self.db_type == DbType.ORACLE:
            return f"SDO_GEOM.SDO_CONVEXHULL({col}, 0.005)"
        elif self.db_type == DbType.SQLITE:
            return f"ConvexHull({col})"
        else:
            raise ValueError(f"CONVEX_HULL non supportato per {self.db_type.value}")
    
    def _translate_envelope(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.POSTGRES:
            return f"ST_Envelope({col})"
        elif self.db_type == DbType.MSSQL:
            return f"{col}.STEnvelope()"
        elif self.db_type == DbType.MYSQL:
            return f"ST_Envelope({col})"
        elif self.db_type == DbType.ORACLE:
            return f"SDO_GEOM.SDO_MBR({col})"
        elif self.db_type == DbType.SQLITE:
            return f"Envelope({col})"
        else:
            raise ValueError(f"ENVELOPE non supportato per {self.db_type.value}")
    
    # ============================================================
    # FUNZIONI NUMERICHE
    # ============================================================
    
    def _translate_round(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        decimals = params.get("decimals")
        
        # Gestisci decimals None o non numerico
        if decimals is None or decimals == '':
            decimals = 0
        else:
            try:
                decimals = int(decimals)
            except (ValueError, TypeError):
                decimals = 0
        
        # PostgreSQL richiede cast a numeric per ROUND con decimali
        if self.db_type == DbType.POSTGRES:
            return f"ROUND({col}::numeric, {decimals})"
        else:
            return f"ROUND({col}, {decimals})"
    
    def _translate_abs(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        return f"ABS({col})"
    
    def _translate_ceil(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.MSSQL:
            return f"CEILING({col})"
        else:
            return f"CEIL({col})"
    
    def _translate_floor(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        return f"FLOOR({col})"
    
    # ============================================================
    # FUNZIONI STRINGA
    # ============================================================
    
    def _translate_upper(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        return f"UPPER({col})"
    
    def _translate_lower(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        return f"LOWER({col})"
    
    def _translate_trim(self, params: Dict, table_alias: str = None) -> str:
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.MSSQL:
            return f"LTRIM(RTRIM({col}))"
        else:
            return f"TRIM({col})"
    
    def _translate_concat(self, params: Dict, table_alias: str = None) -> str:
        values = params["values"]  # Lista di colonne
        separator = params.get("separator", "")
        
        qualified_values = [self._qualify_column(v, table_alias) for v in values]
        
        if self.db_type == DbType.POSTGRES:
            if separator:
                return f"CONCAT_WS('{separator}', {', '.join(qualified_values)})"
            else:
                return f"CONCAT({', '.join(qualified_values)})"
        elif self.db_type == DbType.MSSQL:
            if separator:
                return f"CONCAT_WS('{separator}', {', '.join(qualified_values)})"
            else:
                return f"CONCAT({', '.join(qualified_values)})"
        elif self.db_type == DbType.MYSQL:
            if separator:
                return f"CONCAT_WS('{separator}', {', '.join(qualified_values)})"
            else:
                return f"CONCAT({', '.join(qualified_values)})"
        elif self.db_type == DbType.ORACLE:
            # Oracle non ha CONCAT_WS nativo
            if separator:
                parts = [f"{v} || '{separator}'" for v in qualified_values[:-1]]
                parts.append(qualified_values[-1])
                return " || ".join(parts)
            else:
                return " || ".join(qualified_values)
        elif self.db_type == DbType.SQLITE:
            if separator:
                parts = [f"{v} || '{separator}'" for v in qualified_values[:-1]]
                parts.append(qualified_values[-1])
                return " || ".join(parts)
            else:
                return " || ".join(qualified_values)
        else:
            return f"CONCAT({', '.join(qualified_values)})"
    
    def _translate_substring(self, params: Dict, table_alias: str = None) -> str:
        """Estrae una sottostringa"""
        col = self._qualify_column(params["column"], table_alias)
        start = params.get("start", 1)
        length = params.get("length")
        
        if self.db_type == DbType.POSTGRES:
            if length:
                return f"SUBSTRING({col} FROM {start} FOR {length})"
            else:
                return f"SUBSTRING({col} FROM {start})"
        elif self.db_type == DbType.MSSQL:
            if length:
                return f"SUBSTRING({col}, {start}, {length})"
            else:
                return f"SUBSTRING({col}, {start}, LEN({col}))"
        elif self.db_type == DbType.MYSQL:
            if length:
                return f"SUBSTRING({col}, {start}, {length})"
            else:
                return f"SUBSTRING({col}, {start})"
        elif self.db_type == DbType.ORACLE:
            if length:
                return f"SUBSTR({col}, {start}, {length})"
            else:
                return f"SUBSTR({col}, {start})"
        elif self.db_type == DbType.SQLITE:
            if length:
                return f"SUBSTR({col}, {start}, {length})"
            else:
                return f"SUBSTR({col}, {start})"
        else:
            return f"SUBSTRING({col}, {start}, {length or 'NULL'})"
    
    # ============================================================
    # FUNZIONI DATA
    # ============================================================
    
    def _translate_year(self, params: Dict, table_alias: str = None) -> str:
        """Estrae l'anno da una data"""
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.POSTGRES:
            return f"EXTRACT(YEAR FROM {col})"
        elif self.db_type == DbType.MSSQL:
            return f"YEAR({col})"
        elif self.db_type == DbType.MYSQL:
            return f"YEAR({col})"
        elif self.db_type == DbType.ORACLE:
            return f"EXTRACT(YEAR FROM {col})"
        elif self.db_type == DbType.SQLITE:
            return f"CAST(STRFTIME('%Y', {col}) AS INTEGER)"
        else:
            return f"EXTRACT(YEAR FROM {col})"
    
    def _translate_month(self, params: Dict, table_alias: str = None) -> str:
        """Estrae il mese da una data"""
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.POSTGRES:
            return f"EXTRACT(MONTH FROM {col})"
        elif self.db_type == DbType.MSSQL:
            return f"MONTH({col})"
        elif self.db_type == DbType.MYSQL:
            return f"MONTH({col})"
        elif self.db_type == DbType.ORACLE:
            return f"EXTRACT(MONTH FROM {col})"
        elif self.db_type == DbType.SQLITE:
            return f"CAST(STRFTIME('%m', {col}) AS INTEGER)"
        else:
            return f"EXTRACT(MONTH FROM {col})"
    
    def _translate_day(self, params: Dict, table_alias: str = None) -> str:
        """Estrae il giorno da una data"""
        col = self._qualify_column(params["column"], table_alias)
        
        if self.db_type == DbType.POSTGRES:
            return f"EXTRACT(DAY FROM {col})"
        elif self.db_type == DbType.MSSQL:
            return f"DAY({col})"
        elif self.db_type == DbType.MYSQL:
            return f"DAY({col})"
        elif self.db_type == DbType.ORACLE:
            return f"EXTRACT(DAY FROM {col})"
        elif self.db_type == DbType.SQLITE:
            return f"CAST(STRFTIME('%d', {col}) AS INTEGER)"
        else:
            return f"EXTRACT(DAY FROM {col})"
    
    def _translate_math(self, params: Dict, table_alias: str = None) -> str:
        """
        Esegue operazioni matematiche tra colonne numeriche.
        
        Supporta:
        - column1 + column2
        - column1 - column2
        - column1 * column2
        - column1 / column2
        - column1 + value (numero fisso)
        """
        col1 = self._qualify_column(params["column1"], table_alias)
        operator = params.get("operator") or "+"
        
        # Valida operatore
        valid_operators = {'+', '-', '*', '/'}
        if operator not in valid_operators:
            raise ValueError(f"Operatore non valido: {operator}. Usa: +, -, *, /")
        
        # Determina il secondo operando (colonna o valore)
        col2 = params.get("column2")
        value = params.get("value")
        
        if col2:
            second_operand = self._qualify_column(col2, table_alias)
        elif value is not None:
            second_operand = str(value)
        else:
            raise ValueError("Specificare column2 o value per l'operazione matematica")
        
        # Gestione divisione per zero
        if operator == '/':
            if self.db_type == DbType.POSTGRES:
                return f"({col1} / NULLIF({second_operand}, 0))"
            elif self.db_type == DbType.MSSQL:
                return f"CASE WHEN {second_operand} = 0 THEN NULL ELSE {col1} / {second_operand} END"
            elif self.db_type == DbType.MYSQL:
                return f"IF({second_operand} = 0, NULL, {col1} / {second_operand})"
            elif self.db_type == DbType.ORACLE:
                return f"CASE WHEN {second_operand} = 0 THEN NULL ELSE {col1} / {second_operand} END"
            else:
                return f"({col1} / NULLIF({second_operand}, 0))"
        
        return f"({col1} {operator} {second_operand})"


# ============================================================
# FUNZIONI HELPER
# ============================================================

def get_function_catalog() -> Dict[str, dict]:
    """Restituisce il catalogo funzioni in formato serializzabile per il frontend"""
    result = {}
    for name, func_def in FUNCTION_CATALOG.items():
        result[name] = {
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
    return result


def get_translator(db_type: str) -> SqlFunctionTranslator:
    """Factory per ottenere il traduttore corretto"""
    return SqlFunctionTranslator(DbType(db_type))


def translate_calculated_column(
    db_type: str,
    column_name: str,
    expression: dict,
    table_alias: str = None
) -> str:
    """
    Traduce una definizione di colonna calcolata in SQL.
    
    Args:
        db_type: Tipo database ("postgres", "mssql", etc.)
        column_name: Nome della colonna risultato
        expression: Definizione espressione, es:
            {
                "function": "BUFFER",
                "params": {"column": "geom", "distance": 100}
            }
            oppure espressione annidata:
            {
                "function": "BUFFER",
                "params": {
                    "column": {
                        "function": "CENTROID",
                        "params": {"column": "geom"}
                    },
                    "distance": 100
                }
            }
        table_alias: Alias tabella
        
    Returns:
        SQL string, es: "ST_AsText(ST_Buffer(ST_Centroid(t.geom), 100)) AS buffer_col"
    """
    translator = get_translator(db_type)
    sql_expr = _translate_expression(translator, expression, table_alias)
    
    # Determina se l'output è una geometria guardando la funzione più esterna
    function_name = expression.get("function") if isinstance(expression, dict) else None
    output_is_geometry = False
    
    if function_name and function_name in FUNCTION_CATALOG:
        output_type = FUNCTION_CATALOG[function_name].output_type
        output_is_geometry = output_type.startswith("geometry")
    
    # Se l'output è geometria, wrappa con ST_AsText per uniformità WKT
    if output_is_geometry:
        sql_expr = _wrap_geometry_as_wkt(db_type, sql_expr)
    
    return f"{sql_expr} AS {column_name}"


def _wrap_geometry_as_wkt(db_type: str, geom_expr: str) -> str:
    """
    Wrappa un'espressione geometrica con la funzione per convertirla in WKT.
    
    Args:
        db_type: Tipo database
        geom_expr: Espressione SQL che produce una geometria
        
    Returns:
        Espressione SQL che produce WKT
    """
    wkt_functions = {
        'postgres': f"ST_AsText({geom_expr})",
        'mssql': f"({geom_expr}).STAsText()",
        'mysql': f"ST_AsText({geom_expr})",
        'oracle': f"SDO_UTIL.TO_WKTGEOMETRY({geom_expr})",
        'sqlite': f"AsText({geom_expr})",
    }
    
    return wkt_functions.get(db_type, f"ST_AsText({geom_expr})")


def _translate_expression(
    translator: SqlFunctionTranslator,
    expression: dict,
    table_alias: str = None
) -> str:
    """Traduce ricorsivamente un'espressione (supporta nesting)"""
    
    if isinstance(expression, str):
        # È un nome colonna semplice
        if table_alias:
            return f"{table_alias}.{expression}"
        return expression
    
    if not isinstance(expression, dict):
        # È un valore letterale (numero, etc.)
        return str(expression)
    
    function_name = expression.get("function")
    params = expression.get("params", {})
    
    # Risolvi parametri annidati ricorsivamente
    resolved_params = {}
    for key, value in params.items():
        if isinstance(value, dict) and "function" in value:
            # È un'espressione annidata
            resolved_params[key] = _translate_expression(translator, value, table_alias)
        else:
            resolved_params[key] = value
    
    return translator.translate(function_name, resolved_params, table_alias)