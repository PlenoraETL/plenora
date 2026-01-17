"""
Logging strutturato per l'applicazione.

Approccio IBRIDO:
- app.log: tutti i log insieme (visione globale)
- errors.log: solo errori di tutti i service
- <service_name>.log: log specifico per ogni service

USO:
    from utils.logger import get_logger
    
    logger = get_logger(__name__)
    logger.info("Messaggio informativo")
    logger.error("Errore critico", exc_info=True)
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from config import settings


# Formati log
CONSOLE_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
FILE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Dimensione massima file (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024
BACKUP_COUNT = 5

# Cache dei logger già configurati
_configured_loggers = set()


def _get_service_name(name: str) -> str:
    """
    Estrae nome service dal nome completo del modulo.
    
    Esempi:
        'services.orchestrator_service' -> 'orchestrator'
        'services.project_service' -> 'project'
        '__main__' -> 'main'
        'config' -> 'config'
    """
    # Rimuovi prefisso 'services.'
    if name.startswith('services.'):
        name = name.replace('services.', '')
    
    # Rimuovi suffisso '_service'
    if name.endswith('_service'):
        name = name.replace('_service', '')
    
    return name


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Crea e configura un logger con output su:
    - Console (INFO+)
    - app.log (DEBUG+) - tutti i log
    - errors.log (ERROR+) - solo errori
    - <service>.log (DEBUG+) - log specifico del service
    
    Args:
        name: Nome del logger (usa __name__ per il modulo corrente)
        level: Livello minimo di log (default DEBUG)
        
    Returns:
        Logger configurato
    """
    logger = logging.getLogger(name)
    
    # Evita duplicazione handler se già configurato
    if name in _configured_loggers:
        return logger
    
    logger.setLevel(level)
    
    # Assicura che la directory logs esista
    logs_dir = settings.LOGS_DIR
    
    service_name = _get_service_name(name)
    
    # ========== 1. Console Handler (INFO+) ==========
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT, DATE_FORMAT))
    logger.addHandler(console_handler)
    
    # ========== 2. App.log - Tutto insieme (DEBUG+) ==========
    app_log_file = logs_dir / "app.log"
    app_handler = RotatingFileHandler(
        app_log_file,
        maxBytes=MAX_FILE_SIZE,
        backupCount=BACKUP_COUNT,
        encoding='utf-8'
    )
    app_handler.setLevel(logging.DEBUG)
    app_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
    logger.addHandler(app_handler)
    
    # ========== 3. Errors.log - Solo errori (ERROR+) ==========
    errors_log_file = logs_dir / "errors.log"
    errors_handler = RotatingFileHandler(
        errors_log_file,
        maxBytes=MAX_FILE_SIZE,
        backupCount=BACKUP_COUNT,
        encoding='utf-8'
    )
    errors_handler.setLevel(logging.ERROR)
    errors_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
    logger.addHandler(errors_handler)
    
    # ========== 4. Service-specific log (DEBUG+) ==========
    service_log_file = logs_dir / f"{service_name}.log"
    service_handler = RotatingFileHandler(
        service_log_file,
        maxBytes=MAX_FILE_SIZE,
        backupCount=BACKUP_COUNT,
        encoding='utf-8'
    )
    service_handler.setLevel(logging.DEBUG)
    service_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
    logger.addHandler(service_handler)
    
    # Marca come configurato
    _configured_loggers.add(name)
    
    return logger


# Logger principale dell'applicazione
app_logger = get_logger("app")