"""
Configurazione URBY-ETL — solo DEFAULTS + variabili d'ambiente, niente config.json.

Priorità:
    1. Variabile d'ambiente (es. export CELERY_BROKER_URL=...)
    2. File backend/.env (caricato all'import di questo modulo)
    3. DEFAULTS della classe

"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

# Carica backend/.env il prima possibile, prima di qualunque os.getenv del
# resto dell'app. Tutti i moduli che leggono variabili d'ambiente (incluso
# app.shared.crypto) vedranno così le variabili definite nel .env. Senza
# questo, l'app userebbe la chiave di cifratura di default e fallirebbe a
# decifrare qualsiasi record salvato con la chiave reale.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    # python-dotenv non installato — l'app continua a funzionare con le sole
    # variabili d'ambiente esportate dal sistema.
    pass

logger = logging.getLogger(__name__)

# =============================================================================
# CLASSE SETTINGS
# =============================================================================

class Settings:
    """
    Configurazione singleton per URBY-ETL.

    Accesso:
        from app.config import settings
        settings.CELERY_BROKER_URL
        settings.get("API_TIMEOUT", 30)
    """

    DEFAULTS = {
        # ==================== DIRECTORY ====================
        "DATA_DIR": "./data",

        # ==================== LOG DB ====================
        "LOG_DB_FILE":              "log.db",
        "LOG_DB_MAX_ROWS":          100_000,
        "LOG_DB_CONNECT_TIMEOUT":   5,
        "LOG_DB_BUSY_TIMEOUT":      3000,
        "RUNS_PAGE_SIZE":           50,
        "RUNS_LOG_LIMIT":           200,

        # ==================== CORS ====================
        "CORS_ORIGINS": "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",

        # ==================== DATABASE ====================
        "DB_CONNECT_TIMEOUT": 10,
        "DB_QUERY_TIMEOUT": 30,
        "SUPPORTED_DB_TYPES": ["postgres", "mssql", "mysql", "oracle", "sqlite"],

        # ==================== CELERY (Redis) ====================
        "CELERY_BROKER_URL":            "redis://localhost:6379/0",
        "CELERY_RESULT_BACKEND":        "redis://localhost:6379/1",
        "CELERY_TASK_TIMEOUT":          3600,
        "CELERY_TASK_HARD_LIMIT":       3900,
        "CELERY_PREVIEW_TIMEOUT":       300,
        "CELERY_PREVIEW_HARD_LIMIT":    360,
        "CELERY_SCHEDULE_TIMEOUT":      3500,
        "CELERY_SCHEDULE_HARD_LIMIT":   3600,
        "CELERY_MAX_RETRIES":           3,
        "TASK_PROGRESS_POLL_INTERVAL":  1,
        "CELERY_RESULT_EXPIRES":        86400,

        # ==================== API ESTERNE ====================
        "API_TIMEOUT": 30,
        "API_DEFAULT_RATE_LIMIT": 5,

        # ==================== FORMATI OUTPUT ====================
        "SUPPORTED_OUTPUT_FORMATS": [
            "csv", "json", "xlsx", "parquet",
            "geojson", "gpkg", "shp", "gdb"
        ],
        "DEFAULT_OUTPUT_FORMAT": "csv",

        # ==================== FILE ESTERNI ====================
        "SUPPORTED_FILE_FORMATS": [
            "csv", "xlsx", "json",
            "geojson", "gpkg", "shp", "gdb"
        ],
        "MAX_FILE_SIZE": 100 * 1024 * 1024,  # 100 MB

        # ==================== PROGETTI ====================
        "SUPPORTED_PROJECT_TYPES": ["estrai", "manipola", "arricchisci", "salva"],
        "DEFAULT_GROUP": "default",

        # ==================== LIMITI ====================
        "MAX_PROJECT_NAME_LENGTH": 100,
        "MAX_CONNECTION_NAME_LENGTH": 100,
        "MAX_PREVIEW_ROWS": 1000,

        # ==================== TASK ARRICCHIMENTO ====================
        "ENRICHMENT_BATCH_SIZE": 100,
        "ENRICHMENT_MAX_WORKERS": 5,
        "ENRICHMENT_CACHE_TTL": 3600,
    }

    def __init__(self):
        pass  # Niente da caricare — tutto da DEFAULTS + env

    # ==================== ACCESSO VALORI ====================

    def get(self, key: str, default: Any = None) -> Any:
        """
        Ottieni un valore di configurazione.

        Priorità:
            1. Variabile d'ambiente
            2. DEFAULTS della classe
            3. default parametro
        """
        env_value = os.getenv(key)
        if env_value is not None:
            return self._converti_tipo_env(key, env_value)
        return self.DEFAULTS.get(key, default)

    def _converti_tipo_env(self, key: str, env_value: str) -> Any:
        default_value = self.DEFAULTS.get(key)
        if isinstance(default_value, bool):
            return env_value.lower() in ("true", "1", "yes")
        elif isinstance(default_value, int):
            return int(env_value)
        elif isinstance(default_value, float):
            return float(env_value)
        return env_value

    def set(self, key: str, value: Any, auto_save: bool = True):
        """Set in-memory (runtime only)."""
        os.environ[key] = str(value)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        return self.get(name)

    # ==================== DIRECTORY ====================

    def _directory(self, sottopercorso: str, base: Path = None, create: bool = True) -> Path:
        if base is None:
            base = Path(self.get("DATA_DIR"))
        percorso = base / sottopercorso
        if create:
            percorso.mkdir(parents=True, exist_ok=True)
        return percorso

    @property
    def CORS_ORIGINS_LIST(self) -> list:
        """Restituisce la lista delle origini CORS da stringa CSV."""
        raw = self.get("CORS_ORIGINS", "")
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def DATA_DIR(self) -> Path:
        return Path(self.get("DATA_DIR"))

    @property
    def TEMP_DIR(self) -> Path:
        return self._directory("temp")

    @property
    def CONNECTIONS_DIR(self) -> Path:
        return self._directory("connections", create=False)

    @property
    def RESULTS_DIR(self) -> Path:
        return self._directory("results", create=False)

    @property
    def API_CONNECTIONS_DIR(self) -> Path:
        return self._directory("api_connections", create=False)

    @property
    def UPLOADS_DIR(self) -> Path:
        return self._directory("uploads", create=False)

    @property
    def FILES_DIR(self) -> Path:
        return self._directory("files", create=False)

    @property
    def CATALOG_DIR(self) -> Path:
        return self._directory("catalog", create=False)

    @property
    def LOGS_DIR(self) -> Path:
        return self.DATA_DIR  # data/log.db direttamente in data/

    @property
    def TASKS_DIR(self) -> Path:
        return self._directory("tasks", create=False)

    @property
    def SCHEDULER_LOGS_DIR(self) -> Path:
        return self._directory("scheduler_logs", create=False)

    @property
    def SCHEDULES_DIR(self) -> Path:
        return self._directory("schedules", create=False)

    @property
    def ARCGIS_CONNECTIONS_DIR(self) -> Path:
        return self._directory("arcgis_connections", create=False)

    # ==================== HELPER PERCORSI ====================

    def get_project_output_dir(self, project_id: str) -> Path:
        return self._directory(project_id, base=self.RESULTS_DIR)

    def get_connection_file(self, connection_id: str) -> Path:
        return self.CONNECTIONS_DIR / f"{connection_id}.json"

    def get_api_connection_dir(self, api_id: str) -> Path:
        return self._directory(api_id, base=self.API_CONNECTIONS_DIR)

    def get_file_source_dir(self, file_id: str) -> Path:
        return self._directory(file_id, base=self.FILES_DIR)

    def get_task_dir(self, task_id: str) -> Path:
        return self._directory(task_id, base=self.TASKS_DIR)

    # ==================== REDIS AUTO-START ====================

    def _ensure_redis(self, errors: list, warnings: list) -> None:
        """Verifica che Redis sia raggiungibile; se no, lo avvia via WSL."""
        import subprocess, time, socket

        def _redis_ping() -> bool:
            try:
                with socket.create_connection(("localhost", 6379), timeout=2):
                    return True
            except OSError:
                return False

        if _redis_ping():
            logger.info("Redis già attivo su localhost:6379")
            return

        # Tenta avvio via WSL
        logger.info("Redis non raggiungibile — avvio via WSL...")
        try:
            subprocess.Popen(
                ["wsl", "redis-server", "--daemonize", "yes"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Attendi che Redis sia pronto (max 5s)
            for _ in range(10):
                time.sleep(0.5)
                if _redis_ping():
                    logger.info("Redis avviato con successo via WSL")
                    return
            warnings.append("Redis avviato via WSL ma non risponde dopo 5s")
        except FileNotFoundError:
            errors.append(
                "Redis non attivo e WSL non disponibile. "
                "Avvia Redis manualmente: wsl redis-server --daemonize yes"
            )
        except Exception as e:
            errors.append(f"Impossibile avviare Redis via WSL: {e}")

    # ==================== VALIDAZIONE AVVIO ====================

    def validate_startup(self) -> None:
        errors = []
        warnings = []

        try:
            data_dir = self.DATA_DIR
            data_dir.mkdir(parents=True, exist_ok=True)
            test_file = data_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
        except Exception as e:
            errors.append(f"DATA_DIR '{self.get('DATA_DIR')}' non scrivibile: {e}")

        # Pulizia cartelle temp residue da sessioni precedenti
        try:
            import shutil, stat
            def _on_error(func, fpath, exc_info):
                try:
                    os.chmod(fpath, stat.S_IWRITE)
                    func(fpath)
                except Exception as e:
                    logger.debug("Cleanup temp: impossibile rimuovere %s: %s", fpath, e)
            temp_dir = self.TEMP_DIR
            if temp_dir.exists():
                for entry in temp_dir.iterdir():
                    if entry.is_dir():
                        shutil.rmtree(entry, onerror=_on_error)
                    else:
                        try:
                            entry.unlink()
                        except Exception as e:
                            logger.debug("Cleanup temp: impossibile rimuovere %s: %s", entry, e)
        except Exception as e:
            logger.debug("Pulizia cartelle temp fallita (non critico): %s", e)

        # Rimozione cartelle legacy data/files e data/results
        # I file e i risultati sono nel DB SQLite come blob — queste cartelle
        # sono residui dell'architettura precedente e non vengono più scritte.
        # WAL checkpoint per tutti i DB SQLite — riduce i file .wal residui
        try:
            for db_name in ["config.db", "results.db", "intermediate.db"]:
                db_path = self.DATA_DIR / db_name
                if db_path.exists():
                    try:
                        _conn = sqlite3.connect(str(db_path))
                        _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        _conn.close()
                    except Exception as e:
                        logger.debug("WAL checkpoint fallito per %s: %s", db_name, e)
        except Exception as e:
            logger.debug("WAL checkpoint sweep fallito: %s", e)

        try:
            import shutil as _shutil
            for legacy_dir in ["files", "results", "File", "Results"]:
                legacy_path = self.DATA_DIR / legacy_dir
                if legacy_path.exists() and legacy_path.is_dir():
                    _shutil.rmtree(legacy_path, ignore_errors=True)
        except Exception as e:
            logger.debug("Rimozione cartelle legacy fallita (non critico): %s", e)

        # ── Redis: verifica che sia attivo, altrimenti avvialo via WSL ──
        self._ensure_redis(errors, warnings)

        for w in warnings:
            print(f"⚠️  URBY-ETL: {w}")

        if errors:
            print("\n❌ URBY-ETL — Configurazione non valida, avvio interrotto:\n")
            for i, e in enumerate(errors, 1):
                print(f"  {i}. {e}")
            raise SystemExit(1)

    # ==================== DEBUG ====================

    def print_config(self):
        print("=" * 60)
        print("⚙️  CONFIGURAZIONE URBY-ETL")
        print("=" * 60)
        print("\n📂 DIRECTORY")
        print(f"  DATA_DIR:            {self.DATA_DIR}")
        print(f"  LOGS_DIR:            {self.LOGS_DIR}")
        print(f"  CONNECTIONS_DIR:     {self.CONNECTIONS_DIR}")
        print(f"  RESULTS_DIR:         {self.RESULTS_DIR}")
        print("\n⏱️  TIMEOUT")
        print(f"  DB_QUERY_TIMEOUT:    {self.DB_QUERY_TIMEOUT}s")
        print(f"  API_TIMEOUT:         {self.API_TIMEOUT}s")
        print(f"  CELERY_TASK_TIMEOUT: {self.CELERY_TASK_TIMEOUT}s")
        print("\n🗄️  CELERY (Redis)")
        print(f"  BROKER_URL:          {self.CELERY_BROKER_URL}")
        print(f"  RESULT_BACKEND:      {self.CELERY_RESULT_BACKEND}")
        print("=" * 60)


# Singleton
settings = Settings()

if __name__ == "__main__":
    settings.print_config()
