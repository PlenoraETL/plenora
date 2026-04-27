"""Lock tests per `app.shared.api_utils.validate_external_url`.

Garantisce il contratto SSRF anti-loopback + l'opt-in di test
`PLENORA_ALLOW_LOOPBACK_OUTBOUND=1` (canonical) / `URBY_ALLOW_LOOPBACK_OUTBOUND=1`
(alias retro-compat, deprecato — rinominato 2026-04-23 / P1.5). Senza
questi test, una regression silente nella validazione SSRF abiliterebbe
richieste backend verso servizi bind-loopback (Redis, Postgres, EC2
metadata, ecc.).

Blinda anche S6 / P2.4 (chiuso 2026-04-23): il flag e' IGNORATO by
design in ambienti hardened (`ENVIRONMENT in {production, prod,
staging, stage}`), simmetrico a `crypto.py` guard e
`config.py _DOTENV_DISABLED_ENVS`.
"""

import pytest

from app.shared import api_utils
from app.shared.api_utils import validate_external_url


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


@pytest.fixture(autouse=True)
def _clean_loopback_env(monkeypatch):
    """Pulisce env loopback + ENVIRONMENT prima di ogni test: deterministico.

    Resetta anche la cache del warning one-shot A3
    (`_warn_urby_loopback_alias_once`) per evitare flake tra test che
    triggerano l'alias deprecato URBY_ALLOW_LOOPBACK_OUTBOUND in ordine
    diverso.
    """
    for name in (
        "PLENORA_ALLOW_LOOPBACK_OUTBOUND",
        "URBY_ALLOW_LOOPBACK_OUTBOUND",
        "ENVIRONMENT",
        "ENV",
    ):
        monkeypatch.delenv(name, raising=False)
    api_utils._warn_urby_loopback_alias_once.cache_clear()


# =============================================================================
# Schema validation
# =============================================================================


@pytest.mark.parametrize("url", [
    "ftp://example.test/file",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "ldap://dir.example/cn=foo",
])
def test_rejects_non_http_schemes(url):
    with pytest.raises(ValueError, match="schemi http/https"):
        validate_external_url(url)


def test_rejects_missing_hostname():
    with pytest.raises(ValueError, match="hostname mancante"):
        validate_external_url("http:///")


# =============================================================================
# SSRF: IP privati, link-local, riservati, multicast
# =============================================================================


@pytest.mark.parametrize("url", [
    "http://10.0.0.1/q",       # 10/8 private
    "http://172.16.0.1/q",     # 172.16/12 private
    "http://192.168.1.1/q",    # 192.168/16 private
    "http://169.254.169.254/", # link-local (EC2 metadata)
    "http://224.0.0.1/q",      # multicast
])
def test_rejects_private_and_link_local_addresses(url):
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url(url)


def test_rejects_loopback_by_default():
    """Senza `PLENORA_ALLOW_LOOPBACK_OUTBOUND=1` il loopback e' bloccato
    per defense-in-depth (Redis, Postgres locali bind 127.0.0.1)."""
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://127.0.0.1:6379/")


# =============================================================================
# Test opt-in: PLENORA_ALLOW_LOOPBACK_OUTBOUND (canonical)
# =============================================================================


def test_loopback_allowed_when_opt_in_env_is_set(monkeypatch):
    """Con `PLENORA_ALLOW_LOOPBACK_OUTBOUND=1` il loopback passa il check.
    Pensata per i test funzionali che usano un fake server localhost."""
    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
    # Non solleva
    validate_external_url("http://127.0.0.1:51360/json/ok")


def test_opt_in_does_not_unlock_other_private_ranges(monkeypatch):
    """L'opt-in apre SOLO loopback. 10.x / 192.168.x / 172.16.x restano
    bloccati anche con la env var settata: il blast radius dell'opt-in
    e' minimo."""
    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://10.0.0.1/q")
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://192.168.1.1/q")
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://169.254.169.254/")


def test_opt_in_strict_value_check(monkeypatch):
    """Solo `1` esatto attiva l'opt-in. Valori come 'true', 'yes',
    'on' NON funzionano — evita di abilitare il bypass per errore
    via env var generica."""
    for bad in ("0", "true", "yes", "on", "True", " 1 "):
        monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", bad)
        if bad.strip() == "1":
            # Caso edge: " 1 " strip-ato e' "1", quindi passa
            continue
        with pytest.raises(ValueError, match="URL bloccato"):
            validate_external_url("http://127.0.0.1/")


# =============================================================================
# Retro-compat alias + guard hardened env (P1.5 + S6/P2.4, 2026-04-23)
# =============================================================================


def test_legacy_urby_alias_still_honored_in_dev(monkeypatch):
    """Blinda P1.5 (rinomina 2026-04-23): l'alias legacy
    `URBY_ALLOW_LOOPBACK_OUTBOUND` continua a funzionare in dev/test
    per retro-compat. Deploy o test-harness non ancora migrati
    devono continuare a operare.
    """
    monkeypatch.setenv("URBY_ALLOW_LOOPBACK_OUTBOUND", "1")
    # Non solleva — il fallback legacy attiva l'opt-in
    validate_external_url("http://127.0.0.1:51360/ok")


@pytest.mark.parametrize("hardened_env", ["production", "prod", "staging", "stage"])
@pytest.mark.parametrize("flag_name", [
    "PLENORA_ALLOW_LOOPBACK_OUTBOUND",
    "URBY_ALLOW_LOOPBACK_OUTBOUND",
])
def test_flag_ignored_in_hardened_environments(hardened_env, flag_name, monkeypatch):
    """Blinda S6 / P2.4 (chiuso 2026-04-23): in ambienti hardened
    (`ENVIRONMENT in {production, prod, staging, stage}`) il flag e'
    IGNORATO indipendentemente dal valore.

    Scenario reale: un worker prod con `PLENORA_ALLOW_LOOPBACK_OUTBOUND=1`
    settato per errore nella shell NON deve aprire SSRF verso bind-loopback
    (Redis/Postgres/EC2 metadata). Simmetrico a `crypto.py` guard.
    """
    monkeypatch.setenv("ENVIRONMENT", hardened_env)
    monkeypatch.setenv(flag_name, "1")

    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://127.0.0.1:6379/")


# =============================================================================
# B3-B.8 bugfix (2026-04-27): IPv6 (AAAA) coverage + is_unspecified
# =============================================================================
#
# Pre-B3-B.8 `validate_external_url` usava `socket.gethostbyname_ex` che
# ritorna solo record A (IPv4). Un hostname che risolveva esclusivamente
# a IPv6 privato/loopback/link-local (`fc00::/7`, `::1`, `fe80::/10`)
# passava silenziosamente il check — security gap SSRF su servizi
# IPv6-only o dual-stack con bind interno IPv6.
#
# Post-B3-B.8: `socket.getaddrinfo(host, None)` copre A + AAAA. Test
# mockano `getaddrinfo` per simulare scenari IPv6 deterministicamente
# (no DNS reale).


def _mk_addrinfo_result(addrs):
    """Costruisce il return value di `socket.getaddrinfo` con i campi
    minimi richiesti: `info[4][0]` deve contenere l'IP literal.
    """
    import socket as _sock
    return [
        (_sock.AF_INET6 if ":" in addr else _sock.AF_INET,
         _sock.SOCK_STREAM, 0, "", (addr, 0))
        for addr in addrs
    ]


@pytest.mark.parametrize("ipv6_addr", [
    "::1",                # IPv6 loopback
    "fc00::1",            # IPv6 ULA (Unique Local Address) — equivalente IPv6 di 10.x
    "fd12:3456::1",       # IPv6 ULA
    "fe80::1",            # IPv6 link-local
    "ff02::1",            # IPv6 multicast
])
def test_rejects_ipv6_private_loopback_linklocal_multicast(ipv6_addr, monkeypatch):
    """B3-B.8: hostname che risolve a IPv6 privato/loopback/link-local/
    multicast deve essere bloccato. Pre-B3-B.8 (gethostbyname_ex) il
    check su questi indirizzi era silenziosamente skippato perche'
    non venivano mai esposti al loop di validazione."""
    import socket as _sock
    monkeypatch.setattr(
        _sock, "getaddrinfo",
        lambda host, port=None: _mk_addrinfo_result([ipv6_addr]),
    )
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://internal.example.test/q")


def test_rejects_ipv6_when_dual_stack_resolves_aaaa_only(monkeypatch):
    """B3-B.8: hostname dual-stack che risolve a IPv6 privato (fc00::)
    deve essere bloccato anche se IPv4 non c'e'. Pre-B3-B.8
    `gethostbyname_ex` non vedeva il record AAAA → fail-OPEN."""
    import socket as _sock
    monkeypatch.setattr(
        _sock, "getaddrinfo",
        lambda host, port=None: _mk_addrinfo_result(["fc00::dead:beef"]),
    )
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://intranet.example.test/secret")


def test_rejects_ipv6_in_url_literal():
    """URL con literal IPv6 in brackets `http://[::1]/` viene parsato
    da urlparse → hostname='::1' (senza brackets) → getaddrinfo lo
    risolve direttamente come literal → blocked."""
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://[::1]/")


def test_rejects_ipv6_ula_literal():
    """URL con literal IPv6 ULA (fc00::/7) deve essere bloccato senza
    dipendere da DNS — il literal e' parsato direttamente da
    ipaddress."""
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://[fc00::1]/intranet")


@pytest.mark.parametrize("unspecified_url", [
    "http://0.0.0.0/q",
    "http://[::]/q",
])
def test_rejects_unspecified_address(unspecified_url):
    """B3-B.8: `0.0.0.0` (IPv4) e `::` (IPv6) sono `is_unspecified`.
    Edge case minor (cliente bind-all che si auto-referenzia) ma
    teoricamente exploitabile in setup malconfigurati. Pre-B3-B.8
    NON era bloccato esplicitamente; passava silenziosamente."""
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url(unspecified_url)


def test_dedup_addrinfo_results(monkeypatch):
    """B3-B.8: getaddrinfo puo' ritornare lo stesso IP piu' volte
    (es. SOCK_STREAM + SOCK_DGRAM + SOCK_RAW per la stessa famiglia).
    La dedup via `set` evita check ripetuti — comportamento
    invariato per il caller, regressione test che documenta la dedup."""
    import socket as _sock
    # Stesso IP duplicato 3 volte: dedup → 1 sola validazione
    monkeypatch.setattr(
        _sock, "getaddrinfo",
        lambda host, port=None: _mk_addrinfo_result(["10.0.0.1", "10.0.0.1", "10.0.0.1"]),
    )
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://corp-internal.example.test/")


def test_loopback_opt_in_still_works_for_ipv6_loopback(monkeypatch):
    """B3-B.8: l'opt-in `PLENORA_ALLOW_LOOPBACK_OUTBOUND=1` permette
    sia IPv4 loopback (`127.0.0.0/8`) sia IPv6 loopback (`::1`).
    Test funzionali con fake server in dual-stack vedono entrambi.
    Le altre categorie IPv6 (private/link-local/multicast) restano
    bloccate — coerente con il blast radius minimo dell'opt-in."""
    import socket as _sock
    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
    # IPv6 loopback con opt-in: passa
    monkeypatch.setattr(
        _sock, "getaddrinfo",
        lambda host, port=None: _mk_addrinfo_result(["::1"]),
    )
    validate_external_url("http://localhost-test.example/q")  # non solleva

    # IPv6 ULA con opt-in: blocca (l'opt-in non sblocca private)
    monkeypatch.setattr(
        _sock, "getaddrinfo",
        lambda host, port=None: _mk_addrinfo_result(["fc00::1"]),
    )
    with pytest.raises(ValueError, match="URL bloccato"):
        validate_external_url("http://internal-test.example/q")


# =============================================================================
# A3: deprecation warning one-shot per URBY_ALLOW_LOOPBACK_OUTBOUND
# =============================================================================
#
# Verifica che usare l'alias retro-compat senza il canonical generi UN warning
# loud, e che chiamate successive nello stesso processo non lo ripetano (cache
# hit via functools.lru_cache). La fixture autouse `_clean_loopback_env` chiama
# `cache_clear()` tra test per garantire osservabilita' deterministica.
#
# Semantica funzionale invariata: l'alias continua a sbloccare il loopback in
# dev/test, identico al canonical. Il warning e' puro segnale di migrazione.


def _spy_warning(monkeypatch):
    """Aggancia una spia su `api_utils.logger.warning` e ritorna la lista
    di chiamate registrate. Pattern behavior-bound: indipendente dal
    sink concreto (StructuredLogger / stdout / caplog handler chain)."""
    calls = []
    original = api_utils.logger.warning

    def _spy(msg, *args, **kwargs):
        calls.append(msg if not args else (msg % args if "%" in str(msg) else msg))
        return original(msg, *args, **kwargs)

    monkeypatch.setattr(api_utils.logger, "warning", _spy)
    return calls


def _deprecation_warnings(calls):
    return [m for m in calls if "[DEPRECATED] URBY_ALLOW_LOOPBACK_OUTBOUND" in str(m)]


def test_urby_alias_emits_deprecation_warning_once(monkeypatch):
    """Quando solo URBY_ALLOW_LOOPBACK_OUTBOUND e' settato, viene emesso un
    warning loud UNA volta. Chiamate successive nello stesso processo NON
    ripetono il warning (cache hit). Behavior funzionale invariato: il
    loopback resta sbloccato in dev/test (alias mantiene la sua semantica)."""
    monkeypatch.setenv("URBY_ALLOW_LOOPBACK_OUTBOUND", "1")
    calls = _spy_warning(monkeypatch)

    # Prima chiamata: warning emesso, loopback consentito (semantica alias)
    validate_external_url("http://127.0.0.1:51360/ok")
    assert len(_deprecation_warnings(calls)) == 1, (
        f"Atteso 1 warning di deprecation, trovati {len(_deprecation_warnings(calls))}"
    )

    # Seconda chiamata: nessun warning aggiuntivo (cache hit)
    validate_external_url("http://127.0.0.1:51361/ok")
    assert len(_deprecation_warnings(calls)) == 1, (
        "Il warning di deprecation NON deve ripetersi: cache hit attesa"
    )


def test_canonical_alone_does_not_emit_deprecation_warning(monkeypatch):
    """Quando solo PLENORA_ALLOW_LOOPBACK_OUTBOUND (canonical) e' settato,
    nessun warning di deprecation: l'operatore sta gia' usando il nome
    corretto."""
    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
    calls = _spy_warning(monkeypatch)

    validate_external_url("http://127.0.0.1:51360/ok")

    assert _deprecation_warnings(calls) == [], (
        "Nessun warning atteso quando si usa il canonical da solo"
    )


def test_both_aliases_set_does_not_emit_deprecation_warning(monkeypatch):
    """Quando ENTRAMBI canonical + alias sono settati, nessun warning:
    l'operatore sta migrando (canonical presente). Spammare un warning in
    questo scenario disturberebbe la migrazione progressiva."""
    monkeypatch.setenv("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "1")
    monkeypatch.setenv("URBY_ALLOW_LOOPBACK_OUTBOUND", "1")
    calls = _spy_warning(monkeypatch)

    validate_external_url("http://127.0.0.1:51360/ok")

    assert _deprecation_warnings(calls) == [], (
        "Nessun warning atteso quando canonical + alias sono entrambi settati"
    )


def test_cache_clear_allows_warning_to_be_re_emitted(monkeypatch):
    """Sanity check sulla fixture: dopo `cache_clear()` il warning torna a
    essere emesso. Garantisce che l'autouse fixture isoli correttamente i
    test successivi che dipendono dall'observability del warning."""
    monkeypatch.setenv("URBY_ALLOW_LOOPBACK_OUTBOUND", "1")
    calls = _spy_warning(monkeypatch)

    validate_external_url("http://127.0.0.1:51360/ok")
    assert len(_deprecation_warnings(calls)) == 1

    # Reset cache + nuova chiamata: warning ri-emesso
    api_utils._warn_urby_loopback_alias_once.cache_clear()
    validate_external_url("http://127.0.0.1:51360/ok")
    assert len(_deprecation_warnings(calls)) == 2, (
        "Dopo cache_clear() il warning deve ri-emettersi una volta"
    )
