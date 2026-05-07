"""Contract test — `app.shared.request_utils.get_client_ip`.

Blinda la semantica anti-spoofing del resolve client IP dietro reverse
proxy. Regressione bloccata: il codice pre-fix usava sempre
`request.client.host` — in setup nginx→uvicorn locale tutti i client
diventavano `127.0.0.1`, collassando il rate-limit login su un bucket
condiviso (DoS amplification: un attacker bloccava il login di tutti).

Cases (6):
  1. peer trusted + XFF singolo     → primo IP XFF
  2. peer trusted + XFF chain       → primo IP (client originale)
  3. peer trusted + XFF assente     → fallback peer
  4. peer NON trusted + XFF valido  → peer (ignora XFF spoofabile)
  5. peer None                      → sentinel "unknown"
  6. peer trusted + XFF vuoto/ws    → fallback peer
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def _fake_request(peer_host: str | None, xff: str | None = None):
    """Costruisce un oggetto mock che espone solo gli attributi che
    get_client_ip legge: `.client.host` (o None) e `.headers.get()`.
    Evita dipendenza da FastAPI Request reale (richiede ASGI scope).
    """
    headers = {"X-Forwarded-For": xff} if xff is not None else {}
    client = SimpleNamespace(host=peer_host) if peer_host is not None else None
    return SimpleNamespace(
        client=client,
        headers=SimpleNamespace(get=lambda k, default="": headers.get(k, default)),
    )


@pytest.fixture(autouse=True)
def _reset_trusted_proxies(monkeypatch):
    """Allowlist default: 127.0.0.1 + ::1. Assicura baseline stabile
    anche se un test precedente ha settato TRUSTED_PROXIES."""
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)


# Peer trusted


def test_trusted_peer_with_single_xff_returns_xff():
    from app.shared.request_utils import get_client_ip

    req = _fake_request(peer_host="127.0.0.1", xff="1.2.3.4")
    assert get_client_ip(req) == "1.2.3.4"


def test_trusted_peer_with_xff_chain_returns_first():
    from app.shared.request_utils import get_client_ip

    # Convenzione XFF: "client, proxy1, proxy2, ..." — il primo e' il client
    # originale (quello piu' lontano dal nostro server).
    req = _fake_request(
        peer_host="127.0.0.1",
        xff="1.2.3.4, 10.0.0.1, 10.0.0.2",
    )
    assert get_client_ip(req) == "1.2.3.4"


def test_trusted_peer_without_xff_falls_back_to_peer():
    from app.shared.request_utils import get_client_ip

    req = _fake_request(peer_host="127.0.0.1", xff=None)
    assert get_client_ip(req) == "127.0.0.1"


def test_trusted_peer_with_empty_xff_falls_back_to_peer():
    from app.shared.request_utils import get_client_ip

    req = _fake_request(peer_host="127.0.0.1", xff="")
    assert get_client_ip(req) == "127.0.0.1"


def test_trusted_peer_with_whitespace_only_xff_falls_back_to_peer():
    from app.shared.request_utils import get_client_ip

    req = _fake_request(peer_host="127.0.0.1", xff="   ")
    assert get_client_ip(req) == "127.0.0.1"


def test_ipv6_localhost_is_trusted_by_default():
    from app.shared.request_utils import get_client_ip

    req = _fake_request(peer_host="::1", xff="1.2.3.4")
    assert get_client_ip(req) == "1.2.3.4"


# Peer NON trusted (anti-spoofing)


def test_untrusted_peer_with_xff_ignores_xff():
    """Un peer non-trusted (connessione diretta da internet) puo'
    mandare X-Forwarded-For qualsiasi → lo ignoriamo, ci fidiamo solo
    del TCP peer reale."""
    from app.shared.request_utils import get_client_ip

    req = _fake_request(peer_host="5.6.7.8", xff="1.2.3.4")
    assert get_client_ip(req) == "5.6.7.8"


def test_untrusted_peer_with_xff_chain_ignores_xff():
    from app.shared.request_utils import get_client_ip

    req = _fake_request(
        peer_host="5.6.7.8",
        xff="1.2.3.4, 10.0.0.1",
    )
    assert get_client_ip(req) == "5.6.7.8"


# Edge case: peer None


def test_none_peer_returns_unknown():
    from app.shared.request_utils import get_client_ip

    req = _fake_request(peer_host=None)
    assert get_client_ip(req) == "unknown"


# Override TRUSTED_PROXIES via env var


def test_env_override_adds_custom_trusted_proxy(monkeypatch):
    """Setup container/K8s: il peer puo' essere l'IP interno del pod
    ingress. Via env TRUSTED_PROXIES possiamo aggiungere senza cambio
    codice."""
    from app.shared.request_utils import get_client_ip

    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,::1,10.0.0.5")
    req = _fake_request(peer_host="10.0.0.5", xff="1.2.3.4")
    assert get_client_ip(req) == "1.2.3.4"


def test_env_override_can_drop_default_trusted_list(monkeypatch):
    """Override totale: se setti TRUSTED_PROXIES con una lista che NON
    include 127.0.0.1, quel peer diventa non-trusted."""
    from app.shared.request_utils import get_client_ip

    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
    # 127.0.0.1 ora non e' piu' nella lista → XFF ignorato
    req = _fake_request(peer_host="127.0.0.1", xff="1.2.3.4")
    assert get_client_ip(req) == "127.0.0.1"


# CIDR support (range di IP come trusted proxies)


def test_cidr_range_matches_peer_in_range(monkeypatch):
    """TRUSTED_PROXIES con CIDR: qualsiasi IP nel range e' trusted."""
    from app.shared.request_utils import get_client_ip

    # Range K8s tipico: 10.0.0.0/8
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")

    for peer in ("10.0.0.5", "10.255.255.1", "10.1.2.3"):
        req = _fake_request(peer_host=peer, xff="1.2.3.4")
        assert get_client_ip(req) == "1.2.3.4", f"peer {peer} dovrebbe essere trusted"


def test_cidr_range_does_not_match_peer_outside(monkeypatch):
    """Peer fuori dal CIDR → non-trusted, XFF ignorato."""
    from app.shared.request_utils import get_client_ip

    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/24")  # /24 = 10.0.0.0-10.0.0.255

    # 10.0.1.1 e' fuori da 10.0.0.0/24
    req = _fake_request(peer_host="10.0.1.1", xff="1.2.3.4")
    assert get_client_ip(req) == "10.0.1.1"


def test_mixed_exact_and_cidr(monkeypatch):
    """Lista mista: exact IP + CIDR funzionano insieme."""
    from app.shared.request_utils import get_client_ip

    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,10.0.0.0/8,::1")

    # exact
    req1 = _fake_request(peer_host="127.0.0.1", xff="1.2.3.4")
    assert get_client_ip(req1) == "1.2.3.4"
    # CIDR match
    req2 = _fake_request(peer_host="10.42.0.99", xff="5.6.7.8")
    assert get_client_ip(req2) == "5.6.7.8"
    # exact IPv6
    req3 = _fake_request(peer_host="::1", xff="9.10.11.12")
    assert get_client_ip(req3) == "9.10.11.12"
    # neither
    req4 = _fake_request(peer_host="192.168.1.1", xff="1.2.3.4")
    assert get_client_ip(req4) == "192.168.1.1"


def test_malformed_cidr_entry_ignored_silently(monkeypatch):
    """CIDR malformato nella env var non fa crashare il server, viene
    scartato silenziosamente (il resto della lista resta valido)."""
    from app.shared.request_utils import get_client_ip

    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,not-a-cidr/99,10.0.0.0/8")

    # 127.0.0.1 ancora trusted
    req1 = _fake_request(peer_host="127.0.0.1", xff="1.2.3.4")
    assert get_client_ip(req1) == "1.2.3.4"
    # 10.0.0.5 ancora trusted (CIDR valida nella lista)
    req2 = _fake_request(peer_host="10.0.0.5", xff="5.6.7.8")
    assert get_client_ip(req2) == "5.6.7.8"


# IP validation su XFF (anti-injection)


def test_invalid_xff_falls_back_to_peer():
    """XFF con stringa non-IP → fallback al peer (non si fidiamo di
    valori arbitrari che diventerebbero chiave rate-limit)."""
    from app.shared.request_utils import get_client_ip

    # peer trusted ma XFF non e' un IP valido
    for bad_xff in ("foo", "notanip", "123.456.789.012", "1.2.3", "abc.def.ghi.jkl"):
        req = _fake_request(peer_host="127.0.0.1", xff=bad_xff)
        assert get_client_ip(req) == "127.0.0.1", f"XFF={bad_xff} dovrebbe essere rifiutato"


def test_xff_normalizes_ipv6_canonical_form():
    """IP IPv6 in forma non compressa → ritorna la forma canonica."""
    from app.shared.request_utils import get_client_ip

    # '2001:0db8:0000:0000:0000:0000:0000:0001' = '2001:db8::1' canonico
    req = _fake_request(
        peer_host="127.0.0.1",
        xff="2001:0db8:0000:0000:0000:0000:0000:0001",
    )
    assert get_client_ip(req) == "2001:db8::1"


def test_ipv4_mapped_ipv6_peer_is_trusted_via_ipv4():
    """Peer `::ffff:127.0.0.1` (IPv4-mapped IPv6, dual-stack Linux) deve
    matchare la allowlist default che contiene `127.0.0.1`. Senza il
    ipv4_mapped fallback in _is_trusted_peer, il default non proteggerebbe
    setup dual-stack."""
    from app.shared.request_utils import get_client_ip

    req = _fake_request(peer_host="::ffff:127.0.0.1", xff="1.2.3.4")
    # peer IPv4-mapped trusted via lookup su 127.0.0.1 → XFF viene letto
    assert get_client_ip(req) == "1.2.3.4"


def test_ipv4_mapped_ipv6_in_cidr_range(monkeypatch):
    """IPv4-mapped IPv6 va anche verificato contro CIDR IPv4."""
    from app.shared.request_utils import get_client_ip

    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    # Peer ::ffff:10.0.0.5 (IPv4 10.0.0.5 dentro 10.0.0.0/8)
    req = _fake_request(peer_host="::ffff:10.0.0.5", xff="1.2.3.4")
    assert get_client_ip(req) == "1.2.3.4"


def test_trusted_proxies_cached_hit_on_same_raw(monkeypatch):
    """Il parse della allowlist è cached via lru_cache su raw env.
    Chiamate consecutive con stesso env non riparseano."""
    from app.shared import request_utils

    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,::1,10.0.0.0/8")
    # Clear cache per forzare miss iniziale
    request_utils._parse_trusted_proxies_cached.cache_clear()

    # Due chiamate consecutive
    request_utils._parse_trusted_proxies()
    request_utils._parse_trusted_proxies()

    info = request_utils._parse_trusted_proxies_cached.cache_info()
    assert info.hits >= 1, f"Expected cache hit, got {info}"


def test_trusted_proxies_reparse_on_env_change(monkeypatch):
    """Se l'env cambia (test context), il raw cambia → cache miss → re-parse.
    Non servono clear espliciti del cache."""
    from app.shared.request_utils import get_client_ip

    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1")
    req_a = _fake_request(peer_host="10.0.0.1", xff="1.2.3.4")
    assert get_client_ip(req_a) == "1.2.3.4"

    # Cambia env: ora 10.0.0.1 NON è più trusted
    monkeypatch.setenv("TRUSTED_PROXIES", "192.168.1.1")
    req_b = _fake_request(peer_host="10.0.0.1", xff="5.6.7.8")
    assert get_client_ip(req_b) == "10.0.0.1"  # peer non-trusted ora, XFF ignorato


def test_xff_with_crlf_injection_attempt_is_rejected():
    """Header injection con CRLF → non un IP valido → fallback peer.
    (I parser HTTP li bloccano a monte, ma double check e' safety net.)"""
    from app.shared.request_utils import get_client_ip

    req = _fake_request(
        peer_host="127.0.0.1",
        xff="1.2.3.4\r\nX-Evil-Header: pwned",
    )
    # "1.2.3.4\r\nX-Evil-..." non e' un IP valido dopo split per ","
    # (resta l'intera stringa con CRLF incluso)
    assert get_client_ip(req) == "127.0.0.1"
