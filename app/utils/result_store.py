"""
URBY-ETL — Result Store
Helper per salvare i risultati pipeline nel DB SQLite.
I file vengono scritti in tempdir, salvati nel DB, poi eliminati.
"""

import os
import uuid
from pathlib import Path
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Numero default di risultati da conservare per progetto.
# Sovrascrivibile per singolo progetto tramite projects.retention_n in config.db.
DEFAULT_RESULTS_RETENTION = 10


def get_temp_output_path(filename: str) -> Path:
    """Ritorna un path in data/temp per scrivere il file risultato."""
    from app.config import settings
    tmp_dir = Path(settings.DATA_DIR) / "temp" / f"urby_{os.getpid()}_{os.urandom(4).hex()}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / filename


def _apply_results_retention(project_id: str) -> None:
    """
    Applica la retention policy sui risultati del progetto.
    Conserva gli N risultati più recenti, elimina i più vecchi.
    N viene letto da projects.retention_n (config.db), default DEFAULT_RESULTS_RETENTION.
    """
    try:
        import app.db.config_db_service as cfg_db
        import app.db.results_db_service as res_db

        # Legge retention_n dal progetto (SQLite #1)
        retention_n = cfg_db.get_project_retention_n(project_id)
        if retention_n is None:
            retention_n = DEFAULT_RESULTS_RETENTION

        # Lista risultati ordinati dal più recente (SQLite #2)
        all_results = res_db.list_results(project_id)

        if len(all_results) <= retention_n:
            return  # Nulla da eliminare

        # Elimina i risultati oltre retention_n (i più vecchi sono in fondo)
        to_delete = all_results[retention_n:]
        for r in to_delete:
            res_db.delete_result(r["id"])
            logger.debug(
                f"Retention: eliminato risultato {r['id']} del progetto {project_id}",
                op="retention"
            )

        logger.info(
            f"Retention applicata su {project_id}: conservati {retention_n}, "
            f"eliminati {len(to_delete)}",
            op="retention"
        )

    except Exception as e:
        # La retention non deve mai bloccare il salvataggio del risultato
        logger.warning(f"Errore retention risultati ({project_id}): {e}", op="retention")


def save_result_blob(project_id: str, path: Path, fmt: str,
                     rows_count: int = None, group_id: str = None) -> str:
    """
    Salva il blob del file risultato nel DB SQLite.
    Applica la retention policy dopo il salvataggio.
    Elimina il file temp dopo il salvataggio.

    Args:
        group_id: gruppo dell'utente proprietario del progetto.
                  Se None, viene letto dal progetto nel DB come fallback.

    Returns: result_id generato
    """
    try:
        import app.db.db_service as db
        from app.config import settings

        # Risolvi group_id
        resolved_group_id = group_id
        if not resolved_group_id:
            project = db.get_project(project_id)
            resolved_group_id = (project.get("group_id") if project else None) or settings.DEFAULT_GROUP

        result_id = str(uuid.uuid4())
        blob = path.read_bytes()

        db.save_result(
            result_id=result_id,
            project_id=project_id,
            group_id=resolved_group_id,
            filename=path.name,
            fmt=fmt,
            data=blob,
            rows_count=rows_count,
        )

        # Applica retention dopo il salvataggio
        _apply_results_retention(project_id)

        return result_id

    except Exception as e:
        logger.warning(f"Errore salvataggio risultato nel DB ({project_id}): {e}")
        return ""
    finally:
        # Elimina sempre il file temp
        try:
            if path.exists():
                path.unlink()
            if path.parent.exists() and not any(path.parent.iterdir()):
                path.parent.rmdir()
        except Exception as e:
            logger.warning("Cleanup file temp fallito per %s: %s", project_id, e)


def get_result_as_tempfile(project_id: str) -> Optional[Path]:
    """
    Recupera l'ultimo risultato dal DB ed estrae in un file temp.
    Il chiamante deve eliminare il file dopo l'uso.

    Returns: Path del file temp, o None se non trovato
    """
    try:
        import app.db.db_service as db

        row = db.get_latest_result(project_id)
        if not row:
            return None

        data = db.get_result_data(row["id"])
        if not data:
            return None

        suffix = f".{row['format']}"
        from app.config import settings
        tmp_dir = Path(settings.DATA_DIR) / "temp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"urby_cached_{os.urandom(4).hex()}{suffix}"
        tmp_path.write_bytes(data)
        return tmp_path
    except Exception as e:
        logger.warning(f"Errore lettura risultato dal DB ({project_id}): {e}")
        return None


def get_result_info(project_id: str) -> Optional[dict]:
    """Ritorna metadata dell'ultimo risultato (senza blob)."""
    try:
        import app.db.db_service as db
        return db.get_latest_result(project_id)
    except Exception as e:
        logger.warning("get_result_info fallito per %s: %s", project_id, e)
        return None
