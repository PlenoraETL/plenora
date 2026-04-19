"""
imdf_publish_task.py — Task Celery per pubblicazione IMDF su ArcGIS.

Workflow
========
Il task viene lanciato da POST /api/imdf/publish dopo che un arricchimento
ha prodotto un bundle IMDF persistito in `IMDF_BUNDLES_DIR/{bundle_task_id}/`.

Flusso:
 1. Risolve la cartella del bundle su disco
 2. Carica i 5 layer GDB con `load_imdf_layers`
 3. Legge `renderers.json` (se apply_renderers)
 4. Chiama `upload_imdf_layers(mode=...)` con progress_callback
 5. Ritorna il dict risultato

Il frontend polla il task come fa per l'enrichment (GET /api/tasks/{id}).
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from celery import current_task

from app.config import settings
from app.utils.logger import get_logger
from workers.celery_app import celery_app

# Regex whitelist per bundle_task_id. Gli endpoint REST la applicano gia'
# via `_safe_bundle_dir`, ma il task viene importato anche da code path
# diversi (retry, chiamate dirette in test) — difesa in profondita'.
_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _safe_bundle_dir(bundle_task_id: str) -> Optional[Path]:
    """Valida il bundle_task_id contro path traversal e ritorna il path
    resolved. Ritorna None se l'id e' malformato (il caller deve gestire
    l'errore e propagarlo al task result)."""
    if not bundle_task_id or not _BUNDLE_ID_RE.match(bundle_task_id):
        return None
    root = settings.IMDF_BUNDLES_DIR.resolve()
    candidate = (root / bundle_task_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate

logger = get_logger(__name__)


def _update_progress(task, progress: int, message: str, is_error: bool = False):
    """Aggiorna il progresso del task in Redis. Stesso pattern di
    workers.tasks.enrichment_task.update_task_progress."""
    task.update_state(
        state="PROGRESS",
        meta={
            "progress": max(0, progress),
            "message": message,
            "is_error": is_error,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


@celery_app.task(bind=True, name="tasks.execute_imdf_publish")
def execute_imdf_publish_task(
    self,
    bundle_task_id: str,
    publisher_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Pubblica un bundle IMDF persistito su ArcGIS.

    Args:
        bundle_task_id: task_id del precedente arricchimento IMDF. La funzione
            risolve la cartella `IMDF_BUNDLES_DIR/{bundle_task_id}/` e cerca
            il .gdb + renderers.json al suo interno.
        publisher_config: dict con:
            - connection_id       (str, richiesto)
            - folder              (str, opzionale)
            - fs_name             (str, richiesto)
            - mode                ('replace' | 'append' | 'upsert', default 'replace')
            - apply_renderers     (bool, default False)
            - match_columns       (dict, opzionale — override dei default per-layer)

    Returns:
        Dict con gli stessi campi di upload_imdf_layers, arricchiti di task_id,
        success, mode, e elapsed_time.
    """
    task_id = self.request.id
    started_at = time.time()

    logger.info(
        f"[Task {task_id}] IMDF publish avviato "
        f"(bundle={bundle_task_id} mode={publisher_config.get('mode', 'replace')})"
    )

    try:
        _update_progress(self, 2, "Risoluzione bundle IMDF...")

        # ── 1. Valida bundle_task_id e risolvi path bundle ───────────
        bundle_dir = _safe_bundle_dir(bundle_task_id)
        if bundle_dir is None:
            msg = (
                f"bundle_task_id '{bundle_task_id}' malformato "
                "(ammessi solo alfanumerico + '-' '_')"
            )
            logger.error(f"[Task {task_id}] {msg}")
            return {
                "success": False,
                "task_id": task_id,
                "bundle_task_id": bundle_task_id,
                "error": msg,
                "elapsed_time": round(time.time() - started_at, 2),
            }
        # R2 multi-host: se il bundle non e' nella cache locale di questo
        # worker, prova a hydrate-lo da S3. Serve quando il publish gira su
        # un nodo diverso da quello che ha generato il bundle.
        if not bundle_dir.exists() or not bundle_dir.is_dir():
            try:
                from app.core.imdf import storage as _imdf_storage
                _imdf_storage.ensure_local_bundle(bundle_task_id)
            except FileNotFoundError:
                pass  # gestito sotto con il return 404-like
            except Exception as _e:
                logger.warning(f"[Task {task_id}] hydrate storage fallita: {_e}")

        if not bundle_dir.exists() or not bundle_dir.is_dir():
            msg = (
                f"Bundle IMDF '{bundle_task_id}' non trovato (ne' local ne' S3). "
                f"Potrebbe essere scaduto (TTL {settings.IMDF_BUNDLE_TTL_HOURS}h)."
            )
            logger.error(f"[Task {task_id}] {msg}")
            return {
                "success": False,
                "task_id": task_id,
                "bundle_task_id": bundle_task_id,
                "error": msg,
                "elapsed_time": round(time.time() - started_at, 2),
            }

        # ── 2. Trova .gdb dentro il bundle ───────────────────────────
        gdb_path: Optional[Path] = None
        for entry in bundle_dir.rglob("*.gdb"):
            if entry.is_dir():
                gdb_path = entry
                break
        if gdb_path is None:
            msg = f"Nessun File Geodatabase (.gdb) trovato in {bundle_dir}"
            logger.error(f"[Task {task_id}] {msg}")
            return {
                "success": False,
                "task_id": task_id,
                "bundle_task_id": bundle_task_id,
                "error": msg,
                "elapsed_time": round(time.time() - started_at, 2),
            }

        _update_progress(self, 8, f"Caricamento layer IMDF dal GDB...")

        # ── 3. Carica GDB → GeoDataFrame per layer ───────────────────
        from app.core.imdf.loader import load_imdf_layers
        gdf_layers = load_imdf_layers(gdb_path)
        total_features = sum(len(gdf) for gdf in gdf_layers.values())
        logger.info(
            f"[Task {task_id}] GDB caricato: {len(gdf_layers)} layer, "
            f"{total_features} features totali"
        )

        _update_progress(
            self, 15,
            f"GDB caricato ({len(gdf_layers)} layer, {total_features} feat). Preparazione upload..."
        )

        # ── 4. Leggi renderers.json se richiesto ─────────────────────
        renderers_data = None
        apply_renderers = bool(publisher_config.get("apply_renderers"))
        if apply_renderers:
            renderers_path = bundle_dir / "renderers.json"
            if renderers_path.exists():
                try:
                    renderers_data = json.loads(renderers_path.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"[Task {task_id}] lettura renderers.json fallita: {e}")

        # ── 5. Estrai publisher config ───────────────────────────────
        connection_id = publisher_config.get("connection_id")
        if not connection_id:
            return {
                "success": False,
                "task_id": task_id,
                "bundle_task_id": bundle_task_id,
                "error": "connection_id mancante nel publisher_config",
                "elapsed_time": round(time.time() - started_at, 2),
            }

        fs_name = publisher_config.get("fs_name") or f"imdf_{bundle_task_id[:8]}"
        folder_name = publisher_config.get("folder") or None
        mode = publisher_config.get("mode") or "replace"
        match_columns = publisher_config.get("match_columns") or None

        # ── 6. Progress callback per upload ──────────────────────────
        # L'uploader notifica step-by-step (0..N). Mappiamo nel range 15..95.
        def _upload_progress(stage, current, total, message=""):
            base = 15
            span = 80
            pct = base + int((current / max(total, 1)) * span)
            _update_progress(self, pct, f"Pubblicazione IMDF: {message or stage}")

        _update_progress(self, 18, f"Upload su ArcGIS (mode={mode}, fs={fs_name})...")

        # ── 7. Chiamata uploader ─────────────────────────────────────
        from app.core.imdf.arcgis_uploader import upload_imdf_layers
        publish_result = upload_imdf_layers(
            connection_id=connection_id,
            layers=gdf_layers,
            fs_name=fs_name,
            folder_name=folder_name,
            apply_renderers=apply_renderers,
            renderers_data=renderers_data,
            progress_callback=_upload_progress,
            mode=mode,
            match_columns=match_columns,
        )

        elapsed = round(time.time() - started_at, 2)
        logger.info(
            f"[Task {task_id}] IMDF publish completato in {elapsed}s: "
            f"success={publish_result.get('success')} mode={mode}"
        )

        _update_progress(
            self, 98,
            f"Pubblicazione {mode} completata"
            if publish_result.get("success")
            else f"Pubblicazione {mode} con errori",
            is_error=not publish_result.get("success"),
        )

        return {
            "success": bool(publish_result.get("success")),
            "task_id": task_id,
            "bundle_task_id": bundle_task_id,
            "output_type": "imdf_publish",
            "message": (
                f"Pubblicazione IMDF completata: {fs_name}"
                if publish_result.get("success")
                else f"Pubblicazione IMDF con errori"
            ),
            "elapsed_time": elapsed,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            # Campo "imdf.publish" per coerenza con la struttura del task
            # di enrichment (il frontend lo legge dallo stesso path).
            "imdf": {"publish": publish_result},
        }

    except Exception as e:
        logger.error(f"[Task {task_id}] Errore IMDF publish: {e}", exc_info=True)
        return {
            "success": False,
            "task_id": task_id,
            "bundle_task_id": bundle_task_id,
            "error": str(e),
            "elapsed_time": round(time.time() - started_at, 2),
        }
