"""
ArcGIS Online — full pipeline E2E test (ESTRAI + CARICA chains).

Esercita i due path "produttivi" tipici contro AGOL reale:

  ESTRAI: progetto canvas ESTRAI con un nodo ``feature_source`` che
          punta a un FeatureLayer reale -> /preview ritorna rows
          letti dall'AGOL via REST.

  CARICA: scrittura completa via service layer (FeaturesService):
          create -> append -> verifica counts. Coverage piu' ampia
          dello smoke che fa solo create.

IMDF publisher: documentato ma SKIPPED. Richiede un
``bundle_task_id`` preprocessato dal servizio esterno
``imdf.plenora.it/api/process`` (non disponibile in env locale).
Lasciamo lo stub cosi' se in futuro hai un bundle in DB la suite
si attiva automatica.

Pre-requisiti: stack docker compose UP + admin login funzionante +
connection ArcGIS persistente in DB con ``ARCGIS_TEST_CONNECTION_ID``
(altrimenti si crea on-the-fly).
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

requests = pytest.importorskip("requests")
pd = pytest.importorskip("pandas")
gpd = pytest.importorskip("geopandas")
from shapely.geometry import Point  # noqa: E402


_ENV_ENABLE = "ARCGIS_TESTS_ENABLED"
_ENV_PORTAL = "ARCGIS_TEST_PORTAL_URL"
_ENV_USERNAME = "ARCGIS_TEST_USERNAME"
_ENV_PASSWORD = "ARCGIS_TEST_PASSWORD"
_ENV_FOLDER = "ARCGIS_TEST_FOLDER"
_ENV_PERSISTENT_CONN_ID = "ARCGIS_TEST_CONNECTION_ID"

_PLENORA_API = os.getenv("PLENORA_API_URL", "http://backend:5004")
_PLENORA_USER = os.getenv("PLENORA_ADMIN_USER", "admin_plenora")
_PLENORA_PASS = os.getenv("PLENORA_ADMIN_PASS", "admin_plenora_2026")

_SERVICE_PREFIX = "plenora_ci_pytest_"


def _enabled_or_skip() -> None:
    if os.getenv(_ENV_ENABLE, "").lower() not in {"1", "true", "yes"}:
        pytest.skip(f"{_ENV_ENABLE}!=1")
    if not os.getenv(_ENV_PORTAL):
        pytest.skip(f"{_ENV_PORTAL} missing")


@pytest.fixture(scope="module")
def admin_token() -> str:
    _enabled_or_skip()
    try:
        r = requests.post(
            f"{_PLENORA_API}/api/auth/login",
            json={"username": _PLENORA_USER, "password": _PLENORA_PASS},
            timeout=10,
        )
    except requests.RequestException as e:
        pytest.skip(f"backend non raggiungibile: {e}")
    if r.status_code != 200:
        pytest.skip(f"login admin fallito: {r.status_code}")
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def arcgis_connection_id(admin_token) -> Iterator[str]:
    """Riusa una connection persistente se ``ARCGIS_TEST_CONNECTION_ID``
    e' settata (path tipico in dev locale), altrimenti la crea
    on-the-fly e la cancella in teardown.
    """
    persistent = os.getenv(_ENV_PERSISTENT_CONN_ID)
    if persistent:
        yield persistent
        return  # nessun teardown, e' persistente by design

    if not os.getenv(_ENV_USERNAME) or not os.getenv(_ENV_PASSWORD):
        pytest.skip(
            f"Ne {_ENV_PERSISTENT_CONN_ID} ne user/password configurati"
        )

    headers = {"Authorization": f"Bearer {admin_token}"}
    payload = {
        "name": f"plenora_ci_pytest_pipeline_{uuid.uuid4().hex[:8]}",
        "description": "Auto-created da test_arcgis_real_pipelines",
        "server_url": os.environ[_ENV_PORTAL].rstrip("/"),
        "server_type": "arcgis_online",
        "auth_type": "token",
        "username": os.environ[_ENV_USERNAME],
        "password": os.environ[_ENV_PASSWORD],
    }
    r = requests.post(
        f"{_PLENORA_API}/api/features/connections",
        headers=headers, json=payload, timeout=15,
    )
    assert r.status_code in (200, 201), f"create conn: {r.status_code} {r.text[:200]}"
    cid = r.json().get("id")
    assert cid
    try:
        yield cid
    finally:
        requests.delete(
            f"{_PLENORA_API}/api/features/connections/{cid}",
            headers=headers, timeout=10,
        )


@pytest.fixture(scope="module")
def arcgis_conn_info(admin_token, arcgis_connection_id):
    """Costruisce ArcGISConnectionInfo a partire dalle env var, in
    modo da poter chiamare direttamente FeaturesService senza passare
    dal DB Plenora (utile per i test CARICA write che esercitano il
    service layer direttamente)."""
    from app.core.features.arcgis_client import ArcGISConnectionInfo
    return ArcGISConnectionInfo(
        server_url=os.environ[_ENV_PORTAL].rstrip("/"),
        server_type="arcgis_online",
        auth_type="token",
        username=os.environ.get(_ENV_USERNAME, ""),
        password=os.environ.get(_ENV_PASSWORD, ""),
    )


# Tests: ESTRAI from AGOL via Plenora HTTP

def test_estrai_from_arcgis_preview_returns_rows(admin_token, arcgis_connection_id):
    """End-to-end: crea progetto ESTRAI con default_connection_id =
    connection AGOL, aggiunge nodo feature_source che punta al primo
    layer disponibile, chiama /preview, verifica rows.

    Coverage: HTTP API → canvas persistence → executor → AGOL REST.
    """
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Trova un FeatureLayer reale da cui leggere.
    r = requests.get(
        f"{_PLENORA_API}/api/features/connections/{arcgis_connection_id}/services",
        headers=headers, timeout=15,
    )
    assert r.status_code == 200
    services = r.json() if isinstance(r.json(), list) else r.json().get("services", [])
    assert services, "nessun FeatureService nella connection per testare ESTRAI"

    # Prendi il primo service e il primo layer.
    svc_url = services[0].get("url") or services[0].get("serviceUrl")
    assert svc_url

    r2 = requests.get(
        f"{_PLENORA_API}/api/features/connections/{arcgis_connection_id}/layers",
        headers=headers,
        params={"service_url": svc_url},
        timeout=15,
    )
    assert r2.status_code == 200
    layers = r2.json().get("layers") if isinstance(r2.json(), dict) else r2.json()
    assert layers, f"nessun layer dentro {svc_url}"
    layer_url = layers[0]["url"]

    # 2. Crea progetto ESTRAI con canvas che ha 1 feature_source.
    project_payload = {
        "name": f"pw_arcgis_estrai_{uuid.uuid4().hex[:8]}",
        "description": "auto-created test",
        "project_type": "estrai",
        "default_connection_id": arcgis_connection_id,
        "canvas": {
            "nodes": [
                {
                    "id": "src1",
                    "type": "feature_source",
                    "position": {"x": 0, "y": 0},
                    "layer_url": layer_url,
                    "selected_columns": [],
                    "where": "1=1",
                },
                {
                    "id": "out1",
                    "type": "result",
                    "position": {"x": 400, "y": 0},
                    "format": "json",
                },
            ],
            "edges": [
                {"id": "e1", "source": "src1", "target": "out1"},
            ],
        },
    }
    r3 = requests.post(
        f"{_PLENORA_API}/api/projects",
        headers=headers, json=project_payload, timeout=20,
    )
    assert r3.status_code in (200, 201), f"create estrai: {r3.status_code} {r3.text[:300]}"
    project_id = r3.json().get("metadata", {}).get("id") or r3.json().get("id")
    assert project_id

    try:
        # 3. Preview: il backend deve query-are l'AGOL reale.
        r4 = requests.get(
            f"{_PLENORA_API}/api/projects/{project_id}/preview",
            headers=headers,
            params={"limit": 5},
            timeout=60,
        )
        assert r4.status_code == 200, (
            f"preview {r4.status_code}: {r4.text[:500]}"
        )
        preview = r4.json()
        # Shape comune: {rows: [...], columns: [...]} o {data: [...]}
        rows = (
            preview.get("rows")
            or preview.get("data")
            or preview.get("preview", {}).get("rows")
            or []
        )
        # NON assertiamo len > 0: il layer puo' essere vuoto. Verifichiamo
        # che la chiamata sia andata a buon fine e non sia una shape error.
        assert isinstance(rows, list), (
            f"preview shape inattesa: {list(preview.keys())[:5]} -> {rows!r}"[:300]
        )
    finally:
        requests.delete(
            f"{_PLENORA_API}/api/projects/{project_id}",
            headers=headers, timeout=10,
        )


# Tests: CARICA write chain (create -> append -> verify)


def test_carica_create_then_append_to_arcgis(arcgis_conn_info):
    """CARICA pipeline equivalente:
      1. create_service da DataFrame iniziale (3 rows)
      2. write_features (append) di altre 2 rows
      3. query verifies total = 5

    Esercita il service layer direttamente per evitare il setup
    canvas (gia' coperto dal test ESTRAI). Cleanup robusto via
    delete_feature_service_items su prefix univoco.
    """
    from app.core.features.arcgis_client import ArcGISClient

    client = ArcGISClient()
    service_name = f"{_SERVICE_PREFIX}carica_{uuid.uuid4().hex[:12]}"

    # 1. Initial create with 3 rows
    initial = gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "tag": ["A", "B", "C"],
            "geometry": [Point(9.0, 45.0), Point(10.0, 46.0), Point(11.0, 47.0)],
        },
        crs="EPSG:4326",
    )

    try:
        create_res = client.create_service(
            arcgis_conn_info,
            service_name=service_name,
            layer_name="carica_chain_test",
            df=initial,
            geometry_column="geometry",
            srid=4326,
            folder_id="",  # root e' OK per il test (cleanup per name)
        )
        assert create_res.get("success"), f"create fallito: {create_res}"
        layer_url = create_res["layer_url"]

        # 2. Append 2 more rows via apply_edits
        rows0, cols0 = client.query_features(
            arcgis_conn_info, layer_url=layer_url, where="1=1", out_fields="*",
        )
        assert len(rows0) == 3

        adds = [
            {
                "attributes": {"id": 4, "tag": "D"},
                "geometry": {"x": 12.0, "y": 48.0, "spatialReference": {"wkid": 4326}},
            },
            {
                "attributes": {"id": 5, "tag": "E"},
                "geometry": {"x": 13.0, "y": 49.0, "spatialReference": {"wkid": 4326}},
            },
        ]
        edit_res = client.apply_edits(
            arcgis_conn_info, layer_url=layer_url, adds=adds, updates=[], deletes=[],
        )
        assert edit_res.get("success") or edit_res.get("addResults"), (
            f"apply_edits append: {edit_res!r}"
        )

        # 3. Re-query: must see 5 total
        rows1, cols1 = client.query_features(
            arcgis_conn_info, layer_url=layer_url, where="1=1", out_fields="*",
        )
        assert len(rows1) == 5, (
            f"append totale atteso 5, ricevute {len(rows1)}: {rows1}"
        )
        id_idx = cols1.index("id")
        ids_present = sorted(int(r[id_idx]) for r in rows1)
        assert ids_present == [1, 2, 3, 4, 5], f"ids: {ids_present}"
    finally:
        # cleanup by name prefix
        try:
            items = client.list_user_items(arcgis_conn_info)
            targets = [
                it for it in items
                if str(it.get("title") or it.get("name") or "") == service_name
            ]
            if targets:
                client.delete_feature_service_items(
                    arcgis_conn_info, targets, service_name=service_name,
                )
        except Exception as e:
            print(f"[arcgis-pipelines] cleanup non-fatal: {e}")


# Tests: IMDF publish (BLOCKED — documented stub)


@pytest.mark.skip(reason=(
        "IMDF publish richiede un bundle preprocessato disponibile via "
        "GET /api/imdf/bundles. In env locale i bundle vengono creati "
        "dal servizio esterno imdf.plenora.it/api/process (D3 pipeline) "
        "che non gira nel docker compose locale. "
        "Per attivare: 1) avere imdf.plenora.it accessibile + un IMDF "
        "zip valido; 2) submit del bundle al processor; 3) wait for "
        "completion; 4) rimuovere questo skip e usare bundle_task_id."
    ))
def test_imdf_publish_to_arcgis_real(admin_token, arcgis_connection_id):
    """Publish di un IMDF preprocessato come FeatureService AGOL.

    Stub documentale: il flow e' validato in unit test
    (``tests2/imdf/``) con bundle mock; qui servirebbe il vero
    pipeline imdf.plenora.it+publish, oggi non riproducibile locale.
    """
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = requests.get(
        f"{_PLENORA_API}/api/imdf/bundles", headers=headers, timeout=10,
    )
    assert r.status_code == 200
    bundles = r.json() if isinstance(r.json(), list) else r.json().get("bundles", [])
    if not bundles:
        pytest.skip("Nessun bundle IMDF pre-processato in DB")

    bundle_id = bundles[0].get("bundle_task_id") or bundles[0].get("id")
    fs_name = f"{_SERVICE_PREFIX}imdf_{uuid.uuid4().hex[:8]}"

    r2 = requests.post(
        f"{_PLENORA_API}/api/imdf/publish",
        headers=headers,
        json={
            "bundle_task_id": bundle_id,
            "connection_id": arcgis_connection_id,
            "fs_name": fs_name,
            "folder": os.getenv(_ENV_FOLDER, "test_plenora"),
            "mode": "create",
            "apply_renderers": True,
        },
        timeout=120,
    )
    assert r2.status_code == 200, f"publish: {r2.status_code} {r2.text[:300]}"
    # TODO: cleanup del FeatureService creato
