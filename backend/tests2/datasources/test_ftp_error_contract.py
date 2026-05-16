"""
Contract test per `FTPDataSource` — error mapping.

Il backend FTP espone un'API pubblica uniforme (6 operazioni + lifecycle)
"""

from __future__ import annotations

import ftplib

import pytest

from app.core.datasources.ftp import FTPDataSource
from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


class _FakeFTP:
    """Stand-in di ``ftplib.FTP`` per test di error mapping.

    Ogni istanza espone i metodi usati da ``FTPDataSource.connect()`` e
    dalle operazioni file. Il comportamento è controllato via attributi
    di classe/istanza impostati dal test.
    """

    # Class-level toggles — i test li settano prima dell'uso
    connect_raises: Exception | None = None
    login_raises: Exception | None = None
    cwd_raises: Exception | None = None

    def __init__(self):
        self.connected = False
        self.pasv_mode: bool | None = None
        self.cwd_called_with: str | None = None

    def connect(self, host, port, timeout):
        if type(self).connect_raises:
            raise type(self).connect_raises
        self.connected = True

    def login(self, user, passwd):
        _ = passwd
        if type(self).login_raises:
            raise type(self).login_raises

    def set_pasv(self, b):
        self.pasv_mode = b

    def cwd(self, path):
        if type(self).cwd_raises:
            raise type(self).cwd_raises
        self.cwd_called_with = path

    def quit(self):
        pass

    def close(self):
        pass

    def nlst(self, path="."):
        return []


def _clean_fake_ftp():
    """Reset delle toggle statiche del fake prima di ogni test."""
    _FakeFTP.connect_raises = None
    _FakeFTP.login_raises = None
    _FakeFTP.cwd_raises = None


@pytest.fixture(autouse=True)
def _reset_fake_ftp_state():
    _clean_fake_ftp()
    yield
    _clean_fake_ftp()


def _make_ds(host: str = "ftp.example.com", root_path: str = "/",
             tls: bool = False) -> FTPDataSource:
    config = BackendConfig(
        id="ds-ftp",
        type="ftp",
        root_path=root_path,
        extra={
            "host": host,
            "port": 21,
            "username": "user",
            "password": "pw",
            "tls": tls,
            "passive": True,
        },
    )
    return FTPDataSource(config)


def test_connect_without_host_raises_connection_error_no_network_call(monkeypatch):
    """Blinda fail-fast: ``host=""`` deve produrre ``DataSourceConnectionError``
    PRIMA di istanziare ``ftplib.FTP`` e tentare socket.

    Regressione: se il check ``if not host`` viene spostato dopo
    ``ftplib.FTP()``, ogni test di config senza host pagherebbe il
    costo di un socket timeout (default system level). Lento + log
    rumorosi.
    """
    ftp_calls = []

    class _CountingFTP(_FakeFTP):
        def __init__(self):
            ftp_calls.append("instantiated")
            super().__init__()

    monkeypatch.setattr(ftplib, "FTP", _CountingFTP)

    ds = _make_ds(host="")

    with pytest.raises(
        DataSourceConnectionError, match="Host FTP non configurato",
    ):
        ds.connect()

    assert ftp_calls == [], (
        f"ftplib.FTP NON deve essere istanziata se host è vuoto. "
        f"Istanziazioni osservate: {ftp_calls}"
    )


def test_connect_wraps_generic_ftplib_error_as_connection_error_chained(monkeypatch):
    """Blinda il wrap `except ftplib.all_errors: raise DataSourceConnectionError
    from e`. ``OSError`` è uno degli elementi di ``ftplib.all_errors`` e
    copre timeout / connection refused / DNS fail.

    Regressione: rimuovere il wrap farebbe propagare ``OSError`` al
    caller, rompendo l'API tipata.
    """
    _FakeFTP.connect_raises = OSError("timeout network")
    monkeypatch.setattr(ftplib, "FTP", _FakeFTP)

    ds = _make_ds()

    # F5-B2 (2026-04-24): il messaggio utente-facing e' sanitizzato dal
    # mapping `_sanitize_ftp_error`. OSError generico -> "Connessione FTP
    # rifiutata". Il match e' stato rilassato a "Connessione FTP" per
    # coprire tutte le varianti canoniche ("fallita"/"rifiutata"/
    # "Timeout"/"Host non risolvibile"). Il dettaglio tecnico
    # "timeout network" resta nel `__cause__`, non nel messaggio utente.
    with pytest.raises(
        DataSourceConnectionError, match="Connessione FTP|Timeout|Host FTP",
    ) as exc:
        ds.connect()

    # from e → __cause__ preservato per diagnostica
    assert isinstance(exc.value.__cause__, OSError), (
        f"chain `from e` deve preservare la OSError originale, "
        f"ottenuto __cause__={exc.value.__cause__!r}"
    )
    assert "timeout network" in str(exc.value.__cause__)
    # F5-B2: il dettaglio SDK NON deve comparire nel messaggio utente
    assert "timeout network" not in str(exc.value), (
        f"leak regression: str(e) SDK in messaggio utente-facing: {exc.value!r}"
    )


def test_connect_wraps_auth_failure_as_connection_error(monkeypatch):
    """Blinda auth failure: ``ftplib.error_perm`` su login (es. ``530 Login
    incorrect``) cade nel branch ``except ftplib.all_errors`` e diventa
    ``DataSourceConnectionError`` generico.

    Motivo del contract: credenziali sbagliate NON sono un errore
    "permission" del dominio datasource (che indica ACL su file); sono
    un errore di connessione. Il caller che riceve PermissionError
    penserebbe di ritentare con stessi cred. Il caller che riceve
    ConnectionError sa che la connect globale è rotta.
    """
    _FakeFTP.login_raises = ftplib.error_perm("530 Login incorrect SECRET_DETAIL")
    monkeypatch.setattr(ftplib, "FTP", _FakeFTP)

    ds = _make_ds()

    # F5-B2: ftplib.error_perm -> "Accesso FTP negato o risorsa non disponibile"
    # (non include il response code 530 ne' dettagli login). Il match copre
    # entrambe le varianti canoniche di auth-fail.
    with pytest.raises(
        DataSourceConnectionError, match="Accesso FTP negato|Connessione FTP",
    ) as exc:
        ds.connect()

    assert isinstance(exc.value.__cause__, ftplib.error_perm)
    # F5-B2: anti-leak — token sensibile nello str(e) SDK NON deve comparire
    assert "SECRET_DETAIL" not in str(exc.value)
    assert "530" not in str(exc.value)


def test_connect_with_inaccessible_root_raises_specific_message(monkeypatch):
    """Blinda il branch dedicato per CWD fail: la root path configurata
    NON accessibile ritorna ``DataSourceConnectionError`` con messaggio
    specifico ``"Root path FTP non accessibile"``.

    Discriminante rispetto al generic "Connessione FTP fallita":
    l'admin che legge il log distingue subito se il problema è rete/auth
    (connect/login) o config (root path sbagliata).

    Regressione: se l'inner try attorno a cwd() viene rimosso, il
    raise specifico viene assorbito dall'outer except e il messaggio
    collassa a generic.
    """
    _FakeFTP.cwd_raises = ftplib.error_perm("550 CWD failed")
    monkeypatch.setattr(ftplib, "FTP", _FakeFTP)

    ds = _make_ds(root_path="/missing/dir")

    with pytest.raises(
        DataSourceConnectionError, match="Root path FTP non accessibile",
    ) as exc:
        ds.connect()

    assert "/missing/dir" in str(exc.value), (
        f"il messaggio DEVE includere la root path per diagnostica, "
        f"ottenuto: {exc.value!r}"
    )


def test_operation_without_connect_raises_connection_error_not_connected():
    """Blinda il contract di ``_check``: chiamare un'operazione file senza
    aver chiamato ``connect()`` (o senza context manager) produce
    ``DataSourceConnectionError`` con messaggio esplicito.

    Regressione: se ``_check`` venisse rimosso, ``self._ftp.size(path)``
    su ``self._ftp is None`` solleverebbe ``AttributeError`` non-tipato
    che leak al caller. Il caller del service si aspetta sempre errori
    tipati dal dominio datasource.
    """
    ds = _make_ds()
    # NON chiamiamo ds.connect() — simuliamo il bug "forgot to open"

    for op_name, op in [
        ("file_exists", lambda: ds.file_exists("file.csv")),
        ("file_info",   lambda: ds.file_info("file.csv")),
        ("read_file",   lambda: ds.read_file("file.csv")),
    ]:
        with pytest.raises(
            DataSourceConnectionError, match="Non connesso",
        ):
            op()


def test_read_file_maps_550_error_perm_to_file_not_found(monkeypatch):
    """Blinda il discriminante ``"550"``: FTP code 550 è la canonica
    risposta di server per "file not found" / "no such file". Il backend
    DEVE tradurla in ``DataSourceFileNotFoundError``, non in
    ``PermissionError``.

    Regressione: collassare 550 su PermissionError genericamente
    rompere l'UI che distingue "file missing" (retry inutile) da
    "access denied" (prob ACL, retry con cred diverse).
    """
    class _FakeFTPWith550(_FakeFTP):
        def retrbinary(self, cmd, callback):
            raise ftplib.error_perm("550 File not found")

    monkeypatch.setattr(ftplib, "FTP", _FakeFTPWith550)

    ds = _make_ds()
    ds.connect()

    with pytest.raises(
        DataSourceFileNotFoundError, match="File non trovato",
    ):
        ds.read_file("missing.csv")


def test_read_file_maps_non_550_error_perm_to_permission_error(monkeypatch):
    """Blinda il complementare: ``error_perm`` SENZA "550" (es. ``530``,
    ``553``) → ``DataSourcePermissionError``.

    Insieme al test #6 questa coppia blinda l'intero split decisionale
    dell'error mapping. Se qualcuno invertisse le branche o usasse
    ``startswith("5")``, uno dei due test rompe.
    """
    class _FakeFTPWith530(_FakeFTP):
        def retrbinary(self, cmd, callback):
            raise ftplib.error_perm("530 Access denied, not logged in")

    monkeypatch.setattr(ftplib, "FTP", _FakeFTPWith530)

    ds = _make_ds()
    ds.connect()

    with pytest.raises(
        DataSourcePermissionError, match="Accesso negato",
    ):
        ds.read_file("forbidden.csv")


def test_file_exists_swallows_ftplib_errors_and_returns_false(monkeypatch):
    """Blinda contract semantico di ``file_exists``: è un predicate, non
    un gate di sicurezza. Su QUALUNQUE ftplib error (connection reset,
    550, 530, timeout) ritorna ``False``, NO exception.

    Regressione: se qualcuno "uniformasse" il comportamento
    trasformando l'error in FileNotFound, il predicate diventerebbe
    exception-raising e ogni ``if ds.file_exists(p):`` crasherebbe
    invece di comportarsi come atteso.
    """
    class _FakeFTPSizeFails(_FakeFTP):
        def size(self, path):
            raise ftplib.error_perm("550 Not available")

    monkeypatch.setattr(ftplib, "FTP", _FakeFTPSizeFails)

    ds = _make_ds()
    ds.connect()

    assert ds.file_exists("any.csv") is False, (
        "file_exists su error ftplib DEVE ritornare False silent, no raise"
    )


# F5-B2 Anti-leak (2026-04-24) — sanitize str(e) SDK in messaggi utente


def test_test_connection_sanitizes_ftplib_error_no_sdk_leak(monkeypatch):
    """F5-B2: `test_connection()` non deve esporre banner/response code FTP
    nel campo `message`. Il mapping `_sanitize_ftp_error` produce un
    messaggio canonico; il dettaglio tecnico resta in logger."""
    _FakeFTP.connect_raises = ftplib.error_perm(
        "530 LEAK_BANNER secret_internal.example.com"
    )
    monkeypatch.setattr(ftplib, "FTP", _FakeFTP)

    ds = _make_ds()
    result = ds.test_connection()

    assert result["ok"] is False
    assert "LEAK_BANNER" not in result["message"], (
        f"regression leak: banner SDK ftplib nel message: {result['message']!r}"
    )
    assert "secret_internal.example.com" not in result["message"]
    assert "530" not in result["message"]
    # Il messaggio deve essere uno dei canonici
    assert any(canonical in result["message"] for canonical in (
        "Accesso FTP negato", "Connessione FTP", "Timeout", "Host FTP",
    )), f"message non canonico: {result['message']!r}"


def test_read_file_error_sanitizes_ftplib_detail_no_sdk_leak(monkeypatch):
    """F5-B2: errori generici di `read_file` via ftplib.all_errors (non 550)
    wrappano in DataSourceConnectionError con messaggio canonico, senza
    str(e) raw."""
    class _FakeFTPRetrFails(_FakeFTP):
        def retrbinary(self, cmd, callback):
            raise OSError("pipe broken LEAK_INTERNAL_DETAIL")

    monkeypatch.setattr(ftplib, "FTP", _FakeFTPRetrFails)

    ds = _make_ds()
    ds.connect()

    with pytest.raises(DataSourceConnectionError) as exc:
        ds.read_file("file.csv")

    msg = str(exc.value)
    assert "LEAK_INTERNAL_DETAIL" not in msg, (
        f"leak regression: str(OSError) SDK in messaggio: {msg!r}"
    )
    assert "pipe broken" not in msg
    # __cause__ preserva il dettaglio tecnico per diagnostica
    assert isinstance(exc.value.__cause__, OSError)


def test_ftp_log_preserves_technical_detail_on_sanitize(monkeypatch):
    """F5-B2: mentre il messaggio utente e' sanitizzato, logger.warning deve
    preservare type(e).__name__ + str(e) full per troubleshooting.

    Usa monkeypatch su logger.warning per osservazione deterministica
    (caplog e' inconsistente tra run singolo/gruppo a causa di interazioni
    con altri conftest del progetto)."""
    from app.core.datasources import ftp as ftp_module

    captured_warnings = []
    original_warning = ftp_module.logger.warning

    def _capture(msg, *args, **kwargs):
        captured_warnings.append(str(msg))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(ftp_module.logger, "warning", _capture)

    _FakeFTP.connect_raises = ftplib.error_perm(
        "530 TECHNICAL_DETAIL_FOR_LOGS only"
    )
    monkeypatch.setattr(ftplib, "FTP", _FakeFTP)

    ds = _make_ds()

    with pytest.raises(DataSourceConnectionError):
        ds.connect()

    # Il dettaglio tecnico DEVE essere nei log server-side
    all_log_text = " ".join(captured_warnings)
    assert "TECHNICAL_DETAIL_FOR_LOGS" in all_log_text, (
        f"dettaglio SDK deve essere nei log (contract observability): "
        f"records = {captured_warnings}"
    )
    assert "error_perm" in all_log_text  # type(e).__name__
