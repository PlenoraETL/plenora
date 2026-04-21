"""
Configurazione PLENORA-ETL — solo DEFAULTS + variabili d'ambiente, niente config.json.

Priorità:
    1. Variabile d'ambiente (es. export CELERY_BROKER_URL=...)
    2. File backend/.env (caricato all'import di questo modulo — SOLO in development)
    3. DEFAULTS della classe

In production/staging il file backend/.env viene ignorato: i secret devono
arrivare da variabili d'ambiente iniettate da systemd o da un secret manager.
Se il file è presente in prod/staging viene loggato un warning.
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Env names che disabilitano il caricamento di backend/.env dal disco.
# In questi ambienti i secret devono arrivare da env var di sistema o
# da un secret manager, mai da un file sul filesystem che potrebbe
# essere sopravvissuto a un rollback, finire in un backup, o essere
# stato copiato per errore dal deploy.
_DOTENV_DISABLED_ENVS = ("production", "prod", "staging", "stage")


def _should_load_dotenv() -> bool:
    """True se l'ambiente corrente consente la lettura di backend/.env dal disco.

    L'ambiente è letto da `ENVIRONMENT` (fallback `ENV`, default `development`).
    Ambienti in `_DOTENV_DISABLED_ENVS` restituiscono False.
    """
    env_name = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    return env_name not in _DOTENV_DISABLED_ENVS


# Carica backend/.env il prima possibile, prima di qualunque os.getenv del
# resto dell'app. Tutti i moduli che leggono variabili d'ambiente (incluso
# app.shared.crypto) vedranno così le variabili definite nel .env. Senza
# questo, l'app userebbe la chiave di cifratura di default e fallirebbe a
# decifrare qualsiasi record salvato con la chiave reale.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        if _should_load_dotenv():
            load_dotenv(_env_path)
        else:
            logger.warning(
                "backend/.env presente ma ENVIRONMENT indica prod/staging: "
                "file ignorato. I secret devono arrivare da env var di sistema."
            )
except ImportError:
    # python-dotenv non installato — l'app continua a funzionare con le sole
    # variabili d'ambiente esportate dal sistema.
    pass

# =============================================================================
# CLASSE SETTINGS
# =============================================================================

class Settings:
    """
    Configurazione singleton per PLENORA-ETL.

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
        # C3 fix 4: TTL corto + heartbeat periodico dal worker durante
        # l'esecuzione. Se il worker muore SIGKILL il lock scade in
        # SCHEDULE_LOCK_TTL_SECONDS invece di restare appeso fino al
        # hard limit Celery. HEARTBEAT_INTERVAL << TTL/2 per margine.
        "SCHEDULE_LOCK_TTL_SECONDS":                600,
        "SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS": 180,
        # C3 follow-up: leader election fail-closed di default.
        # Se Redis e' unavailable il beat va in standby invece di
        # diventare leader by default (evita doppio enqueue in caso
        # di flap Redis con due beat attivi). Override a True per
        # deployment single-instance dove nessun altro beat gira mai.
        "SCHEDULE_BEAT_LEADER_FAIL_OPEN":           False,
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

        # ==================== STORAGE POLICY (A1) ====================
        # Policy unica top-level per persistenza di result pipeline e
        # file_source utente. Vedi orchestrate/phase_1_stabilization/a1_storage_policy.md.
        #   - "db"             -> sempre BYTEA in DB, nessun bucket richiesto
        #   - "object_storage" -> sempre bucket, DB tiene solo metadata
        #   - "hybrid"         -> dispatch per soglia (default retro-compat)
        # Validazione coerenza all'avvio in app/shared/cloud_backends.validate_storage_config.
        "STORAGE_POLICY": "hybrid",
        # Hard cap per payload persistiti come BYTEA (mode "db" + ramo DB di "hybrid").
        # Default 500 MB: limite pratico di un BYTEA PG senza toast tuning.
        # Sopra cap: ResultSaveError al save (caller deve failare).
        "RESULT_DB_HARD_CAP_BYTES": 500 * 1024 * 1024,

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

        # ==================== CARICA ====================
        # Soglia righe oltre la quale TRUNCATE_INSERT + ALL_OR_NOTHING usa
        # stage+swap invece di DELETE+INSERT atomico. Sotto soglia: atomic
        # (zero overhead, no privilegi DDL). Sopra soglia: stage+swap
        # (safe su dataset enormi, richiede CREATE TABLE; se il grant
        # manca il writer fa fallback automatico ad atomic con WARNING).
        "CARICA_STAGE_SWAP_THRESHOLD": 500_000,

        # ==================== ESTRAI ====================
        # C11: hard cap righe ritornate da una query ESTRAI. Se un
        # chunk_fetchmany porta il totale oltre questo limite, la
        # query fallisce loud con suggerimento (aggiungi WHERE, usa
        # LIMIT, oppure alza il cap). Protezione anti-OOM: fino a
        # C11 il chunking era cosmetico (fetchmany ma poi accumulo
        # in memoria + DataFrame intero). Default 5M: ragionevole per
        # ETL tipico PLENORA-ETL (centinaia-migliaia di righe), lascia
        # margine per operazioni admin occasionali da 1-5M.
        "ESTRAI_MAX_ROWS": 5_000_000,

        # C11: cap righe serializzate in intermediate.runs snapshot.
        # Sopra questa soglia il writer scrive solo metadata (no
        # df.to_dict rows) per evitare double-materialization della
        # query output nell'intermediate DB. Default 100k: l'intermediate
        # serve per debug UI, non per re-esecuzione integrale.
        "INTERMEDIATE_SNAPSHOT_MAX_ROWS": 100_000,
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
            try:
                return int(env_value)
            except ValueError:
                return default_value
        elif isinstance(default_value, float):
            try:
                return float(env_value)
            except ValueError:
                return default_value
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
    def ESEGUIBILI_OUTPUTS_DIR(self) -> Path:
        return self._directory("eseguibili_outputs", create=False)

    @property
    def IMDF_BUNDLES_DIR(self) -> Path:
        """Cartella dove vengono persistiti i bundle IMDF estratti dopo
        l'arricchimento (GDB + renderers + geojson). I bundle rimangono
        disponibili per la pubblicazione ArcGIS on-demand (nodo imdf_publisher)
        finche' non vengono puliti dal beat scheduler (TTL da IMDF_BUNDLE_TTL_HOURS)."""
        return self._directory("imdf_bundles", create=False)

    @property
    def IMDF_BUNDLE_TTL_HOURS(self) -> int:
        """Ore di retention per i bundle IMDF su disco. Default 48h:
        copre un weekend lungo e lascia tempo per pubblicare manualmente
        senza che il bundle venga rimosso sotto i piedi dell'utente."""
        try:
            return int(self.get("IMDF_BUNDLE_TTL_HOURS", 48))
        except (TypeError, ValueError):
            return 48

    # ==================== LOGGING ====================
    # Livello minimo dei log che finiscono nella console (stdout/journald).
    @property
    def LOG_LEVEL_CONSOLE(self) -> str:
        return (self.get("LOG_LEVEL_CONSOLE", "INFO") or "INFO").upper()

    # Livello minimo dei log persistiti nel DB (tabella plenora.logs).
    # Default WARNING: in prod le righe INFO sono rumore e gonfiano la tabella
    # senza fornire valore diagnostico proporzionale al costo.
    @property
    def LOG_LEVEL_DB(self) -> str:
        return (self.get("LOG_LEVEL_DB", "WARNING") or "WARNING").upper()

    # Giorni di retention per plenora.logs. Record piu' vecchi vengono eliminati
    # dal beat scheduler (PlenoraBeatScheduler._cleanup_old_logs).
    @property
    def LOG_RETENTION_DAYS(self) -> int:
        try:
            return int(self.get("LOG_RETENTION_DAYS", 30))
        except (TypeError, ValueError):
            return 30

    # Giorni di retention per plenora.schedule_logs (storia esecuzioni
    # scheduler). Default 90gg — finestra utile per debug/audit di catene
    # ricorrenti, oltre la quale il dettaglio dei singoli step non serve.
    # Eliminati da PlenoraBeatScheduler._cleanup_old_schedule_logs.
    # Mettere 0 per disabilitare la retention.
    @property
    def SCHEDULE_LOGS_RETENTION_DAYS(self) -> int:
        try:
            return int(self.get("SCHEDULE_LOGS_RETENTION_DAYS", 90))
        except (TypeError, ValueError):
            return 90

    @property
    def UPLOADS_DIR(self) -> Path:
        return self._directory("uploads", create=False)

    @property
    def FILES_DIR(self) -> Path:
        """Cache dir per file_source blob materializzati.

        I file_source sono salvati nel DB (source of truth). Questa dir
        e' solo una cache filesystem popolata on-demand da
        `api_connector._resolve_file_source_id_path` quando un workflow
        deve dare a fiona/GDAL un path concreto. I file qui possono
        essere eliminati in qualsiasi momento — vengono ri-estratti dal
        DB al prossimo accesso.
        """
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
            print(f"⚠️  PLENORA-ETL: {w}")

        if errors:
            print("\n❌ PLENORA-ETL — Configurazione non valida, avvio interrotto:\n")
            for i, e in enumerate(errors, 1):
                print(f"  {i}. {e}")
            raise SystemExit(1)

    # ==================== DEBUG ====================

    def print_config(self):
        print("=" * 60)
        print("⚙️  CONFIGURAZIONE PLENORA-ETL")
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
