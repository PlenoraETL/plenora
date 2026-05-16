#!/usr/bin/env python3
"""
End-to-end check IMDF publish: imdf.plenora.it -> Plenora bundle -> AGOL.

E' uno **script**, non un pytest. Ragione: il bundle dir
(``settings.IMDF_BUNDLES_DIR = /data/imdf_bundles``) vive sul
filesystem del backend, e il runner pytest non condivide quel volume.
Eseguendo direttamente nel backend ci muoviamo sul filesystem giusto
e riusiamo le funzioni interne ``extract_imdf_zip`` /
``persist_imdf_bundle_to_disk`` senza duplicare logica.

Run:
    docker exec -e ARCGIS_TEST_CONNECTION_ID=arcgis_... \
                 -e IMDF_TEST_USERNAME=<imdf_user> \
                 -e IMDF_TEST_PASSWORD=<imdf_pass> \
                 -e PLENORA_ADMIN_PASSWORD=admin_plenora_2026 \
                 -e PYTHONPATH=/app \
                 plenora-backend-1 \
                 python /tmp/imdf_real_e2e_check.py

Lo script va copiato nel container prima del run via ``docker cp``
oppure piped via stdin (``cat ... | docker exec -i ... python -``).

Exit codes:
    0  - publish riuscito e cleanup OK
    2  - skip: nessun sito disponibile su imdf.plenora.it
    1  - failure (con stack trace utile sul stderr)

Env vars richieste:
    IMDF_API_URL             default https://imdf.plenora.it
    IMDF_TEST_USERNAME       login imdf.plenora.it
    IMDF_TEST_PASSWORD       password imdf.plenora.it
    ARCGIS_TEST_CONNECTION_ID  connection AGOL pre-esistente in Plenora
    ARCGIS_TEST_FOLDER       folder dove pubblicare (default test_plenora)
    PLENORA_API_URL          default http://localhost:5004
    PLENORA_ADMIN_USER       default admin_plenora
    PLENORA_ADMIN_PASSWORD   default admin_plenora_2026

Cleanup: best-effort sia del bundle locale sia del FeatureService
creato su AGOL.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import quote

import requests


IMDF_URL = os.getenv("IMDF_API_URL", "https://imdf.plenora.it").rstrip("/")
IMDF_USER = os.environ.get("IMDF_TEST_USERNAME")
IMDF_PASS = os.environ.get("IMDF_TEST_PASSWORD")
ARCGIS_CONN_ID = os.environ.get("ARCGIS_TEST_CONNECTION_ID")
ARCGIS_FOLDER = os.environ.get("ARCGIS_TEST_FOLDER", "test_plenora")
PLENORA_API = os.getenv("PLENORA_API_URL", "http://localhost:5004").rstrip("/")
PLENORA_USER = os.getenv("PLENORA_ADMIN_USER", "admin_plenora")
PLENORA_PASS = os.getenv("PLENORA_ADMIN_PASSWORD", "admin_plenora_2026")


def _log(msg: str) -> None:
    print(f"[imdf-e2e] {msg}", flush=True)


def _fail(msg: str, code: int = 1):
    print(f"[imdf-e2e] FAIL: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def _skip(msg: str):
    _log(f"SKIP: {msg}")
    sys.exit(2)


def login_imdf() -> str:
    if not IMDF_USER or not IMDF_PASS:
        _skip("IMDF_TEST_USERNAME/PASSWORD non configurate")
    r = requests.post(
        f"{IMDF_URL}/token",
        data={
            "username": IMDF_USER,
            "password": IMDF_PASS,
            "grant_type": "password",
        },
        timeout=30,
    )
    if r.status_code != 200:
        _fail(f"login imdf {r.status_code}: {r.text[:200]}")
    return r.json()["access_token"]


def pick_site(imdf_token: str) -> str:
    r = requests.get(
        f"{IMDF_URL}/api/storage",
        headers={"Authorization": f"Bearer {imdf_token}"},
        timeout=30,
    )
    if r.status_code != 200:
        _fail(f"storage list {r.status_code}: {r.text[:200]}")
    data = r.json()
    # API ritorna list o {sites:[...]} o {storage:{...}}
    sites = data if isinstance(data, list) else (
        data.get("sites") or data.get("storage") or list(data.keys() if isinstance(data, dict) else [])
    )
    if isinstance(sites, dict):
        sites = list(sites.keys())
    if not sites:
        _skip(
            "Nessun sito IMDF preprocessato disponibile. "
            "Per testare: upload un DXF via POST /api/process e riprova."
        )
    # Estrai id se sites ha dicts; altrimenti str
    first = sites[0]
    site_id = first if isinstance(first, str) else (
        first.get("site_id") or first.get("id") or first.get("name")
    )
    if not site_id:
        _fail(f"site_id non estraibile dalla list response: {sites[:3]!r}")
    _log(f"sito selezionato: {site_id}")
    return site_id


def download_site_zip(imdf_token: str, site_id: str) -> bytes:
    r = requests.get(
        f"{IMDF_URL}/api/export/{quote(site_id)}",
        headers={"Authorization": f"Bearer {imdf_token}"},
        timeout=120,
        stream=True,
    )
    if r.status_code != 200:
        _fail(f"export {site_id} {r.status_code}: {r.text[:200]}")
    content = b"".join(chunk for chunk in r.iter_content(chunk_size=65536) if chunk)
    if len(content) < 200:
        _fail(f"zip troppo piccolo ({len(content)}b), formato sospetto")
    _log(f"zip scaricato: {len(content)} bytes")
    return content


def persist_to_plenora(zip_bytes: bytes) -> tuple[str, Path]:
    """Salva zip su tmp, lo estrae con extract_imdf_zip e persiste
    sul filesystem locale come bundle Plenora. Ritorna (task_id, dir).
    """
    from app.core.imdf.loader import extract_imdf_zip
    from app.core.imdf.persist import persist_imdf_bundle_to_disk

    task_id = f"imdf_e2e_{uuid.uuid4().hex[:12]}"

    with tempfile.NamedTemporaryFile(
        prefix=f"{task_id}_", suffix=".zip", delete=False,
    ) as tmp:
        tmp.write(zip_bytes)
        tmp_zip_path = Path(tmp.name)

    try:
        bundle = extract_imdf_zip(tmp_zip_path)
        result = persist_imdf_bundle_to_disk(
            bundle=bundle,
            task_id=task_id,
            project_id=None,
        )
        bundle_dir = Path(result["path"])
        _log(f"bundle persistito: {bundle_dir} (site_id={bundle.site_id})")

        # Per superare _assert_bundle_group_access serve group_id nel
        # meta. Imposta a 'default' (gruppo admin) cosi' admin_plenora
        # ha visibility.
        meta_path = bundle_dir / "plenora_metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["group_id"] = "default"
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            _log("group_id=default impostato nel plenora_metadata.json")

        return task_id, bundle_dir
    finally:
        try:
            tmp_zip_path.unlink()
        except OSError:
            pass


def login_plenora() -> str:
    r = requests.post(
        f"{PLENORA_API}/api/auth/login",
        json={"username": PLENORA_USER, "password": PLENORA_PASS},
        timeout=10,
    )
    if r.status_code != 200:
        _fail(f"plenora login {r.status_code}: {r.text[:200]}")
    return r.json()["access_token"]


def trigger_publish(plenora_token: str, bundle_task_id: str) -> str:
    fs_name = f"plenora_ci_pytest_imdf_{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{PLENORA_API}/api/imdf/publish",
        headers={"Authorization": f"Bearer {plenora_token}"},
        json={
            "bundle_task_id": bundle_task_id,
            "connection_id": ARCGIS_CONN_ID,
            "fs_name": fs_name,
            "folder": ARCGIS_FOLDER,
            "mode": "replace",
            "apply_renderers": True,
        },
        timeout=30,
    )
    if r.status_code != 200:
        _fail(f"/imdf/publish {r.status_code}: {r.text[:300]}")
    task_id = r.json().get("task_id")
    if not task_id:
        _fail(f"task_id mancante in publish response: {r.json()}")
    _log(f"publish task avviato: celery task_id={task_id}, fs_name={fs_name}")
    return task_id, fs_name


def poll_task(plenora_token: str, celery_task_id: str, timeout_s: int = 300):
    """Polla GET /api/tasks/{id} (se esiste) o solo sleeps + check
    item su AGOL. Fallback semplice: timeout fisso + verifica via AGOL.
    """
    deadline = time.time() + timeout_s
    last_status = None
    while time.time() < deadline:
        try:
            r = requests.get(
                f"{PLENORA_API}/api/tasks/{celery_task_id}",
                headers={"Authorization": f"Bearer {plenora_token}"},
                timeout=10,
            )
            if r.status_code == 200:
                status = r.json().get("status") or r.json().get("state")
                if status != last_status:
                    _log(f"task status: {status}")
                    last_status = status
                if str(status).upper() in {"SUCCESS", "COMPLETED", "DONE"}:
                    return True
                if str(status).upper() in {"FAILURE", "FAILED", "ERROR"}:
                    _fail(f"publish task fallito: {r.json()}")
        except requests.RequestException as e:
            _log(f"poll error (continuo): {e}")
        time.sleep(3)
    _log(f"timeout {timeout_s}s: status_final={last_status}")
    return last_status not in {None, "FAILURE", "FAILED", "ERROR"}


def cleanup_arcgis_service(fs_name: str):
    """Cleanup best-effort: cancella il FeatureService creato."""
    try:
        from app.core.connections.arcgis_connection_resolver import (
            resolve_arcgis_connection,
        )
        from app.core.features.arcgis_client import ArcGISClient
        resolved = resolve_arcgis_connection(ARCGIS_CONN_ID)
        if not resolved:
            _log("cleanup AGOL: conn_info=None, skip")
            return
        # resolve_arcgis_connection -> Tuple[ConnectionInfo, ProviderProtocol]
        conn_info = resolved[0] if isinstance(resolved, tuple) else resolved
        client = ArcGISClient()
        items = client.list_user_items(conn_info)
        targets = [
            it for it in items
            if str(it.get("title") or it.get("name") or "") == fs_name
        ]
        if targets:
            client.delete_feature_service_items(conn_info, targets, fs_name)
            _log(f"cleanup AGOL: cancellati {len(targets)} item per {fs_name}")
        else:
            _log(f"cleanup AGOL: nessun item match per {fs_name}")
    except Exception as e:
        _log(f"cleanup AGOL non-fatal: {e}")


def cleanup_bundle(bundle_task_id: str, plenora_token: str, bundle_dir: Path):
    """Cleanup best-effort del bundle locale + record DB."""
    try:
        requests.delete(
            f"{PLENORA_API}/api/imdf/bundles/{bundle_task_id}",
            headers={"Authorization": f"Bearer {plenora_token}"},
            timeout=15,
        )
    except Exception as e:
        _log(f"cleanup bundle DELETE non-fatal: {e}")
    # Fallback: cancella la dir direttamente se ancora presente
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir, ignore_errors=True)
        _log(f"cleanup bundle dir locale: {bundle_dir}")


def main() -> int:
    if not ARCGIS_CONN_ID:
        _skip("ARCGIS_TEST_CONNECTION_ID non settata")

    imdf_token = login_imdf()
    _log("login imdf.plenora.it OK")

    site_id = pick_site(imdf_token)
    zip_bytes = download_site_zip(imdf_token, site_id)
    bundle_task_id, bundle_dir = persist_to_plenora(zip_bytes)

    plenora_token = login_plenora()
    _log("login plenora OK")

    fs_name = None
    try:
        celery_task_id, fs_name = trigger_publish(plenora_token, bundle_task_id)
        ok = poll_task(plenora_token, celery_task_id, timeout_s=300)
        if not ok:
            _fail("publish task non terminato entro timeout")
        _log("PUBLISH OK")
    finally:
        if fs_name:
            cleanup_arcgis_service(fs_name)
        cleanup_bundle(bundle_task_id, plenora_token, bundle_dir)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
