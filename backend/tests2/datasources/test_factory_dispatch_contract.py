"""Contract test per `app.core.datasources.factory` — dispatch tipo → backend.

Invariants blindati (da ``backend/docs/architecture/invariants.md``):

- §4.1 layer business: fail-fast su dato critico. ``create_backend`` è il
  singolo entry point del modulo datasources; accettare silentemente un
  ds_type sconosciuto o cadere su un default arbitrario violerebbe la
  policy del modulo.
- §1.1 "un solo entry point, una sola policy di transazione, un solo
  modello di errore": il factory è l'unico punto di accoppiamento tra
  ``DataSourceService`` e le 4 implementazioni. Un secondo dispatch
  parallelo violerebbe il principio canonico.

Regressioni protette:

- Nuovo backend aggiunto a ``_BACKENDS`` senza aggiornare i test: il
  parametrize su ``supported_types()`` scopre subito se una classe non
  è più registrata.
- Fail-fast su ds_type sconosciuto: un merge futuro che sostituisse il
  ``raise ValueError`` con un fallback (es. ``LocalDataSource`` default)
  romperebbe il contratto — Silent compromise sul tipo storage.
- Campi non-string di configurazione (porta numerica, booleani, None):
  un refactor che normalizzasse indifferentemente in ``extra`` o
  rimuovesse il filtro ``None`` romperebbe i backend che si aspettano
  valori puliti.
"""

from __future__ import annotations

import pytest

from app.core.datasources import factory
from app.core.datasources.interface import DataSourceInterface
from app.core.datasources.local import LocalDataSource
from app.core.datasources.ftp import FTPDataSource
from app.core.datasources.sftp import SFTPDataSource
from app.core.datasources.sharepoint import SharePointDataSource


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


@pytest.mark.parametrize("ds_type,expected_cls", [
    ("local",      LocalDataSource),
    ("ftp",        FTPDataSource),
    ("sftp",       SFTPDataSource),
    ("sharepoint", SharePointDataSource),
])
def test_create_backend_dispatches_to_correct_class(ds_type, expected_cls):
    """Blinda §1.1 entry-point singolo: ogni ds_type canonico produce
    l'istanza della classe attesa.

    La parametrizzazione è esaustiva sui 4 tipi supportati. Un merge
    futuro che registri un nuovo backend DEVE aggiornarla qui.
    """
    config = {
        "id": "ds-test",
        "type": ds_type,
        "root_path": "/tmp/fake",
    }

    result = factory.create_backend(config)

    assert isinstance(result, expected_cls), (
        f"Per ds_type={ds_type!r} atteso {expected_cls.__name__}, "
        f"ottenuto {type(result).__name__}"
    )
    assert isinstance(result, DataSourceInterface), (
        "ogni backend DEVE implementare DataSourceInterface"
    )


def test_create_backend_raises_value_error_on_unknown_type():
    """Blinda §4.1 fail-fast: ds_type sconosciuto → ValueError esplicito,
    con lista tipi validi nel messaggio (diagnostica).

    Regressione: in passato un fallback silenzioso sul tipo "local"
    avrebbe potuto mascherare misconfigurazioni. Il contratto ora è
    rigido: tipo non noto = stop immediato.
    """
    config = {"id": "x", "type": "s3", "root_path": "/bucket"}

    with pytest.raises(ValueError, match="non supportato") as exc_info:
        factory.create_backend(config)

    # Il messaggio DEVE includere la lista dei tipi validi per diagnostica.
    msg = str(exc_info.value)
    for expected in ("local", "ftp", "sftp", "sharepoint"):
        assert expected in msg, (
            f"messaggio di errore DEVE listare il tipo {expected!r} per "
            f"facilitare la correzione, ottenuto: {msg!r}"
        )


def test_create_backend_raises_on_missing_type_field():
    """``type`` assente ≡ tipo sconosciuto: stessa policy fail-fast.

    Evita che un dict malformato (es. salvataggio corrotto su FS) passi
    attraverso il factory con default ambiguo.
    """
    config = {"id": "x", "root_path": "/p"}  # NO 'type'

    with pytest.raises(ValueError, match="non supportato"):
        factory.create_backend(config)


def test_create_backend_raises_on_empty_string_type():
    """``type=""`` è un bug di config, non un input valido.

    Il factory usa ``config_dict.get("type", "")`` e quindi empty
    string segue lo stesso path di missing → ValueError.
    """
    config = {"id": "x", "type": "", "root_path": "/p"}

    with pytest.raises(ValueError, match="non supportato"):
        factory.create_backend(config)


def test_create_backend_routes_custom_fields_into_extra():
    """Blinda il contratto di trasporto config: campi NON-structured
    (credenziali, opzioni specifiche del backend) devono atterrare in
    ``config.extra`` così come sono, senza rename.

    Questo è il canale che i backend concreti (ftp, sftp, sharepoint)
    usano per leggere username/password/host/port/etc. — cifrati dal
    service, decifrati dal backend al connect.
    """
    config_dict = {
        "id": "ds-ftp-01",
        "type": "ftp",
        "root_path": "/export",
        "name": "ftp prod",              # structured, NON va in extra
        "description": "primary",         # structured, NON va in extra
        "created_at": "2026-01-01",       # structured, NON va in extra
        "updated_at": "2026-01-02",       # structured, NON va in extra
        # Tutti i seguenti NON sono nel set structured → extra
        "host": "ftp.example.com",
        "port": 21,
        "username": "urby",
        "password": "enc:xxx",
        "use_tls": True,
    }

    backend = factory.create_backend(config_dict)

    assert backend.config.id == "ds-ftp-01"
    assert backend.config.type == "ftp"
    assert backend.config.root_path == "/export"

    extra = backend.config.extra
    for key in ("host", "port", "username", "password", "use_tls"):
        assert key in extra, f"campo custom {key!r} DEVE finire in extra"
    for key in ("id", "type", "root_path", "name", "description",
                "created_at", "updated_at"):
        assert key not in extra, (
            f"campo structured {key!r} NON DEVE duplicarsi in extra"
        )


def test_create_backend_filters_none_values_from_extra():
    """Contratto di cleanup: valori ``None`` nel dict di config NON
    finiscono in ``extra``. Giustificato da
    ``factory.py:56`` ``extra={k: v ... if v is not None}``.

    Regressione: se il filtro sparisce, i backend che fanno dict.get
    con default diverso da None potrebbero ricevere None dove non se
    lo aspettano (es. port=None → TypeError in connect).
    """
    config_dict = {
        "id": "ds-ftp-02",
        "type": "ftp",
        "root_path": "/",
        "host": "ftp.example.com",
        "optional_proxy": None,     # DEVE essere filtrato
        "optional_ca_cert": None,   # DEVE essere filtrato
        "explicit_false": False,    # NON None → DEVE restare (policy filtra solo None)
        "empty_string": "",         # NON None → DEVE restare (policy filtra solo None)
    }

    backend = factory.create_backend(config_dict)

    assert "optional_proxy" not in backend.config.extra
    assert "optional_ca_cert" not in backend.config.extra
    assert backend.config.extra.get("explicit_false") is False, (
        "False NON deve essere filtrato, solo None"
    )
    assert backend.config.extra.get("empty_string") == "", (
        "Empty string NON deve essere filtrato, solo None"
    )


def test_supported_types_returns_canonical_four():
    """Blinda la superficie pubblica: ``supported_types()`` ritorna
    esattamente i 4 tipi canonici — né più, né meno.

    Questo è il contract che l'UI (DataSource config modal) e altri
    consumer interrogano per popolare dropdown. Aggiungere/rimuovere
    un tipo senza aggiornare il contract rompe l'UX silent.
    """
    types = factory.supported_types()

    assert isinstance(types, list), "ritorno deve essere list (non tuple, non set)"
    assert set(types) == {"local", "ftp", "sftp", "sharepoint"}, (
        f"tipi supportati attesi: local, ftp, sftp, sharepoint. Ottenuto: {types}"
    )


def test_supported_types_matches_create_backend_dispatch():
    """Meta-contract: ogni tipo in ``supported_types()`` DEVE essere
    dispatch-able da ``create_backend`` senza sollevare ValueError.

    Se questa property viene violata, significa che la documentazione
    pubblica e il dispatch sono divergenti — bug di coerenza interna.
    """
    for ds_type in factory.supported_types():
        config = {"id": "x", "type": ds_type, "root_path": "/"}
        # Non deve sollevare
        backend = factory.create_backend(config)
        assert isinstance(backend, DataSourceInterface)
