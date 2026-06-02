#!/usr/bin/env python3
"""E2E IMDF REALE (una-run) — DXF -> bundle (plenora-api) -> publish AGOL.

Gated da IMDF_REAL_E2E=1 (gira solo con credenziali reali). NON si chiama
`test_*` apposta: run_all.py non lo raccoglie (hit su servizi esterni reali:
plenora-api convertitore + ArcGIS Online).

Canvas una-run (project_type=arricchisci):
    file_source(DXF) -> api_enrich(rest_file -> plenora-api /api/process
    ?output_format=bundle, output_kind=imdf) -> result -> carica_arcgis(
    create_service su AGOL).

Verifica: la conversione produce un bundle multi-layer valido e carica_arcgis
crea un FeatureService con N layer sull'org AGOL reale. Cleanup best-effort
(progetto/connessione/file_source in Plenora + FeatureService su AGOL).

Credenziali (org di test usa-e-getta; default overridabili da env):
    PLENORA  :5004  admin            -> plenora_admin / plenora_admin_2026!
    plenora-api convertitore /token  -> marco_bonamente / secret
    AGOL connection (in :5004)       -> ARCGIS_TEST_CONNECTION_ID
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# --- config (default overridabili) ---
BASE = os.environ.get("PLENORA_E2E_API_BASE_URL", "http://127.0.0.1:5004/api").rstrip("/")
ADMIN_USER = os.environ.get("PLENORA_E2E_USERNAME", "plenora_admin")
ADMIN_PASS = os.environ.get("PLENORA_E2E_PASSWORD", "plenora_admin_2026!")

# convertitore IMDF (plenora-api) — URL raggiungibile dal WORKER (rete docker)
IMDF_PROCESS_URL = os.environ.get(
    "IMDF_PROCESS_URL", "http://plenora-api:5002/api/process?output_format=bundle"
)
IMDF_TOKEN_URL = os.environ.get("IMDF_TOKEN_URL", "http://plenora-api:5002/token")
IMDF_USER = os.environ.get("IMDF_TEST_USERNAME", "marco_bonamente")
IMDF_PASS = os.environ.get("IMDF_TEST_PASSWORD", "secret")

# AGOL connection esistente nello stack :5004 + target publish
ARCGIS_CONN_ID = os.environ.get(
    "ARCGIS_TEST_CONNECTION_ID", "arcgis_e2e_imdf_agol_urby_20260602_082425_9c718d1a"
)
ARCGIS_FOLDER = os.environ.get("ARCGIS_TEST_FOLDER", "test_plenora")
# AGOL diretto (read-only) per VERIFICARE il publish (layer/feature reali)
AGOL_PORTAL = os.environ.get("ARCGIS_TEST_PORTAL_URL", "https://urbyetorbit2.maps.arcgis.com").rstrip("/")
AGOL_USER = os.environ.get("ARCGIS_TEST_USERNAME", "urbydeveloper3")
AGOL_PASS = os.environ.get("ARCGIS_TEST_PASSWORD", "MaGiMaAl2015")
MIN_LAYERS = int(os.environ.get("IMDF_MIN_LAYERS", "5"))
MIN_FEATURES = int(os.environ.get("IMDF_MIN_FEATURES", "500"))

DXF_PATH = os.environ.get(
    "IMDF_LOCAL_DXF",
    "/home/marcob/Scrivania/plenora/backend/test-e2e/files/imdf/dxf/LO2244_FV01_PF00_GEO001.dxf",
)
# layer non vuoti attesi nel bundle (da verifica stadio-1)
EXPECTED_NONEMPTY_LAYERS = {"Localita", "OperaPrincipale", "Livello", "Locale", "Details"}


def log(m: str) -> None:
    print(f"[imdf-real-e2e] {m}", flush=True)


def _agol_token() -> str:
    import urllib.parse
    data = urllib.parse.urlencode({
        "username": AGOL_USER, "password": AGOL_PASS,
        "referer": "https://www.arcgis.com", "f": "json",
    }).encode()
    with urllib.request.urlopen(AGOL_PORTAL + "/sharing/rest/generateToken", data=data, timeout=30) as r:
        return json.loads(r.read().decode()).get("token", "")


def _agol_verify_publish(service_name: str) -> tuple[int, int]:
    """Ritorna (n_layer, n_feature_totali) del FeatureServer pubblicato.

    Verifica REALE (non basta che il nome compaia): interroga il servizio +
    conta le feature per layer. Cosi' un publish che scrive solo il layer 0
    (bug add_layer_to_service) NON passa.
    """
    tok = _agol_token()
    q = urllib.parse.quote(f"owner:{AGOL_USER} AND title:{service_name}")
    with urllib.request.urlopen(
        f"{AGOL_PORTAL}/sharing/rest/search?q={q}&f=json&token={tok}&num=10", timeout=30
    ) as r:
        results = json.loads(r.read().decode()).get("results", [])
    item = next((x for x in results if x.get("title") == service_name and "Feature" in x.get("type", "")), None)
    if not item:
        return 0, 0
    url = item.get("url", "")
    with urllib.request.urlopen(f"{url}?f=json&token={tok}", timeout=30) as r:
        layers = json.loads(r.read().decode()).get("layers", [])
    total = 0
    for lyr in layers:
        lid = lyr.get("id")
        with urllib.request.urlopen(
            f"{url}/{lid}/query?where=1=1&returnCountOnly=true&f=json&token={tok}", timeout=30
        ) as r:
            total += int(json.loads(r.read().decode()).get("count", 0) or 0)
    return len(layers), total


def fail(m: str):
    print(f"[imdf-real-e2e] FAIL: {m}", file=sys.stderr, flush=True)
    sys.exit(1)


def _req(method, path, *, token=None, payload=None, raw=None, headers=None, timeout=120):
    url = BASE + path if path.startswith("/") else path
    data = None
    hdr = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        hdr["Content-Type"] = "application/json"
    elif raw is not None:
        data, ct = raw
        hdr["Content-Type"] = ct
    if token:
        hdr["Authorization"] = "Bearer " + token
    if headers:
        hdr.update(headers)
    r = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "----imdf" + uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields.items():
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        buf.write(f"{v}\r\n".encode())
    for k, (fname, content) in files.items():
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(
            f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'.encode()
        )
        buf.write(b"Content-Type: application/octet-stream\r\n\r\n")
        buf.write(content)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    if os.environ.get("IMDF_REAL_E2E", "").lower() not in ("1", "true", "yes"):
        log("SKIP: set IMDF_REAL_E2E=1 per eseguire (hit su servizi esterni reali)")
        return 0
    if not Path(DXF_PATH).exists():
        fail(f"DXF non trovato: {DXF_PATH}")

    suffix = uuid.uuid4().hex[:8]
    service_name = f"e2e_imdf_{suffix}"
    project_id = None
    api_id = None
    file_source_id = None

    # 1. login Plenora admin
    st, tok = _req("POST", "/auth/login", payload={"username": ADMIN_USER, "password": ADMIN_PASS})
    if st != 200 or "access_token" not in tok:
        fail(f"login plenora {st}: {tok}")
    token = tok["access_token"]
    log("login plenora OK")

    # 1b. risolvi il folder AGOL nome->id (create_service vuole l'ID, non il titolo)
    folder_id = ARCGIS_FOLDER
    st, fld = _req("GET", f"/features/connections/{ARCGIS_CONN_ID}/folders", token=token)
    if st == 200 and isinstance(fld, dict):
        for f in fld.get("folders", []):
            if f.get("title") == ARCGIS_FOLDER or f.get("id") == ARCGIS_FOLDER:
                folder_id = f.get("id", "")
                break
    log(f"folder AGOL risolto: '{ARCGIS_FOLDER}' -> id '{folder_id}'")

    try:
        # 2. upload DXF -> file_source
        dxf_bytes = Path(DXF_PATH).read_bytes()
        body, ct = _multipart(
            {"name": f"e2e_imdf_dxf_{suffix}", "description": "E2E IMDF DXF"},
            {"file": (Path(DXF_PATH).name, dxf_bytes)},
        )
        st, fs = _req("POST", "/files/upload", token=token, raw=(body, ct))
        if st != 201 or not isinstance(fs, dict) or not fs.get("id"):
            fail(f"upload dxf {st}: {fs}")
        file_source_id = fs["id"]
        log(f"file_source DXF caricato: {file_source_id}")

        # 3. connessione rest_file -> plenora-api (oauth2_password)
        st, conn = _req("POST", "/api-connections", token=token, payload={
            "name": f"e2e_imdf_converter_{suffix}",
            "description": "IMDF DXF->bundle converter (rest_file)",
            "api_type": "rest_file",
            "connection_type": "rest_file",
            "category": "e2e",
            "url": IMDF_PROCESS_URL,
            "method": "POST",
            "auth": {
                "type": "oauth2_password",
                "token_url": IMDF_TOKEN_URL,
                "username": IMDF_USER,
                "password": IMDF_PASS,
                "grant_type": "password",
            },
            "request": {
                "file_fields": [{"name": "file", "source": "node_input", "required": True}],
                "static_fields": [],
                "timeout": 600,
            },
            "response": {"format": "binary_file", "output_extension": ".json"},
        })
        if st != 201 or not isinstance(conn, dict) or not conn.get("id"):
            fail(f"create rest_file connection {st}: {conn}")
        api_id = conn["id"]
        log(f"connessione rest_file creata: {api_id}")

        # 4. canvas una-run + progetto arricchisci
        canvas = {
            "nodes": [
                {
                    "id": "file-1", "type": "file_source", "position": {"x": 0, "y": 0},
                    "file_source_id": file_source_id,
                    "data": {"file_source_id": file_source_id},
                },
                {
                    "id": "enrich-1", "type": "api_enrich", "position": {"x": 250, "y": 0},
                    "api_id": api_id, "output_kind": "imdf",
                    "param_mapping": {}, "output_mapping": [],
                    "data": {"api_id": api_id, "output_kind": "imdf",
                             "param_mapping": {}, "output_mapping": []},
                },
                {
                    "id": "result-1", "type": "result", "position": {"x": 500, "y": 0},
                    "format": "json", "fileName": f"e2e_imdf_bundle_{suffix}",
                    "data": {"format": "json", "fileName": f"e2e_imdf_bundle_{suffix}"},
                },
                {
                    "id": "carica-1", "type": "carica_arcgis", "position": {"x": 750, "y": 0},
                    "dest_type": "arcgis",
                    "arcgis_connection_id": ARCGIS_CONN_ID,
                    "arcgis_operation": "create_service",
                    "arcgis_new_service_name": service_name,
                    "arcgis_folder_id": folder_id,
                    "source_srid": 4326,
                    "data": {
                        "dest_type": "arcgis",
                        "arcgis_connection_id": ARCGIS_CONN_ID,
                        "arcgis_operation": "create_service",
                        "arcgis_new_service_name": service_name,
                        "arcgis_folder_id": folder_id,
                        "source_srid": 4326,
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "file-1", "target": "enrich-1"},
                {"id": "e2", "source": "enrich-1", "target": "result-1"},
                {"id": "e3", "source": "result-1", "target": "carica-1"},
            ],
        }
        st, proj = _req("POST", "/projects", token=token, payload={
            "name": f"e2e_imdf_unarun_{suffix}", "project_type": "arricchisci", "canvas": canvas,
        })
        project_id = (
            (proj.get("id") if isinstance(proj, dict) else None)
            or (proj.get("metadata", {}).get("id") if isinstance(proj, dict) else None)
        )
        if st not in (200, 201) or not project_id:
            fail(f"create project {st}: {proj}")
        log(f"progetto creato: {project_id}")

        # 5. validate
        st, rep = _req("POST", f"/projects/{project_id}/validate", token=token)
        if st != 200:
            fail(f"validate {st}: {rep}")
        if rep.get("has_errors"):
            fail(f"canvas non valido: {rep.get('issues')}")
        log("validate OK (canvas valido)")

        # 6. execute (arricchisci converte via plenora-api + dispatch carica_arcgis)
        st, ex = _req("POST", f"/projects/{project_id}/execute", token=token,
                      payload={"force_recalculate": True}, timeout=600)
        if st != 200:
            fail(f"execute {st}: {ex}")
        log(f"execute avviato: success={ex.get('success')} rows={ex.get('rows_count')}")
        if ex.get("success") is False:
            fail(f"arricchisci fallito: {ex.get('message')}")

        # 7. verifica REALE (poll): il FeatureServer pubblicato deve avere
        #    >= MIN_LAYERS layer e >= MIN_FEATURES feature totali. Contare i
        #    layer/feature (non il solo nome) intercetta un publish multi-layer
        #    rotto che scrive solo il layer 0.
        n_layers = n_feat = 0
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                n_layers, n_feat = _agol_verify_publish(service_name)
            except Exception as exc:
                log(f"verifica AGOL retry ({exc})")
                n_layers = n_feat = 0
            if n_layers >= MIN_LAYERS and n_feat >= MIN_FEATURES:
                break
            time.sleep(10)
        log(f"AGOL publish: {n_layers} layer, {n_feat} feature totali")
        if n_layers < MIN_LAYERS:
            fail(f"publish multi-layer incompleto: {n_layers} layer (atteso >= {MIN_LAYERS})")
        if n_feat < MIN_FEATURES:
            fail(f"feature scritte insufficienti: {n_feat} (atteso >= {MIN_FEATURES}) "
                 f"— possibile publish del solo layer 0")

        log(f"PASS: una-run IMDF reale -> publish AGOL OK ({n_layers} layer, {n_feat} feature)")
        return 0
    finally:
        # cleanup best-effort
        if project_id:
            _req("DELETE", f"/projects/{project_id}", token=token)
        if api_id:
            _req("DELETE", f"/api-connections/{api_id}", token=token)
        if file_source_id:
            _req("DELETE", f"/files/{file_source_id}", token=token)
        log(f"cleanup Plenora fatto (FeatureService '{service_name}' su AGOL: cancella a mano se serve)")


if __name__ == "__main__":
    raise SystemExit(main())
