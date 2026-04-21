"""Contract test per `SFTPDataSource` — sicurezza + error mapping.

Il backend SFTP ha due classi di contract critici:

1. **Host key policy** — la configurazione ``host_key_policy`` dell'utente
   DEVE tradursi in un setup paramiko coerente. Il valore ``"reject"``
   è l'UNICA via sicura contro MITM (blocca host ignoti, carica
   known_hosts di sistema). Ogni altro valore cade su ``AutoAddPolicy``
   che accetta silent chiavi nuove — utile per dev, insicuro in prod.

2. **Credenziali fail-closed** — senza ``password`` E senza
   ``private_key`` il connect DEVE fallire esplicitamente. Niente
   tentativi di connect "anonimo" o default.

Invariants blindati:

- §1.6 SFTP fail-closed: host key policy canonica (``reject`` vs altro)
  mappa deterministicamente, niente ambiguità silent.
- §1.6 credential fail-fast: config senza credenziali → stop, NON
  retry con "" silent.
- Error mapping paramiko → eccezioni tipate: ``AuthenticationException``,
  ``SSHException``, ``OSError`` tradotti in ``DataSourceConnectionError``
  con messaggi distintivi per diagnostica admin.

Regressioni protette:

- Inversione policy setup: se qualcuno sposta ``load_system_host_keys``
  nel branch ``auto_add`` (o lo rimuove dal ``reject``), la policy
  reject bloccherebbe ANCHE gli host legittimi in known_hosts.
- Degradazione del check ``== "reject"`` a ``startswith("rej")`` o
  case-insensitive: sembrerebbe benigno ma introduce ambiguità sul
  valore canonico esatto che l'admin si aspetta.
- Credential silent fallback: rimuovere il raise "Credenziali mancanti"
  e lasciare che paramiko fallisca internamente maschererebbe la
  misconfigurazione dietro un ``AuthenticationException`` ambiguo.
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


# ─── Fake paramiko minimal per mocking deterministico ─────────────────────

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


class _FakeParamikoModule:
    """Modulo finto sostituito via monkeypatch di ``_import_paramiko``."""
    SSHClient = FakeSSHClient
    RejectPolicy = RejectPolicy
    AutoAddPolicy = AutoAddPolicy
    AuthenticationException = _AuthenticationException
    SSHException = _SSHException
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


# ─── 1) reject policy → RejectPolicy + load_system_host_keys ──────────────

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


# ─── 2) auto_add (default esplicito) → AutoAddPolicy + NO system keys ─────

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


# ─── 3) Policy non-canonica → fallback auto_add (documenta fail-open) ─────

def test_host_key_policy_unknown_value_falls_back_to_auto_add_policy():
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
        ds.connect()

        client = FakeSSHClient.instances[0]
        assert client.policy_set_to == "AutoAddPolicy", (
            f"policy={bad_value!r} DEVE cadere su AutoAddPolicy "
            f"(check esatto '== reject'). Ottenuto: {client.policy_set_to!r}"
        )
        assert client.system_keys_loaded is False


# ─── 4) Credenziali mancanti → fail-fast ConnectionError ──────────────────

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


# ─── 5) Password + private_key → vince private_key ────────────────────────

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


# ─── 6) AuthenticationException → ConnectionError "Autenticazione fallita" ─

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


# ─── 7) SSHException → ConnectionError "Errore SSH" ───────────────────────

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
        self.connect_raises = _SSHException("Banner timeout")

    try:
        FakeSSHClient.__init__ = init_with_raise
        with pytest.raises(
            DataSourceConnectionError, match="Errore SSH",
        ) as exc:
            ds.connect()
        assert isinstance(exc.value.__cause__, _SSHException)
    finally:
        FakeSSHClient.__init__ = original_init


# ─── 8) Operation senza connect → _check gate ─────────────────────────────

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
