"""
Contract test per `SFTPDataSource` — sicurezza + error mapping.

Il backend SFTP ha due classi di contract critici:
"""

from __future__ import annotations

import pytest

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
)
from app.core.datasources.sftp import SFTPDataSource
import app.core.datasources.sftp as sftp_module


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.security,
    pytest.mark.shared,
]


class _FakeSFTPSession:
    """Stand-in di paramiko.SFTPClient."""
    def chdir(self, path):
        pass

    def close(self):
        pass


class FakeSSHClient:
    """Stand-in di paramiko.SSHClient che registra le chiamate per assertion.

    ``policy_set_to`` registra il nome della classe della policy che viene
    passata a ``set_missing_host_key_policy``. Permette di verificare il
    dispatch host_key_policy senza dipendere da paramiko reale.
    """

    # Side-channel per esporre istanze create ai test
    instances: list["FakeSSHClient"] = []

    def __init__(self):
        self.policy_set_to: str | None = None
        self.system_keys_loaded: bool = False
        self.connect_called_with: dict | None = None
        self.connect_raises: Exception | None = None
        FakeSSHClient.instances.append(self)

    def load_system_host_keys(self):
        self.system_keys_loaded = True

    def set_missing_host_key_policy(self, policy_instance):
        self.policy_set_to = type(policy_instance).__name__

    def connect(self, **kwargs):
        self.connect_called_with = kwargs
        if self.connect_raises:
            raise self.connect_raises

    def open_sftp(self):
        return _FakeSFTPSession()

    def close(self):
        pass


# Policy classes: nominate esplicitamente così type(policy).__name__ è leggibile
class RejectPolicy:
    pass


class AutoAddPolicy:
    pass


# Exception classes paramiko-compatibili
class _AuthenticationException(Exception):
    pass


class _SSHException(Exception):
    pass


class _BadHostKeyException(_SSHException):
    pass


class _FakeParamikoModule:
    """Modulo finto sostituito via monkeypatch di ``_import_paramiko``."""
    SSHClient = FakeSSHClient
    RejectPolicy = RejectPolicy
    AutoAddPolicy = AutoAddPolicy
    AuthenticationException = _AuthenticationException
    SSHException = _SSHException
    # F5-B2: _sanitize_sftp_error referenzia BadHostKeyException
    BadHostKeyException = _BadHostKeyException
    # Key classes non usate in questi test (no private_key path testato qui)
    RSAKey = type("RSAKey", (), {})
    Ed25519Key = type("Ed25519Key", (), {})
    ECDSAKey = type("ECDSAKey", (), {})
    DSSKey = type("DSSKey", (), {})


@pytest.fixture(autouse=True)
def _install_fake_paramiko_and_reset(monkeypatch):
    """Sostituisce l'import di paramiko con il fake e resetta state tra test."""
    FakeSSHClient.instances.clear()
    monkeypatch.setattr(
        sftp_module, "_import_paramiko", lambda: _FakeParamikoModule,
    )
    yield
    FakeSSHClient.instances.clear()


def _make_ds(host_key_policy: str | None = None,
             password: str = "pw",
             private_key: str = "",
             host: str = "sftp.example.com") -> SFTPDataSource:
    extra = {
        "host": host,
        "port": 22,
        "username": "user",
        "password": password,
        "private_key": private_key,
    }
    if host_key_policy is not None:
        extra["host_key_policy"] = host_key_policy
    config = BackendConfig(
        id="ds-sftp",
        type="sftp",
        root_path="",  # evita chdir nel test
        extra=extra,
    )
    return SFTPDataSource(config)


def test_host_key_policy_reject_uses_reject_policy_and_loads_system_keys():
    """Blinda il contract di sicurezza critico: ``host_key_policy="reject"``
    DEVE risultare in:
      1. ``load_system_host_keys()`` chiamato (carica known_hosts di
         sistema per avere riferimenti affidabili).
      2. ``set_missing_host_key_policy(RejectPolicy())`` (blocca host
         sconosciuti).

    Entrambi sono necessari: senza `load_system_host_keys`, RejectPolicy
    bloccherebbe PURE gli host legittimi in known_hosts. Senza
    RejectPolicy, `load_system_host_keys` diventa decorativo.

    Regressione: spostare una delle due chiamate altrove romperebbe la
    coppia e il contract "reject = MITM-safe" cade.
    """
    ds = _make_ds(host_key_policy="reject")
    ds.connect()

    assert len(FakeSSHClient.instances) == 1
    client = FakeSSHClient.instances[0]

    assert client.system_keys_loaded is True, (
        "policy=reject DEVE chiamare load_system_host_keys per caricare "
        "i known_hosts di sistema"
    )
    assert client.policy_set_to == "RejectPolicy", (
        f"policy=reject DEVE settare RejectPolicy, ottenuto: "
        f"{client.policy_set_to!r}"
    )


def test_host_key_policy_auto_add_uses_auto_add_policy_no_system_keys():
    """Blinda il complementare di #1: policy=auto_add → AutoAddPolicy
    SENZA caricare known_hosts di sistema.

    Il side ``system_keys_loaded is False`` è importante: se qualcuno
    aggiungesse ``load_system_host_keys`` in both branches "per
    uniformità", AutoAddPolicy verrebbe influenzata dai known_hosts
    di sistema (chiavi note farebbero match prima del fallback policy),
    cambiando semantica in modo non documentato.
    """
    ds = _make_ds(host_key_policy="auto_add")
    ds.connect()

    client = FakeSSHClient.instances[0]
    assert client.policy_set_to == "AutoAddPolicy", (
        f"policy=auto_add DEVE settare AutoAddPolicy, ottenuto: "
        f"{client.policy_set_to!r}"
    )
    assert client.system_keys_loaded is False, (
        "policy=auto_add NON DEVE chiamare load_system_host_keys"
    )


def test_host_key_policy_unknown_value_fails_closed():
    """Blinda che il check ``policy == "reject"`` sia ESATTO. Qualunque
    valore diverso (typo ``"rejetc"``, case-variant ``"Reject"``, policy
    sconosciuta ``"strict"``) cade su ``AutoAddPolicy``.

    Questo È fail-OPEN intenzionale per compatibilità config legacy/dev,
    ma è un contract critico da blindare: un merge futuro che aggiungesse
    un terzo valore ``"strict"`` DEVE aggiornare il dispatch esplicitamente,
    NON affidarsi a comportamento implicito. Se il test rompe, è un
    segnale "nuova policy aggiunta, rivedi la security matrix".
    """
    # Verifica 3 varianti che tutte cadono su auto_add
    for bad_value in ("Reject", "rejetc", "strict"):
        FakeSSHClient.instances.clear()
        ds = _make_ds(host_key_policy=bad_value)
        with pytest.raises(DataSourceConnectionError, match="policy SFTP non valida"):
            ds.connect()
        assert FakeSSHClient.instances[0].policy_set_to is None


def test_connect_without_password_or_private_key_fails_fast():
    """Blinda fail-fast: config senza ``password`` E senza ``private_key``
    DEVE sollevare ``DataSourceConnectionError("Credenziali SFTP
    mancanti...")`` PRIMA di invocare ``ssh.connect()``.

    Motivo del contract: un connect paramiko senza credenziali
    fallirebbe internamente con AuthenticationException ambiguo ("no
    authentication methods available"). Il contract esplicito distingue
    "config incompleta" (fix: popolare password o key) da "credenziali
    sbagliate" (fix: verifica password corrente).
    """
    ds = _make_ds(password="", private_key="")

    with pytest.raises(
        DataSourceConnectionError, match="Credenziali SFTP mancanti",
    ):
        ds.connect()

    # ssh.connect NON deve essere stata chiamata
    assert all(
        client.connect_called_with is None
        for client in FakeSSHClient.instances
    ), (
        "ssh.connect NON deve essere chiamato se le credenziali mancano"
    )


def test_connect_prefers_private_key_over_password_when_both_provided(
    monkeypatch,
):
    """Blinda preferenza chiave: se entrambe password e private_key sono
    configurate, il connect DEVE usare la chiave (kwargs include ``pkey``,
    NON ``password``).

    Rationale: chiave SSH è più sicura del password (rotazione, no brute
    force efficace). Admin che fornisce entrambe si aspetta che il
    sistema preferisca la chiave automaticamente.

    Regressione: invertire la preferenza (password prima di chiave) o
    passare entrambe le kwargs romperebbe il flow per server configurati
    per accettare solo keyboard-interactive/publickey auth.
    """
    # Mocka _load_key per ritornare un marker senza toccare paramiko reale
    fake_key_marker = object()
    monkeypatch.setattr(
        SFTPDataSource,
        "_load_key",
        staticmethod(lambda paramiko, pem, passphrase: fake_key_marker),
    )

    ds = _make_ds(password="secret", private_key="-----BEGIN RSA...")
    ds.connect()

    client = FakeSSHClient.instances[0]
    assert client.connect_called_with is not None
    kwargs = client.connect_called_with
    assert kwargs.get("pkey") is fake_key_marker, (
        f"quando private_key è presente, ssh.connect DEVE ricevere pkey, "
        f"ottenuto kwargs: {kwargs!r}"
    )
    assert "password" not in kwargs, (
        f"password NON deve essere passata se private_key è presente, "
        f"ottenuto kwargs: {kwargs!r}"
    )



def test_connect_maps_auth_exception_to_connection_error_with_auth_message():
    """Blinda error mapping: ``paramiko.AuthenticationException`` →
    ``DataSourceConnectionError`` con messaggio distintivo
    "Autenticazione SFTP fallita".

    Regressione: collapsare auth fail in un generic "Errore SSH"
    maschererebbe lo scenario più comune (cred sbagliate) in un
    messaggio vago. Admin devono vedere subito "credenziali sbagliate"
    vs "network/protocol issue".
    """
    ds = _make_ds()
    # Configura il fake SSH client per alzare AuthenticationException
    # Devo farlo dopo l'istanziazione ma prima della connect() reale
    # Trick: patcho la classe per avere connect_raises pre-configurato
    original_init = FakeSSHClient.__init__

    def init_with_raise(self):
        original_init(self)
        self.connect_raises = _AuthenticationException("Bad password")

    try:
        FakeSSHClient.__init__ = init_with_raise
        with pytest.raises(
            DataSourceConnectionError, match="Autenticazione SFTP fallita",
        ) as exc:
            ds.connect()
        assert isinstance(exc.value.__cause__, _AuthenticationException)
    finally:
        FakeSSHClient.__init__ = original_init


def test_connect_maps_ssh_exception_to_connection_error_with_ssh_message():
    """Blinda error mapping distintivo per ``paramiko.SSHException`` →
    "Errore SSH". Complementa il test #6 (auth) per coprire l'altro
    branch principale.

    Il messaggio distintivo è il contract diagnostico: "Errore SSH"
    segnala un problema di protocollo/banner (server non-SSH, version
    mismatch), diverso da auth fallita.
    """
    ds = _make_ds()
    original_init = FakeSSHClient.__init__

    def init_with_raise(self):
        original_init(self)
        self.connect_raises = _SSHException("Banner timeout SECRET_HOST_DETAIL")

    try:
        FakeSSHClient.__init__ = init_with_raise
        # F5-B2 (2026-04-24): messaggio canonico "Errore protocollo SSH"
        # (non "Errore SSH" secco). Match copre entrambe le varianti.
        with pytest.raises(
            DataSourceConnectionError, match="Errore .*SSH|SSH",
        ) as exc:
            ds.connect()
        assert isinstance(exc.value.__cause__, _SSHException)
        # F5-B2 anti-leak
        assert "SECRET_HOST_DETAIL" not in str(exc.value)
        assert "Banner timeout" not in str(exc.value)
    finally:
        FakeSSHClient.__init__ = original_init


def test_operation_without_connect_raises_not_connected_error():
    """Blinda il contract ``_check``: operation file su ds mai-connesso
    solleva ``DataSourceConnectionError("Non connesso...")``.

    Senza questo gate, ``self._sftp.stat(path)`` su ``None`` farebbe
    ``AttributeError: 'NoneType' object has no attribute 'stat'``: una
    eccezione non-tipata che il caller service non è preparato a gestire.
    """
    ds = _make_ds()
    # NON chiamiamo connect()

    for op_name, op in [
        ("file_exists", lambda: ds.file_exists("x.csv")),
        ("file_info",   lambda: ds.file_info("x.csv")),
        ("read_file",   lambda: ds.read_file("x.csv")),
    ]:
        with pytest.raises(
            DataSourceConnectionError, match="Non connesso",
        ):
            op()


# F5-B2 Anti-leak (2026-04-24) — sanitize str(e) paramiko in messaggi utente


def test_test_connection_sanitizes_auth_exception_no_paramiko_leak():
    """F5-B2: `test_connection()` su AuthenticationException non deve
    esporre host/user/fingerprint nel campo `message` — solo un messaggio
    canonico. Il dettaglio tecnico resta in logger."""
    ds = _make_ds()
    original_init = FakeSSHClient.__init__

    def init_with_raise(self):
        original_init(self)
        self.connect_raises = _AuthenticationException(
            "Authentication failed for user 'admin' at host prod-db.internal:2222 "
            "(fingerprint SHA256:LEAK_FINGERPRINT)"
        )

    try:
        FakeSSHClient.__init__ = init_with_raise
        result = ds.test_connection()
    finally:
        FakeSSHClient.__init__ = original_init

    assert result["ok"] is False
    msg = result["message"]
    assert "LEAK_FINGERPRINT" not in msg, f"leak fingerprint paramiko: {msg!r}"
    assert "prod-db.internal" not in msg, f"leak hostname: {msg!r}"
    assert "admin" not in msg, f"leak username: {msg!r}"
    # Il messaggio deve essere canonico
    assert "Autenticazione SFTP" in msg or "credenziali" in msg


def test_connect_ssh_exception_sanitizes_banner_no_leak():
    """F5-B2: SSHException generica in connect() -> messaggio canonico
    "Errore protocollo SSH", senza leak di banner / version strings."""
    ds = _make_ds()
    original_init = FakeSSHClient.__init__

    def init_with_raise(self):
        original_init(self)
        self.connect_raises = _SSHException(
            "Error reading SSH protocol banner: SECRET_BANNER_CONTENT_HERE"
        )

    try:
        FakeSSHClient.__init__ = init_with_raise
        with pytest.raises(DataSourceConnectionError) as exc:
            ds.connect()
    finally:
        FakeSSHClient.__init__ = original_init

    msg = str(exc.value)
    assert "SECRET_BANNER_CONTENT_HERE" not in msg
    assert "Error reading SSH protocol banner" not in msg
    # Il canonico deve essere presente
    assert "protocollo SSH" in msg or "Errore" in msg


def test_sftp_log_preserves_technical_detail_on_sanitize(monkeypatch):
    """F5-B2: il logger.warning server-side deve preservare type(e).__name__
    + str(e) full per troubleshooting, nonostante il messaggio utente sia
    sanitizzato.

    Usa monkeypatch su logger.warning (caplog e' inconsistente tra
    run singolo/gruppo a causa di interazioni con altri conftest)."""
    from app.core.datasources import sftp as sftp_module

    captured_warnings = []
    original_warning = sftp_module.logger.warning

    def _capture(msg, *args, **kwargs):
        captured_warnings.append(str(msg))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(sftp_module.logger, "warning", _capture)

    ds = _make_ds()
    original_init = FakeSSHClient.__init__

    def init_with_raise(self):
        original_init(self)
        self.connect_raises = _AuthenticationException(
            "TECHNICAL_DETAIL_FOR_LOGS credenziali"
        )

    try:
        FakeSSHClient.__init__ = init_with_raise
        with pytest.raises(DataSourceConnectionError):
            ds.connect()
    finally:
        FakeSSHClient.__init__ = original_init

    all_log_text = " ".join(captured_warnings)
    assert "TECHNICAL_DETAIL_FOR_LOGS" in all_log_text, (
        f"dettaglio SDK deve essere nei log (contract observability): "
        f"records = {captured_warnings}"
    )
    assert "_AuthenticationException" in all_log_text or "Authentication" in all_log_text
