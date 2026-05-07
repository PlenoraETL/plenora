"""Integration smoke test — C12 Bearer token su ArcGIS Online reale.

Verifica end-to-end che il nuovo _request_with_token funzioni contro
un vero server ArcGIS Online:
1. authenticate() ottiene un token classico via generateToken
2. list_services passa via Bearer header e ArcGIS Online accetta
3. list_layers su un Feature Service carica i layer
4. query_features su un layer esegue una GET paginata

Scope: READ-ONLY. Niente addFeatures/deleteFeatures per non toccare
dati di produzione. Se serve copertura write, passare
ARCGIS_TEST_DISPOSABLE_LAYER_URL via env.

Skip automatico se:
- la connection id hard-coded non esiste nel DB produzione, oppure
- DATABASE_URL o PLENORA_ENCRYPTION_KEY (o l'alias legacy URBY_ENCRYPTION_KEY)
  non sono configurati localmente (impedisce di decifrare la password dalla
  tabella arcgis_connections).
"""

from __future__ import annotations

import os
from typing import Optional

import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.security,
    pytest.mark.contract,
]

# Connection id presente in prod (letto via query preliminare in chat C12).
# Se non c'e' piu', i test skippano.
_CONN_ID = "arcgis_Arcgis_Online_Plenora_20260209_122921_6f62aab7"


def _fetch_conn_or_skip():
    """Prova a caricare la connection dal DB. Skip su qualsiasi errore
    (db non raggiungibile, id non presente, encryption key mancante)."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL non configurato localmente")
    try:
        from app.core.features.features_service import get_connection_db
        conn = get_connection_db(_CONN_ID)
    except Exception as e:
        pytest.skip(f"Impossibile caricare connection da DB: {e}")
    if conn is None:
        pytest.skip(f"Connection {_CONN_ID} non trovata nel DB")
    if not (conn.password or conn.client_secret):
        pytest.skip("Connection senza credenziali utilizzabili")
    return conn


def _client_and_conn_info():
    """Costruisce ArcGISClient + ArcGISConnectionInfo dalla connection DB."""
    db_conn = _fetch_conn_or_skip()
    from app.core.features.arcgis_client import (
        ArcGISClient, ArcGISConnectionInfo,
    )
    conn_info = ArcGISConnectionInfo(
        server_url=db_conn.server_url,
        server_type=db_conn.server_type or "arcgis_online",
        auth_type=db_conn.auth_type or "token",
        username=db_conn.username or "",
        password=db_conn.password or "",
        client_id=db_conn.client_id or "",
        client_secret=db_conn.client_secret or "",
        token_url=db_conn.token_url or "",
    )
    return ArcGISClient(), conn_info


# Scenari


def test_authenticate_returns_token_via_generateToken():
    """Smoke: la chiamata generateToken risponde 200 + token non vuoto."""
    client, conn = _client_and_conn_info()
    token = client.authenticate(conn)
    assert token, "authenticate() ha restituito token vuoto/None"
    # ArcGIS token tipicamente alfanumerici + lunghi
    assert len(token) > 10


def test_list_services_via_bearer_header_works_against_arcgis_online():
    """End-to-end C12: la GET con Authorization: Bearer verso
    /sharing/rest/content/users/{user} dev'essere accettata da
    ArcGIS Online — la migrazione da ?token= a Bearer non ha rotto
    il flow reale."""
    client, conn = _client_and_conn_info()
    services = client.list_services(conn)
    # Atteso: lista (puo' essere vuota se l'utente non ha Feature Service)
    assert isinstance(services, list)


def test_list_layers_if_any_service_exists():
    """Se c'e' almeno un Feature Service, list_layers deve ritornare
    la lista dei layer via Bearer header."""
    client, conn = _client_and_conn_info()
    services = client.list_services(conn)
    if not services:
        pytest.skip("Nessun Feature Service disponibile per il test")

    first_service = services[0]
    service_url = first_service.get("url") or first_service.get("serviceUrl")
    if not service_url:
        pytest.skip(f"Primo service senza url utilizzabile: {first_service}")

    layers = client.list_layers(conn, service_url)
    assert isinstance(layers, list)


def test_query_features_with_tiny_limit_via_bearer():
    """Query features con limite piccolo — verifica che il path query
    con paginazione + Bearer non rompa. Se non ci sono layer leggibili,
    skip."""
    client, conn = _client_and_conn_info()
    services = client.list_services(conn)
    if not services:
        pytest.skip("Nessun Feature Service disponibile")

    service_url = None
    layer_url = None
    for svc in services[:5]:  # prova max 5 servizi
        url = svc.get("url") or svc.get("serviceUrl")
        if not url:
            continue
        try:
            layers = client.list_layers(conn, url)
            if layers:
                service_url = url
                layer_url = f"{url.rstrip('/')}/{layers[0]['id']}"
                break
        except Exception:
            continue

    if not layer_url:
        pytest.skip("Nessun layer query-able trovato nei Feature Service")

    rows, cols = client.query_features(
        conn, layer_url,
        where="1=1",
        max_features=5,
        return_geometry=False,
    )
    assert isinstance(rows, list)
    assert isinstance(cols, list)
    # Non assertiamo len(rows) > 0: un layer vuoto e' legittimo
