"""Celery beat task: cleanup di file_sources temporanei scaduti.

Bug originale (plenora-bug-orchestration I2 P2):
  `app/core/eseguibili/service.py:360` crea un FileSource con
  `name="__exec_temp_<id>_<uuid8>"` quando un eseguibile viene lanciato
  con parametri custom (input dinamico). Pre-fix nessun cleanup
  automatico: queste righe restavano nel DB indefinitamente, gonfiando
  `file_sources` e (con `STORAGE_POLICY=object_storage`) anche lo
  storage MinIO.

Post-fix:
  1. Migration `2026-05-22_add_file_source_temp_ttl.sql` aggiunge
     `is_temp BOOLEAN` + `expires_at TIMESTAMPTZ` con index parziale.
  2. `eseguibili/service.py` setta `is_temp=true, expires_at=now()+24h`
     in `create_from_upload`.
  3. Questo task gira ogni ora via PlenoraBeatScheduler e cancella le
     righe `WHERE is_temp = true AND expires_at < now()` chiamando
     `delete_file_source` per ogni id (cleanup blob MinIO incluso).

Pattern Go-friendly: task modulo, no classi, dependency injection
esplicita via import locale. Mappa direttamente a un Go cron worker
che invoca `cleanupExpiredTempFiles(ctx, db, storage) error`.
"""

from __future__ import annotations

from typing import TypedDict

from app.db.pg_connection import unit_of_work
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CleanupResult(TypedDict):
    """Result payload del task. Esposto come dict per ispezione operativa
    via Flower / Celery result backend."""
    success: bool
    candidates_found: int
    deleted_ok: int
    deleted_failed: int
    duration_ms: int


def _list_expired_temp_file_ids() -> list[str]:
    """Query expired temp file_sources.

    Funzione modulo separata per testabilita' (puo' essere monkeypatched
    nei test senza toccare il task body).
    """
    with unit_of_work() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM file_sources "
                "WHERE is_temp = true AND expires_at IS NOT NULL "
                "AND expires_at < now()"
            )
            rows = cur.fetchall()
    return [row["id"] for row in rows]


def run_cleanup_temp_files() -> CleanupResult:
    """Beat sweep: cancella file_sources temp scaduti.

    Strategia:
      - Lista degli ID expired in una SELECT separata (non un singolo
        DELETE batch) per usare `delete_file_source` che gestisce
        correttamente il cleanup MinIO end-to-end + il refcount sui
        blob condivisi.
      - Errori per singolo file vengono loggati ma NON bloccano il
        loop: una key MinIO mancante non deve impedire la cancellazione
        di altre N-1 righe valide.
      - Idempotente: rieseguire il task non cambia lo stato (le righe
        gia' cancellate non riappaiono nella SELECT).
    """
    import time
    from app.core.files.db import delete_file_source

    start = time.monotonic()
    try:
        candidates = _list_expired_temp_file_ids()
    except Exception as exc:
        logger.error(
            "[cleanup_temp_files] SELECT expired fallita: %s: %s",
            type(exc).__name__, exc,
            exc_info=True,
        )
        return {
            "success": False,
            "candidates_found": 0,
            "deleted_ok": 0,
            "deleted_failed": 0,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }

    if not candidates:
        logger.debug("[cleanup_temp_files] nessun temp file scaduto")
        return {
            "success": True,
            "candidates_found": 0,
            "deleted_ok": 0,
            "deleted_failed": 0,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }

    deleted_ok = 0
    deleted_failed = 0
    for file_id in candidates:
        try:
            if delete_file_source(file_id):
                deleted_ok += 1
            else:
                # delete_file_source ritorna False se l'id non esiste:
                # significa che e' gia' stato cancellato da un'altra
                # istanza del task / da operatore. Non e' un errore.
                logger.info(
                    "[cleanup_temp_files] file %s gia' cancellato (skip)",
                    file_id,
                )
        except Exception as exc:
            deleted_failed += 1
            logger.warning(
                "[cleanup_temp_files] delete fallita per id=%s: %s: %s",
                file_id, type(exc).__name__, exc,
                exc_info=True,
            )

    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "[cleanup_temp_files] completed candidates=%d ok=%d failed=%d "
        "duration_ms=%d",
        len(candidates), deleted_ok, deleted_failed, duration_ms,
    )
    return {
        "success": deleted_failed == 0,
        "candidates_found": len(candidates),
        "deleted_ok": deleted_ok,
        "deleted_failed": deleted_failed,
        "duration_ms": duration_ms,
    }
