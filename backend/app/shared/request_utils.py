"""
Request helpers — cross-cutting utilities per endpoint FastAPI.

Qui convergono le utility che servono a piu' di un router/middleware
"""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


# Allowlist di peer TCP che possono scrivere X-Forwarded-For di cui ci
# fidiamo. Override via env `TRUSTED_PROXIES` con CSV. Supporta
# IP singoli: "127.0.0.1,::1,10.0.0.5"
# CIDR ranges: "10.0.0.0/8,172.16.0.0/12"
# Mix: "127.0.0.1,10.0.0.0/8,::1"
# Default coerente con setup single-host nginx -> uvicorn sullo stesso box
# lab.plenora.it today
_DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1"


@lru_cache(maxsize=4)
def _parse_trusted_proxies_cached(raw: str) -> tuple:
    """Versione cached del parse. Chiave = raw CSV. Se l'env cambia il
    raw cambia → cache miss → re-parse. In pratica la chiave cambia solo
    al boot (env immutabile dopo) quindi cache hit ratio ~100%.

    Size 4 per gestire test monkeypatching che settano env var multiple
    volte (default + override tipici).
    """
    exact: set[str] = set()
    networks: list = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "/" in entry:
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                # CIDR malformato → ignora silenzioso (no crash al boot
                continue
        else:
            try:
                # Normalizza via ipaddress per uniformare IPv6 equivalenti
                # es. "::1" vs "0:0:0:0:0:0:0:1") — il set lookup diventa
                # sulla forma canonica
                exact.add(str(ipaddress.ip_address(entry)))
            except ValueError:
                continue
    return (frozenset(exact), tuple(networks))


def _parse_trusted_proxies() -> tuple[set[str], list]:
    """Entry point: legge env e delega al parse cached.

    Il cache e' lru_cache su `raw`, non su os.getenv — se qualcuno cambia
    l'env a runtime tramite monkeypatch, la chiave del cache cambia
    automaticamente e il parse viene rifatto.
    """
    raw = os.getenv("TRUSTED_PROXIES", _DEFAULT_TRUSTED_PROXIES)
    exact_frozen, networks_tuple = _parse_trusted_proxies_cached(raw)
    return set(exact_frozen), list(networks_tuple)


def _is_trusted_peer(peer: str) -> bool:
    """True se `peer` matcha la allowlist (exact IP o CIDR).

    Gestisce anche IPv4-mapped IPv6 (es. `::ffff:127.0.0.1`): estrae
    l'IPv4 equivalente e confronta anche su quello. Setup dual-stack
    Linux tipici passano peer IPv6-mapped per connessioni IPv4; senza
    questa normalizzazione il default `127.0.0.1` della allowlist non
    matcherebbe.
    """
    exact, networks = _parse_trusted_proxies()
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return False

    # Candidati da testare: IP originale + eventuale IPv4-mapped
    candidates = [peer_ip]
    if isinstance(peer_ip, ipaddress.IPv6Address) and peer_ip.ipv4_mapped:
        candidates.append(peer_ip.ipv4_mapped)

    for ip in candidates:
        if str(ip) in exact:
            return True
        if any(ip in net for net in networks):
            return True
    return False


def _parse_client_ip_from_xff(xff: str) -> Optional[str]:
    """Estrae il primo IP valido da X-Forwarded-For.

    XFF convention: "client, proxy1, proxy2, ...". Il primo e' il
    client originale (quello piu' lontano dal nostro server). Se il
    primo token non e' un IP valido (malformed header, injection
    attempt, ecc.) ritorna None — il caller deve fallback a peer.

    Validazione via `ipaddress.ip_address`: rifiuta qualsiasi cosa
    non sia un indirizzo IPv4 o IPv6 ben formato. Previene:
      - Iniezione di stringhe arbitrarie come "chiave" rate-limit
        (es. '"a", "b"' causerebbe un bucket sporco con spazi)
      - Header injection multi-line (CR/LF) — i parser HTTP li
        bloccano a monte, ma un secondo check qui costa zero.
    """
    if not xff:
        return None
    first = xff.split(",", 1)[0].strip()
    if not first:
        return None
    try:
        # Normalizza via ipaddress → rifiuta tutto quello che non e'
        # un IP. Ritorna la forma canonica (compressa per IPv6
        return str(ipaddress.ip_address(first))
    except ValueError:
        return None


def get_client_ip(request: "Request") -> str:
    """Risolve l'IP client vero dietro un reverse proxy (nginx, CDN, ecc.).

    Pattern anti-spoofing:
      - Se `request.client.host` matcha TRUSTED_PROXIES (exact o CIDR) →
        la richiesta arriva da un proxy di cui ci fidiamo. Legge
        `X-Forwarded-For` e prende il **primo** IP della catena (il
        client originale). Validazione: deve essere un IP ben formato,
        altrimenti fallback a peer.
      - Altrimenti → usa `request.client.host`. La richiesta arriva
        direttamente da un peer non-trusted, X-Forwarded-For va
        ignorato (spoofabile dall'attaccante).
      - Se `request.client` e' None (edge case: client disconnesso,
        test env con mock incompleto) → ritorna "unknown" sentinel.

    Usato da:
      - `auth.py` rate-limit login per-IP (shared bucket DoS se mal
        risolto: tutti gli attacchi diventerebbero 127.0.0.1)
      - audit log / access log (TODO: integrare)
    """
    peer = request.client.host if request.client else None
    if peer is None:
        return "unknown"

    if _is_trusted_peer(peer):
        xff = request.headers.get("X-Forwarded-For", "")
        client_ip = _parse_client_ip_from_xff(xff)
        if client_ip:
            return client_ip

    return peer
