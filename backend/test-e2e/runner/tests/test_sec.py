"""SEC-001..SEC-XXX — Security smokes.

Catalogo: backend/test-e2e/catalog/sec.yaml
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import httpx
import pytest


API_PREFIX = "/api"


# ── SEC-001 — AntiSSRF: URL privato bloccato ──────────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
@pytest.mark.parametrize(
    "url, label",
    [
        ("http://127.0.0.1/anything",        "loopback IPv4"),
        ("http://[::1]/anything",            "loopback IPv6"),
        ("http://169.254.169.254/anything",  "link-local (cloud metadata)"),
        ("http://0.0.0.0/anything",          "unspecified"),
    ],
    ids=["loopback_v4", "loopback_v6", "link_local", "unspecified"],
)
def test_sec_001_ssrf_blocks_private_url(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    url: str,
    label: str,
) -> None:
    """SEC-001 — Anti-SSRF: il test runtime di un'api_connection con
    URL verso un IP privato/loopback/link-local DEVE essere rifiutato.

    Reference: `app/core/security/outbound_url.py:check_outbound_address`.

    Approccio:
      1. POST /api-connections con `url=<url privato>`. Il check al
         create-time NON c'e' (vedi `api_connection_management.
         create_api_connection`); il guard scatta solo al runtime
         per non rompere la creazione di connessioni offline.
      2. POST /api-connections/{id}/test → deve ritornare
         `success=false` con error contenente "URL bloccato" /
         "non valido" / "non risolvibile" (label dipende dal tipo
         di blocco — link-local => "IP privato/interno",
         loopback => idem, 0.0.0.0 => "IP privato/interno" via
         is_unspecified).

    NB: in environment dev con `PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND
    =1` (necessario per i container e2e dello stack) gli IP privati
    standard (10.x, 172.16-31.x, 192.168.x) sono ammessi. Il smoke
    si concentra sui blocchi che restano attivi anche in dev:
    loopback, link-local, unspecified.
    """
    # 1. Crea api_connection. Il SSRF check non gira al create —
    # creiamo una connection "fake" con auth `none`.
    payload = {
        "name": f"e2e-sec001-{uuid.uuid4().hex[:6]}",
        "description": f"SEC-001 {label}",
        "connection_type": "rest",
        "api_type": "enricher",
        "url": url,
        "method": "GET",
        "auth": {"type": "none"},
    }
    r_create = http_client.post(
        f"{API_PREFIX}/api-connections",
        json=payload, headers=auth_headers,
    )
    assert r_create.status_code in (200, 201), (
        f"SEC-001[{label}] create api-connection: {r_create.status_code} "
        f"— {r_create.text[:200]}"
    )
    cid = r_create.json().get("id")
    assert cid, f"create senza id: {r_create.json()}"

    try:
        # 2. Test connessione: il guard SSRF deve bloccare la
        # richiesta outbound prima che parta.
        r_test = http_client.post(
            f"{API_PREFIX}/api-connections/{cid}/test",
            json={}, headers=auth_headers,
        )
        # L'endpoint test ritorna 200 con body `{success: false,
        # error: ...}` per gli errori "gestiti" (tra cui SSRF) per
        # evitare 5xx su input utente. Accettiamo sia 200+success
        # =false che status 4xx.
        if r_test.status_code == 200:
            body = r_test.json()
            assert body.get("success") is False, (
                f"SEC-001[{label}] atteso success=false, "
                f"ottenuto {body}"
            )
            err_msg = (
                str(body.get("error") or "")
                + " " + str(body.get("message") or "")
            ).lower()
        else:
            assert r_test.status_code in (400, 403), (
                f"SEC-001[{label}] status code inatteso "
                f"{r_test.status_code}: {r_test.text[:200]}"
            )
            err_msg = r_test.text.lower()

        # I messaggi prodotti da `check_outbound_address`:
        #   - "URL bloccato: l'host risolve a un IP privato/interno (...)"
        #   - "URL non valido: ..."
        # Verifichiamo presenza di un keyword tra quelli ragionevoli.
        keywords = ("bloccato", "blocked", "ssrf", "privato",
                    "loopback", "interno", "non valido", "invalid")
        assert any(k in err_msg for k in keywords), (
            f"SEC-001[{label}] messaggio errore non riconosciuto: "
            f"{err_msg!r}"
        )
    finally:
        http_client.delete(
            f"{API_PREFIX}/api-connections/{cid}", headers=auth_headers,
        )


# ── SSRF-001/002 — Anti-SSRF sul path payload-based ────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
@pytest.mark.parametrize(
    "url, label",
    [
        # SSRF-001: cloud metadata (AWS / GCP / Alibaba expose 169.254.169.254)
        ("http://169.254.169.254/latest/meta-data/", "aws_metadata"),
        # SSRF-002: localhost varianti
        ("http://localhost:6379/INFO",   "localhost_redis"),
        ("http://127.0.0.1:5432/",       "localhost_postgres"),
        ("http://[::1]:8080/admin",      "localhost_ipv6"),
        ("http://0.0.0.0:80/",           "unspecified"),
    ],
    ids=["aws_metadata", "localhost_redis", "localhost_postgres",
         "localhost_ipv6", "unspecified"],
)
def test_ssrf_001_002_blocks_payload_based_test(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    url: str,
    label: str,
) -> None:
    """SSRF-001/002 — `POST /api-connections/test` (config payload,
    senza salvarla) deve essere bloccato dall'anti-SSRF prima di
    inviare la richiesta HTTP.

    Reference: `app/core/arricchisci/router.test_api_config:706`
    chiama `_validate_external_url(url)` che alza `HTTPException
    403` su URL bloccati. Differente dal path by-id (SEC-001) che
    usa `validate_external_url` come early-return; qui il guard
    e' all'ingresso del router, prima di costruire la config.

    Catalog richiede `outbound_url_blocked` come error_code; il
    backend ritorna `403` con messaggio "URL bloccato:" quando il
    sostituto `outbound_url_blocked` non e' ancora cablato come
    error envelope dedicato. Verifichiamo entrambi: status 403/400
    + messaggio contenente i keyword di blocco.
    """
    payload = {
        "url": url,
        "method": "GET",
        "auth": {"type": "none"},
    }
    r = http_client.post(
        f"{API_PREFIX}/api-connections/test",
        json=payload, headers=auth_headers,
    )
    # Il guard alza HTTPException 403 (private/blocked) o 400
    # (URL malformato / non risolvibile). Mai success 200.
    assert r.status_code in (400, 403, 422), (
        f"SSRF[{label}] status code inatteso {r.status_code}: "
        f"{r.text[:200]}"
    )
    msg = r.text.lower()
    keywords = ("bloccato", "blocked", "ssrf", "privato", "loopback",
                "interno", "non valido", "invalid", "non risolvibile")
    assert any(k in msg for k in keywords), (
        f"SSRF[{label}] messaggio non riconosciuto: {msg!r}"
    )
