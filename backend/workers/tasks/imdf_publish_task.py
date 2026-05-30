"""
imdf_publish_task.py — Task Celery per pubblicazione IMDF su ArcGIS.

Workflow
"""

import time
from datetime import datetime, timezone

from app.config import settings
from app.core.imdf import storage as imdf_storage
from app.core.imdf.persist import load_indoor_bundle_payload
from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger
from workers.celery_app import celery_app

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
    publisher_config: JsonObject,
) -> JsonObject:
    """Pubblica un bundle IMDF persistito su ArcGIS.

    Args:
        bundle_task_id: task_id del precedente arricchimento che ha prodotto il
            plenora.indoor.bundle persistito su storage (sezioni
            manifest/validation/renderers/{site}/*.geojson).
        publisher_config: JSON object con:
            - connection_id (str, richiesto)
            - folder (str, opzionale)
            - fs_name (str, richiesto)
            - mode ('replace' | 'append' | 'upsert', default 'replace')
            - apply_renderers (bool, default False)
            - match_columns (dict, opzionale — override dei default per-layer)

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

        meta = imdf_storage.get_meta_only(bundle_task_id)
        if meta is None:
            msg = (
                f"Bundle IMDF '{bundle_task_id}' senza meta su storage "
                f"(o pre-IMDF-3 non piu' supportato). "
                f"Potrebbe essere scaduto (TTL {settings.IMDF_BUNDLE_TTL_HOURS}h)."
            )
            logger.error(f"[Task {task_id}] {msg}")
            return {
                "success": False, "task_id": task_id,
                "bundle_task_id": bundle_task_id, "error": msg,
                "elapsed_time": round(time.time() - started_at, 2),
            }

        if meta.get("format") == "plenora.indoor.bundle":
            # Path NUOVO: bundle plenora.indoor.bundle (sezioni JSON su storage).
            # Niente .gdb / vsimem / pyogrio: i layer si ricompongono dal
            # descrittore e diventano GeoDataFrame al boundary dataframe.
            _update_progress(self, 8, "Caricamento layer dal bundle indoor...")
            try:
                from app.core.imdf.bundle_geo import layer_geodataframes_only
                payload = load_indoor_bundle_payload(bundle_task_id)
                if not payload or not payload.get("layers"):
                    msg = (
                        f"Bundle indoor '{bundle_task_id}' senza layer leggibili "
                        f"su storage."
                    )
                    logger.error(f"[Task {task_id}] {msg}")
                    return {
                        "success": False, "task_id": task_id,
                        "bundle_task_id": bundle_task_id, "error": msg,
                        "elapsed_time": round(time.time() - started_at, 2),
                    }
                gdf_layers = layer_geodataframes_only(payload, drop_z=True)
                total_features = sum(len(gdf) for gdf in gdf_layers.values())
            except Exception as e:
                msg = f"Caricamento bundle indoor fallito: {e}"
                logger.error(f"[Task {task_id}] {msg}", exc_info=True)
                return {
                    "success": False, "task_id": task_id,
                    "bundle_task_id": bundle_task_id, "error": msg,
                    "elapsed_time": round(time.time() - started_at, 2),
                }
        else:
            # Formato legacy (GDB) rimosso: solo plenora.indoor.bundle e'
            # supportato. Un bundle senza questo formato va rigenerato.
            msg = (
                f"Bundle '{bundle_task_id}' in formato non supportato "
                f"({meta.get('format') or 'legacy/GDB'}): rigenera l'arricchimento "
                f"per produrre un plenora.indoor.bundle."
            )
            logger.error(f"[Task {task_id}] {msg}")
            return {
                "success": False, "task_id": task_id,
                "bundle_task_id": bundle_task_id, "error": msg,
                "elapsed_time": round(time.time() - started_at, 2),
            }

        logger.info(
            f"[Task {task_id}] layer caricati: {len(gdf_layers)} layer, "
            f"{total_features} features totali"
        )

        _update_progress(
            self, 15,
            f"Layer caricati ({len(gdf_layers)} layer, {total_features} feat). Preparazione upload..."
        )

        renderers_data = None
        apply_renderers = bool(publisher_config.get("apply_renderers"))
        if apply_renderers:
            # Post-tombstone (IMDF-4 step 4): renderers letti solo da
            # storage (`{PREFIX}/{task_id}/files/renderers.json`).
            renderers_data = imdf_storage.read_renderers(bundle_task_id)

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

        # L'uploader notifica step-by-step (0..N). Mappiamo nel range 15..95
        def _upload_progress(stage, current, total, message=""):
            base = 15
            span = 80
            pct = base + int((current / max(total, 1)) * span)
            _update_progress(self, pct, f"Pubblicazione IMDF: {message or stage}")

        _update_progress(self, 18, f"Upload su ArcGIS (mode={mode}, fs={fs_name})...")

        from app.core.imdf.arcgis_uploader import upload_imdf_layers
        # Writer ArcGIS condiviso (adapter concreto): istanziato qui nel worker
        # (fuori dal boundary IMDF), iniettato nella policy IMDF.
        from app.core.features.features_service import FeaturesService

        publish_result = upload_imdf_layers(
            connection_id=connection_id,
            layers=gdf_layers,
            fs_name=fs_name,
            feature_writer=FeaturesService(),
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
                else "Pubblicazione IMDF con errori"
            ),
            "elapsed_time": elapsed,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            # Campo "imdf.publish" per coerenza con la struttura del task
            # di enrichment (il frontend lo legge dallo stesso path
            "imdf": {"publish": publish_result},
        }

    except Exception as e:
        logger.error(f"[Task {task_id}] Errore IMDF publish: {e}", exc_info=True)
        # intent: boundary top-level task Celery / outcome: fail-closed sanitizzato
        return {
            "success": False,
            "task_id": task_id,
            "bundle_task_id": bundle_task_id,
            "error": "imdf_publish_failed",
            "message": "Errore pubblicazione IMDF (consulta log)",
            "elapsed_time": round(time.time() - started_at, 2),
        }
