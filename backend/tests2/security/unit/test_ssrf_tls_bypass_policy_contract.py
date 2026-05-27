"""
Contract test parametrizzato per il cluster SSRF/TLS-bypass.

Copre 4 bug originariamente reportati da plenora-bug-connections e
plenora-bug-etl-sources, accomunati dalla policy "credenziali e payload
sensibili non devono viaggiare su canali compromessi":

  1. plenora-bug-etl-sources P0
     `enrichment_service.py` non chiamava `validate_external_url` nel path
     enricher per-riga. URL configurata dall'utente poteva puntare a
     `http://169.254.169.254/` (AWS/Azure metadata) o a IP privati.
     Post-fix: `EnrichmentExecutor.__init__` solleva ValueError per URL
     che `validate_external_url` rifiuta.

  2. plenora-bug-etl-sources P0
     `enrichment_service.py:17` chiamava `urllib3.disable_warnings(
     InsecureRequestWarning)` a livello di import, sopprimendo i warning
     SSL per l'intero processo Python (non solo per il modulo).
     Post-fix: la riga e' rimossa.

  3. plenora-bug-connections P0
     `oauth.py:549` accettava `verify_ssl=False` da config utente per il
     POST del token ArcGIS in qualsiasi ambiente — credenziali su canale
     MITM-able in produzione.
     Post-fix: `_resolve_tls_verify` forza verify=True in prod/staging
     con WARNING. In dev resta opt-in con WARNING.

  4. plenora-bug-connections P1
     `sftp.py:88` defaultava ad AutoAddPolicy quando `host_key_policy` era
     omesso — qualsiasi server MitM veniva accettato silenziosamente.
     Post-fix: default env-aware (reject in prod/staging, auto_add in dev),
     piu' WARNING esplicito quando AutoAddPolicy e' attiva.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

# NOTA: EnrichmentExecutor importa pandas/pyarrow indirettamente. L'import
# e' deferito all'interno dei test che ne hanno bisogno cosi' la collection
# resta veloce e i test SFTP/OAuth non scaricano il graph pandas.
from app.core.datasources.interface import BackendConfig
from app.core.datasources.sftp import SFTPDataSource
from app.shared.security import tls_policy
import app.core.datasources.sftp as sftp_module
import app.shared.security.tls_policy as tls_policy_module


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.security,
    pytest.mark.regression,
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Pulisce env vars di policy security per garantire determinismo."""
    for name in (
        "ENVIRONMENT",
        "ENV",
        "PLENORA_ALLOW_LOOPBACK_OUTBOUND",
        "PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND",
        "PLENORA_ALLOW_TLS_BYPASS",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Bug #1 — SSRF gate nel path enricher per-riga
# ---------------------------------------------------------------------------

class TestEnrichmentExecutorSsrfGate:
    """`EnrichmentExecutor.__init__` deve respingere URL SSRF-rischiose."""

    @pytest.mark.parametrize("hostile_url", [
        "http://127.0.0.1/api",                  # loopback IPv4
        "http://localhost/api",                  # loopback hostname
        "http://169.254.169.254/latest/meta",    # link-local (metadata cloud)
        "ftp://example.test/data",               # schema non http(s)
        "http:///path-without-host",             # hostname mancante
    ])
    def test_rejects_ssrf_or_invalid_url(self, hostile_url, monkeypatch):
        from app.core.arricchisci.enrichment_service import EnrichmentExecutor
        # Forza prod-like per chiudere il loopback opt-in
        monkeypatch.setenv("ENVIRONMENT", "production")
        with pytest.raises(ValueError):
            EnrichmentExecutor(
                api_config={"endpoint": {"url": hostile_url, "method": "GET"}},
                param_mapping={},
            )

    def test_accepts_public_url(self):
        from app.core.arricchisci.enrichment_service import EnrichmentExecutor
        # `api.example.com` e' un dominio di documentazione (IANA reserved)
        # che non risolve come privato. Verifica che il gate NON blocchi
        # URL legittime.
        executor = EnrichmentExecutor(
            api_config={"endpoint": {"url": "https://api.example.com/v1", "method": "GET"}},
            param_mapping={},
        )
        assert executor.url == "https://api.example.com/v1"

    def test_tolerates_empty_url_at_init(self):
        from app.core.arricchisci.enrichment_service import EnrichmentExecutor
        # URL vuota e' un caso degenere gestito da validazioni a valle.
        # Il gate __init__ NON deve sollevare su stringa vuota — altrimenti
        # rompe i path che costruiscono l'executor prima di assegnare l'url.
        executor = EnrichmentExecutor(
            api_config={"endpoint": {"url": "", "method": "GET"}},
            param_mapping={},
        )
        assert executor.url == ""


# ---------------------------------------------------------------------------
# Bug #2 — no `urllib3.disable_warnings` globale all'import di enrichment_service
# ---------------------------------------------------------------------------

class TestEnrichmentServiceNoGlobalDisableWarnings:
    """Il modulo enrichment_service non deve sopprimere globalmente i
    warning SSL: regressione su `verify=False` introdotta da terzi
    risulterebbe altrimenti invisibile al security monitoring.
    """

    def test_module_does_not_call_disable_warnings_at_import(self):
        # AST check: nessun `Call` con target `urllib3.disable_warnings`
        # a livello modulo (le menzioni in commenti/docstring sono OK e
        # documentano la rimozione storica).
        import ast

        src_path = (
            Path(__file__).resolve().parents[3]
            / "app" / "core" / "arricchisci" / "enrichment_service.py"
        )
        tree = ast.parse(src_path.read_text(encoding="utf-8"))

        bad_calls: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # `urllib3.disable_warnings(...)` o `disable_warnings(...)` plain
            if isinstance(func, ast.Attribute) and func.attr == "disable_warnings":
                bad_calls.append(ast.unparse(node))
            elif isinstance(func, ast.Name) and func.id == "disable_warnings":
                bad_calls.append(ast.unparse(node))

        assert not bad_calls, (
            "enrichment_service.py non deve chiamare disable_warnings: "
            f"trovate {len(bad_calls)} occorrenze: {bad_calls!r}"
        )


# ---------------------------------------------------------------------------
# Bug #3 — OAuth verify_ssl=False non bypassabile da config in prod/staging
# ---------------------------------------------------------------------------

class _WarningSpy:
    """Spy diretto sul metodo `warning(...)` dello StructuredLogger Plenora.

    Plenora wrappa stdlib in `StructuredLogger` con `propagate=False`:
    caplog pytest non li intercetta nemmeno con `set_level(logger=...)`
    perche' i record non raggiungono il root logger. Spy sul wrapper
    e' il path piu' affidabile per assertion sui WARNING emessi.
    """

    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def all_messages(self) -> list[str]:
        msgs = []
        for args, _ in self.calls:
            if not args:
                continue
            fmt = args[0]
            rest = args[1:]
            try:
                msgs.append(fmt % rest if rest else str(fmt))
            except (TypeError, ValueError):
                msgs.append(f"{fmt} {rest}")
        return msgs


class TestTlsBypassPolicy:
    """`tls_policy.resolve_tls_verify` deve essere fail-safe by default:
    bypass concesso solo con opt-in esplicito o env=dev/test.
    """

    @pytest.mark.parametrize("prod_env", ["production", "prod", "staging", "stage"])
    def test_prod_or_staging_forces_verify_true(self, prod_env, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", prod_env)
        spy = _WarningSpy()
        monkeypatch.setattr(tls_policy_module.logger, "warning", spy)
        resolved = tls_policy.resolve_tls_verify(
            False, context="ArcGIS token"
        )
        assert resolved is True
        assert any(
            "verify_ssl=False ignorato" in msg for msg in spy.all_messages()
        ), "Deve loggare WARNING quando override TLS viene bloccata in prod"

    def test_prod_ignores_opt_in_env_var(self, monkeypatch):
        """Anche con `PLENORA_ALLOW_TLS_BYPASS=1`, prod-like envs rifiutano
        il bypass: signal di defense-in-depth contro misconfig accidentali.
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("PLENORA_ALLOW_TLS_BYPASS", "1")
        spy = _WarningSpy()
        monkeypatch.setattr(tls_policy_module.logger, "warning", spy)
        resolved = tls_policy.resolve_tls_verify(False, context="API enricher")
        assert resolved is True

    @pytest.mark.parametrize("dev_env", ["development", "dev", "test"])
    def test_dev_envs_allow_verify_false_with_warning(self, dev_env, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", dev_env)
        spy = _WarningSpy()
        monkeypatch.setattr(tls_policy_module.logger, "warning", spy)
        resolved = tls_policy.resolve_tls_verify(False, context="ArcGIS token")
        assert resolved is False
        assert any(
            "verify_ssl=False attivo" in msg for msg in spy.all_messages()
        ), "Anche in dev deve loggare WARNING su verify_ssl=False"

    def test_explicit_opt_in_env_allows_bypass(self, monkeypatch):
        """Con ENVIRONMENT non settato MA `PLENORA_ALLOW_TLS_BYPASS=1`,
        il bypass passa. Usato in CI per fixture con certificati self-signed.
        """
        # ENVIRONMENT delle' deja' deletata dal fixture _clean_env
        monkeypatch.setenv("PLENORA_ALLOW_TLS_BYPASS", "1")
        spy = _WarningSpy()
        monkeypatch.setattr(tls_policy_module.logger, "warning", spy)
        resolved = tls_policy.resolve_tls_verify(False, context="ArcGIS token")
        assert resolved is False
        assert any(
            "bypass abilitato via env" in msg for msg in spy.all_messages()
        )

    def test_fail_safe_when_env_unset_and_no_opt_in(self, monkeypatch):
        """Quando NESSUN signal e' presente (env vuoto, no opt-in), la
        policy fallisce safe: verify=True forzato. Cattura il caso in cui
        un deploy dimentica di impostare ENVIRONMENT.
        """
        # Nessun env settato (cleanup di _clean_env)
        spy = _WarningSpy()
        monkeypatch.setattr(tls_policy_module.logger, "warning", spy)
        resolved = tls_policy.resolve_tls_verify(False, context="ArcGIS token")
        assert resolved is True, (
            "Fail-safe: senza ENVIRONMENT e senza opt-in, verify deve "
            "essere True (mai aprire MITM per default)"
        )

    def test_verify_true_passes_through_silently(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        spy = _WarningSpy()
        monkeypatch.setattr(tls_policy_module.logger, "warning", spy)
        resolved = tls_policy.resolve_tls_verify(
            True, context="ArcGIS token"
        )
        assert resolved is True
        # Nessun warning di security su flow normale.
        assert not any(
            "verify_ssl" in msg for msg in spy.all_messages()
        )


# ---------------------------------------------------------------------------
# Bug #4 — SFTP host_key_policy default env-aware
# ---------------------------------------------------------------------------

class _FakeSFTPSession:
    def chdir(self, path):
        pass

    def close(self):
        pass


class _FakeSSHClient:
    instances: list["_FakeSSHClient"] = []

    def __init__(self):
        self.policy_set_to: str | None = None
        self.system_keys_loaded: bool = False
        _FakeSSHClient.instances.append(self)

    def load_system_host_keys(self):
        self.system_keys_loaded = True

    def set_missing_host_key_policy(self, policy_instance):
        self.policy_set_to = type(policy_instance).__name__

    def connect(self, **kwargs):
        pass

    def open_sftp(self):
        return _FakeSFTPSession()

    def close(self):
        pass


class _RejectPolicy:
    pass


class _AutoAddPolicy:
    pass


class _FakeParamiko:
    SSHClient = _FakeSSHClient
    RejectPolicy = _RejectPolicy
    AutoAddPolicy = _AutoAddPolicy

    class AuthenticationException(Exception):
        pass

    class SSHException(Exception):
        pass

    class BadHostKeyException(SSHException):
        pass

    RSAKey = type("RSAKey", (), {})
    Ed25519Key = type("Ed25519Key", (), {})
    ECDSAKey = type("ECDSAKey", (), {})
    DSSKey = type("DSSKey", (), {})


@pytest.fixture
def _install_fake_paramiko(monkeypatch):
    _FakeSSHClient.instances.clear()
    monkeypatch.setattr(sftp_module, "_import_paramiko", lambda: _FakeParamiko)
    yield
    _FakeSSHClient.instances.clear()


def _make_sftp(extra_overrides=None):
    extra = {
        "host": "sftp.example.com",
        "port": 22,
        "username": "user",
        "password": "pw",
        "private_key": "",
    }
    if extra_overrides:
        extra.update(extra_overrides)
    return SFTPDataSource(BackendConfig(
        id="ds-sftp", type="sftp", root_path="", extra=extra,
    ))


class TestSftpHostKeyPolicyDefault:
    """Quando `host_key_policy` e' omesso, il default deve essere
    env-aware (reject in prod/staging, auto_add altrove). Quando e'
    settato esplicitamente, deve avere precedenza (compat test esistenti).
    """

    @pytest.mark.parametrize("prod_env", ["production", "prod", "staging", "stage"])
    def test_default_reject_in_prod_or_staging(
        self, prod_env, monkeypatch, _install_fake_paramiko,
    ):
        monkeypatch.setenv("ENVIRONMENT", prod_env)
        # host_key_policy omesso intenzionalmente
        ds = _make_sftp()
        ds.connect()
        client = _FakeSSHClient.instances[0]
        assert client.policy_set_to == "_RejectPolicy", (
            f"Default in env={prod_env!r} deve essere RejectPolicy, "
            f"ottenuto {client.policy_set_to!r}"
        )
        assert client.system_keys_loaded is True

    @pytest.mark.parametrize("dev_env", ["development", "dev", "test"])
    def test_default_auto_add_in_dev(
        self, dev_env, monkeypatch, _install_fake_paramiko,
    ):
        monkeypatch.setenv("ENVIRONMENT", dev_env)
        ds = _make_sftp()
        ds.connect()
        client = _FakeSSHClient.instances[0]
        assert client.policy_set_to == "_AutoAddPolicy"
        assert client.system_keys_loaded is False

    def test_explicit_auto_add_in_prod_logs_warning(
        self, monkeypatch, _install_fake_paramiko,
    ):
        monkeypatch.setenv("ENVIRONMENT", "production")
        # Spy diretto sul logger (StructuredLogger propagate=False —
        # vedi commento su _WarningSpy nella sezione OAuth).
        spy = _WarningSpy()
        monkeypatch.setattr(sftp_module.logger, "warning", spy)
        ds = _make_sftp({"host_key_policy": "auto_add"})
        ds.connect()
        client = _FakeSSHClient.instances[0]
        # Override esplicito ha precedenza sul default env-aware.
        assert client.policy_set_to == "_AutoAddPolicy"
        # Ma deve emettere un WARNING di security.
        assert any(
            "AutoAddPolicy" in msg and "MitM" in msg
            for msg in spy.all_messages()
        ), "AutoAddPolicy deve loggare WARNING su rischio MitM"
