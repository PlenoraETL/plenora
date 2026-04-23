"""
persist.py — Persistenza di un ImdfBundle estratto su disco.

Estratto da workers.tasks.enrichment_task per isolare la logica di move
verso `IMDF_BUNDLES_DIR/{task_id}/` e garantire tramite `try/finally` il
cleanup SEMPRE della tmp_dir — anche su errori di `shutil.move` che in
passato lasciavano orfani tipo `/tmp/imdf_XXXX/` su disco.

Boundary (D3, `orchestrate/phase_1_stabilization/d3_imdf_boundaries.md`):
Persist e' **local materializer puro**. Non conosce S3 ne' object storage
(R2): l'orchestrazione persist -> storage vive nel worker
(enrichment_task). Non riderivalo `site_id` (R4): legge `bundle.site_id`
autoritativo dal loader.

Uso:
    from app.core.imdf.loader import extract_imdf_zip
    from app.core.imdf.persist import persist_imdf_bundle_to_disk

    bundle = extract_imdf_zip(str(zip_path))
    result = persist_imdf_bundle_to_disk(bundle, task_id, project_id)
    # bundle.tmp_dir e' stata cancellata in ogni caso

Schema `plenora_metadata.json` (contratto stabile, letto da router/
storage/validator):

  Campi sempre presenti:
    project_id:    str | None   # canvas che ha generato il bundle
    task_id:       str          # id Celery del task
    site_id:       str          # da bundle.site_id (R4: "" se euristica
                                # fallita, mai None)
    saved_at:      str          # timestamp ISO 8601 UTC
    has_gdb:       bool
    has_ifc:       bool
    has_renderers: bool

  Campi opzionali:
    validation_summary: {passed: bool, total_errors: int,
                         total_warnings: int}
                                # presente se validation.json leggibile
    building_count: int         # presente se {site_id}/building.geojson
                                # esiste nel bundle
    level_count:    int         # idem per level.geojson
    unit_count:     int         # idem per unit.geojson

Invariant (D3 commit 5, §9.6): `plenora_metadata.json` e' sempre presente
nel dest dir. Se il write fallisce a meta' persist esegue rollback della
dest dir (shutil.rmtree) e solleva — mai lasciare una bundle dir senza
meta (una dir senza meta e' inaccessibile per ACL:
router._assert_bundle_group_access fail-closed). Il worker
(enrichment_task) cattura l'eccezione e logga warning senza bloccare
il task (bundle marcato non-persistito, semantic end-user invariata).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _count_features(p: Path) -> Optional[int]:
    """Conta feature in un GeoJSON senza caricarlo interamente come oggetto.
    Ritorna None su errore/file mancante."""
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        feats = data.get("features")
        return len(feats) if isinstance(feats, list) else None
    except Exception as e:
        logger.warning(f"_count_features {p.name}: {e}")
        return None


def persist_imdf_bundle_to_disk(
    bundle,
    task_id: str,
    project_id: Optional[str],
    dest_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Sposta il contenuto di `bundle.tmp_dir` in
    `IMDF_BUNDLES_DIR/{task_id}/` e scrive `plenora_metadata.json` nella
    root della destinazione.

    - **Garantisce** che `bundle.tmp_dir` sia cancellata in OGNI caso
      (happy path o errore), tramite un `try/finally`. Questo previene
      gli orfani storici tipo `/tmp/imdf_XXXX/` rimasti dopo crash del
      worker a metà del move.
    - Se la destinazione esiste già viene rimossa prima di creare la
      nuova (idempotenza su retry del task).
    - Il `plenora_metadata.json` include `site_id`, counts layer dai
      geojson, validation_summary, timestamp `saved_at`.

    Args:
        bundle:      ImdfBundle (da `extract_imdf_zip`)
        task_id:     id univoco che nomina la cartella di destinazione
        project_id:  project_id del canvas (per filtering lato list)
        dest_root:   override del root di destinazione (per test)

    Returns:
        dict con chiavi:
            - path: str del dest_root finale
            - meta: dict del plenora_metadata scritto

    Raises:
        Propaga eventuali eccezioni da shutil.move / write. La tmp_dir è
        comunque cancellata dal finally.
    """
    if dest_root is None:
        # Import tardivo per rispettare i monkeypatch di test
        from app.config import settings as _settings
        root = _settings.IMDF_BUNDLES_DIR / task_id
    else:
        root = Path(dest_root)
    try:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

        if bundle.tmp_dir and bundle.tmp_dir.exists():
            # Materializzo la lista perche' `iterdir` e' un generator lazy:
            # se modifichiamo la directory durante l'iterazione (con
            # shutil.move) potremmo saltare elementi o ricevere OSError.
            for item in list(bundle.tmp_dir.iterdir()):
                target = root / item.name
                shutil.move(str(item), str(target))

        # ── metadata ──────────────────────────────────────────────
        meta: Dict[str, Any] = {
            "project_id":    project_id,
            "task_id":       task_id,
            "site_id":       bundle.site_id,
            "saved_at":      datetime.now(timezone.utc).isoformat(),
            "has_gdb":       bundle.gdb_path is not None,
            "has_ifc":       bundle.ifc_path is not None,
            "has_renderers": bundle.renderers_path is not None,
        }

        # Validation summary: leggiamo dalla DESTINAZIONE perché il move
        # ha già trasferito validation.json (i path dentro `bundle`
        # puntavano alla vecchia tmp_dir, ormai vuota).
        try:
            val_path = root / "validation.json"
            if val_path.exists():
                val = json.loads(val_path.read_text(encoding="utf-8")) or {}
                meta["validation_summary"] = {
                    "passed":         val.get("passed"),
                    "total_errors":   val.get("total_errors"),
                    "total_warnings": val.get("total_warnings"),
                }
        except Exception as ve:
            logger.warning(f"[{task_id}] validation_summary read: {ve}")

        # Counts layer (fallback per il list endpoint se il bundle li ha
        # in formato geojson riconoscibile). Cerca in {site_id}/*.geojson.
        if bundle.site_id:
            site_dir = root / bundle.site_id
            if site_dir.is_dir():
                bc = _count_features(site_dir / "building.geojson")
                lc = _count_features(site_dir / "level.geojson")
                uc = _count_features(site_dir / "unit.geojson")
                if bc is not None: meta["building_count"] = bc
                if lc is not None: meta["level_count"] = lc
                if uc is not None: meta["unit_count"] = uc

        try:
            (root / "plenora_metadata.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
        except Exception as me:
            # D3 commit 5 (§9.6): invariant "meta sempre presente nel dest
            # dir" enforcement. Senza meta la dir e' inaccessibile per ACL
            # (router._assert_bundle_group_access fail-closed 403). Rollback
            # della dest dir e re-raise: il worker (enrichment_task) gia'
            # cattura persist failure e logga warning senza bloccare il task.
            logger.warning(
                f"[{task_id}] plenora_metadata.json write fallito, "
                f"rollback dest: {me}"
            )
            shutil.rmtree(root, ignore_errors=True)
            raise

        logger.info(
            f"[{task_id}] IMDF bundle persistito in {root} "
            f"(site={bundle.site_id})"
        )

        # D3 commit 4 (R2): la chiamata inline a storage.save_bundle e' stata
        # rimossa da qui. L'orchestrazione "persist locale + push remoto" vive
        # nel worker (enrichment_task) come passi espliciti: dopo questa
        # funzione, il caller chiama storage.save_bundle. Persist resta local
        # materializer puro, non conosce S3 ne' object storage.

        return {"path": str(root), "meta": meta}

    finally:
        # Cleanup SEMPRE garantito: se il happy path ha già svuotato
        # tmp_dir via shutil.move, bundle.cleanup() è un no-op. Se è
        # fallito a metà, rimuoviamo quel che resta. Previene gli
        # orfani /tmp/imdf_XXXX/ storici.
        try:
            bundle.cleanup()
        except Exception as ce:
            logger.warning(f"[{task_id}] bundle.cleanup in finally: {ce}")
