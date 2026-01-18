"""
Configurazione centralizzata dell'applicazione.

Tutti i path e le costanti sono definiti qui.
Per modificare, cambia direttamente i valori in questo file.

USO:
    from config import settings
    
    # Accedi ai path
    projects_dir = settings.PROJECTS_DIR
    
    # Ottieni path per un progetto specifico
    output_dir = settings.get_project_output_dir("my_project_id")
"""

from pathlib import Path
from typing import List


class Settings:
    """Configurazione centralizzata dell'applicazione."""
    
    # ==================== DIRECTORIES ====================
    # Modifica questi valori per cambiare i path
    
    # Directory base per tutti i dati
    DATA_DIR: Path = Path("./data")
    
    # ==================== DATABASE ====================
    
    # Timeout connessione database (secondi)
    DB_CONNECT_TIMEOUT: int = 10
    
    # Timeout esecuzione query (secondi)
    DB_QUERY_TIMEOUT: int = 30
    
    # Tipi database supportati
    SUPPORTED_DB_TYPES: List[str] = ["postgres", "mssql", "mysql", "oracle", "sqlite"]
    
    # ==================== API ====================
    
    # Timeout chiamate API esterne (secondi)
    API_TIMEOUT: int = 30
    
    # Rate limit default per API calls (richieste/secondo)
    API_DEFAULT_RATE_LIMIT: int = 5
    
    # ==================== OUTPUT ====================
    
    # Formati output supportati
    SUPPORTED_OUTPUT_FORMATS: List[str] = ["csv", "json", "xlsx", "parquet", "geojson", "gpkg", "shp", "gdb"]
    
    # Formato output default
    DEFAULT_OUTPUT_FORMAT: str = "csv"
    
    # ==================== FILE ESTERNI ====================
    
    # Formati file supportati per import
    SUPPORTED_FILE_FORMATS: List[str] = ["csv", "xlsx", "json", "geojson", "gpkg", "shp", "gdb"]
    
    # Dimensione massima file upload (100 MB)
    MAX_FILE_SIZE: int = 100 * 1024 * 1024
    
    # ==================== PROGETTI ====================
    
    # Tipi progetto supportati
    SUPPORTED_PROJECT_TYPES: List[str] = ["estrai", "manipola", "arricchisci", "salva"]
    
    # ==================== LIMITI ====================
    
    # Lunghezza massima nome progetto
    MAX_PROJECT_NAME_LENGTH: int = 100
    
    # Lunghezza massima nome connessione
    MAX_CONNECTION_NAME_LENGTH: int = 100
    
    # Numero massimo righe preview
    MAX_PREVIEW_ROWS: int = 1000
    
    # ==================== DIRECTORY PROPERTIES ====================
    
    @property
    def PROJECTS_DIR(self) -> Path:
        """Directory progetti"""
        path = self.DATA_DIR / "projects"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def CONNECTIONS_DIR(self) -> Path:
        """Directory connessioni database"""
        path = self.DATA_DIR / "connections"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def RESULTS_DIR(self) -> Path:
        """Directory risultati query/elaborazioni"""
        path = self.DATA_DIR / "results"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def API_CONNECTIONS_DIR(self) -> Path:
        """Directory configurazioni API"""
        path = self.DATA_DIR / "api_connections"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def UPLOADS_DIR(self) -> Path:
        """Directory upload temporanei"""
        path = self.DATA_DIR / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def FILES_DIR(self) -> Path:
        """Directory file esterni (CSV, XLSX, GeoJSON, GDB, etc.)"""
        path = self.DATA_DIR / "files"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def CATALOG_DIR(self) -> Path:
        """Directory catalogo tabelle"""
        path = self.DATA_DIR / "catalog"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def LOGS_DIR(self) -> Path:
        """Directory log"""
        path = Path("./logs")
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    # ==================== HELPER METHODS ====================
    
    def get_project_dir(self, project_id: str) -> Path:
        """Ottieni directory di un progetto specifico."""
        path = self.PROJECTS_DIR / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def get_project_output_dir(self, project_id: str) -> Path:
        """Ottieni directory output per un progetto specifico."""
        path = self.RESULTS_DIR / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def get_connection_file(self, connection_id: str) -> Path:
        """Ottieni path file di una connessione."""
        return self.CONNECTIONS_DIR / f"{connection_id}.json"
    
    def get_api_connection_dir(self, api_id: str) -> Path:
        """Ottieni directory di una API connection."""
        path = self.API_CONNECTIONS_DIR / api_id
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def get_file_source_dir(self, file_id: str) -> Path:
        """Ottieni directory di un file source."""
        path = self.FILES_DIR / file_id
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    # ==================== VALIDATION ====================
    
    def is_valid_db_type(self, db_type: str) -> bool:
        """Verifica se il tipo database è supportato"""
        return db_type in self.SUPPORTED_DB_TYPES
    
    def is_valid_output_format(self, format: str) -> bool:
        """Verifica se il formato output è supportato"""
        return format in self.SUPPORTED_OUTPUT_FORMATS
    
    def is_valid_project_type(self, project_type: str) -> bool:
        """Verifica se il tipo progetto è supportato"""
        return project_type in self.SUPPORTED_PROJECT_TYPES
    
    def is_valid_file_format(self, format: str) -> bool:
        """Verifica se il formato file è supportato per import"""
        return format in self.SUPPORTED_FILE_FORMATS


# Singleton
settings = Settings()


if __name__ == "__main__":
    print("=" * 50)
    print("⚙️  CONFIGURAZIONE")
    print("=" * 50)
    print(f"DATA_DIR:            {settings.DATA_DIR}")
    print(f"PROJECTS_DIR:        {settings.PROJECTS_DIR}")
    print(f"CONNECTIONS_DIR:     {settings.CONNECTIONS_DIR}")
    print(f"RESULTS_DIR:         {settings.RESULTS_DIR}")
    print(f"API_CONNECTIONS_DIR: {settings.API_CONNECTIONS_DIR}")
    print(f"UPLOADS_DIR:         {settings.UPLOADS_DIR}")
    print(f"FILES_DIR:           {settings.FILES_DIR}")
    print("-" * 50)
    print(f"DB_TIMEOUT:          {settings.DB_QUERY_TIMEOUT}s")
    print(f"API_TIMEOUT:         {settings.API_TIMEOUT}s")
    print(f"MAX_FILE_SIZE:       {settings.MAX_FILE_SIZE / 1024 / 1024:.0f} MB")
    print("=" * 50)
