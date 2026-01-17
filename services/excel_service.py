import pandas as pd
from pathlib import Path
from typing import Dict, List
from models import TableCatalog, ColumnInfo
from config import settings

class ExcelService:
    """Servizio per parsing Excel e gestione catalogo tabelle"""
    
    def __init__(self):
        self.CATALOG_PATH = settings.CATALOG_DIR / "tables.json"
    
    def parse_excel(self, file_path: str) -> TableCatalog:
        """
        Legge Excel con colonne TABLE_NAME, COLUMN_NAME e opzionalmente COLUMN_TYPE
        e crea il catalogo tabelle
        
        Args:
            file_path: Path del file Excel
            
        Returns:
            TableCatalog con tutte le tabelle e colonne (con tipi)
            
        Formato Excel atteso:
            TABLE_NAME | COLUMN_NAME | COLUMN_TYPE (opzionale)
            users      | id          | int
            users      | name        | str
            users      | geom        | geometry:point
        """
        # Leggi Excel
        df = pd.read_excel(file_path)
        
        # Verifica colonne richieste
        required_cols = {"TABLE_NAME", "COLUMN_NAME"}
        if not required_cols.issubset(df.columns):
            raise ValueError(f"Excel deve avere colonne: {required_cols}")
        
        # Verifica se c'è la colonna tipo (opzionale)
        has_type_col = "COLUMN_TYPE" in df.columns
        
        # Raggruppa colonne per tabella
        tables: Dict[str, List[ColumnInfo]] = {}
        for table_name, group in df.groupby("TABLE_NAME"):
            columns = []
            for _, row in group.iterrows():
                col_name = row["COLUMN_NAME"]
                if pd.isna(col_name):
                    continue
                
                # Tipo: prendi da Excel se presente, altrimenti default 'str'
                col_type = "str"
                if has_type_col and not pd.isna(row.get("COLUMN_TYPE")):
                    col_type = str(row["COLUMN_TYPE"]).lower().strip()
                    # Valida il tipo
                    col_type = self._normalize_type(col_type)
                
                columns.append(ColumnInfo(name=str(col_name), type=col_type))
            
            tables[table_name] = columns
        
        return TableCatalog(tables=tables)
    
    def _normalize_type(self, type_str: str) -> str:
        """
        Normalizza e valida il tipo colonna.
        Accetta varianti comuni e le converte nel formato standard.
        
        Args:
            type_str: Tipo come stritto nell'Excel
            
        Returns:
            Tipo normalizzato (int, real, str, bool, date, geometry, geometry:*)
        """
        type_lower = type_str.lower().strip()
        
        # Mapping varianti comuni
        type_aliases = {
            # Integer
            'integer': 'int', 'bigint': 'int', 'smallint': 'int', 'number': 'int',
            # Real
            'float': 'real', 'double': 'real', 'decimal': 'real', 'numeric': 'real',
            # String
            'string': 'str', 'varchar': 'str', 'text': 'str', 'char': 'str',
            # Boolean
            'boolean': 'bool', 'bit': 'bool',
            # Date
            'datetime': 'date', 'timestamp': 'date', 'time': 'date',
            # Geometry
            'point': 'geometry:point',
            'linestring': 'geometry:line', 'line': 'geometry:line',
            'polygon': 'geometry:polygon',
            'multipolygon': 'geometry:multipolygon',
            'multipoint': 'geometry:multipoint',
            'multilinestring': 'geometry:multiline', 'multiline': 'geometry:multiline',
            'geom': 'geometry',
        }
        
        # Se è già un tipo valido, ritornalo
        valid_types = {
            'int', 'real', 'str', 'bool', 'date', 
            'geometry', 'geometry:point', 'geometry:line', 'geometry:polygon',
            'geometry:multipolygon', 'geometry:multipoint', 'geometry:multiline'
        }
        
        if type_lower in valid_types:
            return type_lower
        
        # Cerca nelle alias
        if type_lower in type_aliases:
            return type_aliases[type_lower]
        
        # Default a string per tipi sconosciuti
        return 'str'
    
    def save_catalog(self, catalog: TableCatalog) -> None:
        """Salva catalogo su disco"""
        with open(self.CATALOG_PATH, "w", encoding="utf-8") as f:
            f.write(catalog.model_dump_json(indent=2))
    
    def load_catalog(self) -> TableCatalog:
        """Carica catalogo da disco"""
        if not self.CATALOG_PATH.exists():
            return TableCatalog(tables={})
        
        with open(self.CATALOG_PATH, "r", encoding="utf-8") as f:
            return TableCatalog.model_validate_json(f.read())
    
    def get_table_columns(self, table_name: str) -> List[ColumnInfo]:
        """
        Ottieni colonne di una tabella specifica con i loro tipi.
        
        Args:
            table_name: Nome della tabella
            
        Returns:
            Lista di ColumnInfo con name e type
        """
        catalog = self.load_catalog()
        return catalog.tables.get(table_name, [])