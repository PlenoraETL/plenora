"""
Contract test per `app.core.datasources.service` — crypto at-rest.

Scope: blindare il contratto **crypto round-trip via service**
"""

from __future__ import annotations


import pytest

from app.core.datasources.models import DataSourceUpdate
from app.core.datasources.service import DataSourceService
from app.infrastructure.security import crypto


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _ensure_database_url_for_module_import(monkeypatch):
    """Placeholder DATABASE_URL per permettere l'import di
    ``app.core.datasources.db`` (via ``pg_connection``, che al top-level
    richiede la env var).

    Il valore è fittizio: il fixture `_mock_db` che segue monkeypatch-a
    tutte le funzioni usate dal service, quindi nessuna connessione
    reale viene mai aperta.

    Anomalia baseline annotata in STREAM_REPORT.md: ``pg_connection.py:113``
    fa fail-fast al top-level. Lazy-init sarebbe più friendly per i test
    unit che non toccano davvero il DB.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost:5432/fake")
    yield


@pytest.fixture
def service():
    return DataSourceService()


@pytest.fixture
def fake_db(monkeypatch):
    """Mock dei punti di accesso DB usati dal service.

    Il service usa il modulo domain-owned ``app.core.datasources.db``.
    """
    import app.core.datasources.db as datasource_db

    # Calls capture dicts
    captured = {"save": None, "update": None, "delete": [], "get": {}}

    def _save(**kwargs):
        captured["save"] = kwargs

    def _update(ds_id, **kwargs):
        captured["update"] = {"ds_id": ds_id, **kwargs}

    def _get(ds_id):
        row = captured["get"].get(ds_id)
        return datasource_db.DataSourceRow.from_mapping(row) if row else None

    def _delete(ds_id):
        captured["delete"].append(ds_id)

    monkeypatch.setattr(datasource_db, "save_datasource", _save)
    monkeypatch.setattr(datasource_db, "update_datasource", _update)
    monkeypatch.setattr(datasource_db, "get_datasource", _get)
    monkeypatch.setattr(datasource_db, "delete_datasource", _delete)

    return captured


def _make_existing_row(ds_id: str, *, password_plain: str = "pwd-old",
                       private_key_plain: str = "PEM-OLD",
                       client_secret_plain: str = "secret-old",
                       group_id: str = "grp-1",
                       created_by: str = "u-1") -> dict:
    """Costruisce una row DB plausibile con campi sensibili cifrati.

    Simula lo stato "record salvato da create_datasource, riletto da
    _get_full_config" — crypto at-rest, group_id fuori dal blob.
    """
    config = {
        "id": ds_id,
        "name": "SFTP Test",
        "description": "",
        "type": "sftp",
        "root_path": "/inbound",
        "host": "sftp.example.com",
        "port": 22,
        "username": "u",
        "password": password_plain,
        "private_key": private_key_plain,
        "client_secret": client_secret_plain,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    crypto.encrypt_dict_fields(
        config,
        ["password", "private_key", "private_key_passphrase", "client_secret"],
    )
    return {
        "id": ds_id,
        "group_id": group_id,
        "name": "SFTP Test",
        "ds_type": "sftp",
        "created_by": created_by,
        "config": config,
    }


def test_update_reencrypts_sensitive_fields_with_new_value(service, fake_db):
    """Blinda il contratto di re-encryption al save.

    Scenario: un utente cambia la password via UI. Il service DEVE
    cifrare il nuovo valore (enc:...) prima di persistere, altrimenti
    il plaintext finirebbe nel DB — violazione diretta di §1.6 crypto.
    """
    fake_db["get"]["ds_1"] = _make_existing_row("ds_1", password_plain="pwd-OLD")

    result = service.update_datasource(
        "ds_1",
        DataSourceUpdate(password="pwd-NEW"),
    )

    assert fake_db["update"] is not None, (
        "update_datasource DEVE chiamare datasource_db.update_datasource"
    )
    saved_config = fake_db["update"]["config"]
    assert crypto.is_encrypted(saved_config["password"]), (
        "nuova password DEVE essere cifrata prima del save al DB"
    )
    assert crypto.decrypt(saved_config["password"]) == "pwd-NEW", (
        "ciphertext salvato DEVE decifrare al nuovo valore"
    )
    # La response API redatta (il caller non riceve plaintext)
    assert result is not None and result.password == "***"


def test_update_skips_masked_triple_asterisk_preserving_stored_value(service, fake_db):
    """Blinda la disciplina di update "***": il client invia il
    placeholder per i campi non modificati — il service NON DEVE
    sovrascrivere il reale con ``"***"``.

    Regressione critica: senza questo filtro, salvare un update da UI
    sostituirebbe il ciphertext esistente con il ciphertext di ``"***"``,
    bricking silentemente le connessioni.
    """
    fake_db["get"]["ds_2"] = _make_existing_row(
        "ds_2",
        password_plain="pwd-KEEPME",
        private_key_plain="PEM-KEEPME",
        client_secret_plain="sec-KEEPME",
    )

    # Il client invia tutti e 4 i sensitive come "***" (non modificati)
    service.update_datasource(
        "ds_2",
        DataSourceUpdate(
            password="***",
            private_key="***",
            private_key_passphrase="***",
            client_secret="***",
            description="updated description",  # change reale fuori dai sensitive
        ),
    )

    saved_config = fake_db["update"]["config"]
    # I sensitive NON DEVONO essere "***" cifrato — devono essere i ciphertext originali.
    for field, original in {
        "password": "pwd-KEEPME",
        "private_key": "PEM-KEEPME",
        "client_secret": "sec-KEEPME",
    }.items():
        assert crypto.is_encrypted(saved_config[field]), (
            f"{field} DEVE restare cifrato (non essere re-inserito plaintext)"
        )
        assert crypto.decrypt(saved_config[field]) == original, (
            f"{field} NON DEVE essere sovrascritto da '***': "
            f"decifrato {crypto.decrypt(saved_config[field])!r}, atteso {original!r}"
        )
    # Il cambio non-sensitive DEVE invece essere salvato.
    assert saved_config["description"] == "updated description"


def test_update_strips_group_id_and_created_by_from_saved_config_blob(service, fake_db):
    """Blinda la separazione schema DB vs JSON blob:
    ``group_id`` e ``created_by`` sono COLONNE DB separate, NON devono
    finire duplicate nel blob ``config`` JSON.

    Regressione: ``_get_full_config`` inietta ``group_id`` / ``created_by``
    nel dict config per comodità del caller (response + authz). Se
    ``update_datasource`` non fa pop, quei due chiavi verrebbero
    ri-salvate dentro il blob → duplicazione, incoerenze dopo un
    aggiornamento admin della group ownership.
    """
    fake_db["get"]["ds_3"] = _make_existing_row(
        "ds_3", group_id="grp-ORIGINAL", created_by="u-ORIGINAL"
    )

    service.update_datasource("ds_3", DataSourceUpdate(description="x"))

    saved_config = fake_db["update"]["config"]
    assert "group_id" not in saved_config, (
        "group_id NON DEVE atterrare dentro il blob config (colonna DB separata)"
    )
    assert "created_by" not in saved_config, (
        "created_by NON DEVE atterrare dentro il blob config (colonna DB separata)"
    )


def test_to_model_with_redact_true_masks_all_four_sensitive_fields(service):
    """Blinda la superficie API-out: tutti e 4 i sensitive field sono
    mascherati a ``"***"`` quando ``redact=True`` (path response API).

    Il set ``_SENSITIVE_FIELDS`` del modulo è l'allowlist canonica. Un
    refactor che rimuovesse uno qualsiasi di questi nomi leakerebbe
    plaintext in response — CVE-class regression.
    """
    from app.core.datasources import service as svc_mod

    config = {
        "id": "x",
        "name": "x",
        "type": "sftp",
        "root_path": "/",
        "password": "PLAIN-PWD",
        "private_key": "PLAIN-PEM",
        "private_key_passphrase": "PLAIN-PASS",
        "client_secret": "PLAIN-SECRET",
        "username": "u",                    # NON sensitive: resta plaintext
        "host": "h",                        # NON sensitive
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    model = service._to_model(config, redact=True)

    for field in svc_mod._SENSITIVE_FIELDS:
        assert getattr(model, field) == "***", (
            f"campo sensibile {field!r} DEVE essere redatto a '***' in response API"
        )
    # Conferma che la redaction non coinvolge NON-sensitive
    assert model.username == "u"
    assert model.host == "h"


def test_to_model_with_redact_false_preserves_plaintext_for_internal_use(service):
    """Blinda il dual-mode di ``_to_model``: con ``redact=False`` il
    plaintext passa through.

    Usato internamente dove serve il valore reale (es. log diagnostica
    interna). Il contract esplicito previene che un refactor "semplifichi"
    facendo sempre redact — cosa che romperebbe gli usi interni.
    """
    config = {
        "id": "x",
        "name": "x",
        "type": "sftp",
        "root_path": "/",
        "password": "PLAIN-PWD",
        "client_secret": "PLAIN-SECRET",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    model = service._to_model(config, redact=False)

    assert model.password == "PLAIN-PWD"
    assert model.client_secret == "PLAIN-SECRET"


def test_update_nonexistent_datasource_returns_none_no_db_write(service, fake_db):
    """Blinda la policy di update su id inesistente: ritorna ``None``,
    NON solleva 404 al livello service (il router si occupa della 404).

    Scenario: un race tra due admin, uno cancella mentre l'altro updata.
    Il secondo update DEVE uscire pulito (None) senza effetti collaterali
    — nessuna scrittura al DB del record fantasma.
    """
    # fake_db["get"] intentionally vuoto → get_datasource ritorna None

    result = service.update_datasource(
        "ds_ghost",
        DataSourceUpdate(password="pwd-new"),
    )

    assert result is None
    assert fake_db["update"] is None, (
        "update_datasource NON DEVE chiamare datasource_db.update_datasource su id inesistente"
    )


# FIXME: #2 (2026-04-23): test_connection decrypt-fail policy


def test_get_full_config_strict_raises_on_undecryptable_field(service, monkeypatch):
    """Bug #2: _get_full_config(strict=True) con campo cifrato corrotto →
    CredentialUnavailableError invece di passare None al backend."""
    import app.core.datasources.db as datasource_db

    # Config con password cifrata ma non decriptabile (token random non valido)
    corrupted_config = {
        "id": "ds-broken",
        "name": "ftp-broken",
        "type": "ftp",
        "host": "example.test",
        "port": 21,
        "username": "user",
        "password": "enc:Z0FBQUFBQm5vdEF0b2tlbkNvcnJvdHRvQ2hlRmFsbGlzY2U=",  # token corrotto
    }

    monkeypatch.setattr(
        datasource_db,
        "get_datasource",
        lambda _id: datasource_db.DataSourceRow.from_mapping({
            "id": "ds-broken",
            "name": "ftp-broken",
            "ds_type": "ftp",
            "config": corrupted_config,
            "group_id": "grp-1",
            "created_by": "u-1",
        }),
    )

    with pytest.raises(crypto.CredentialUnavailableError) as exc_info:
        service._get_full_config("ds-broken", strict=True)

    assert "password" in str(exc_info.value).lower()


def test_get_full_config_non_strict_returns_none_field_silently(service, monkeypatch):
    """Bug #2 guard: retro-compat — senza strict=True, decrypt fallito
    ritorna None nel campo (comportamento Previous behavior per path NON-test).
    Serve per non rompere list_datasources / get_datasource / enrichment runtime."""
    import app.core.datasources.db as datasource_db

    corrupted_config = {
        "id": "ds-broken",
        "name": "ftp-broken",
        "type": "ftp",
        "password": "enc:Z0FBQUFBQm5vdEF0b2tlbkNvcnJvdHRvQ2hlRmFsbGlzY2U=",
    }

    monkeypatch.setattr(
        datasource_db,
        "get_datasource",
        lambda _id: datasource_db.DataSourceRow.from_mapping({
            "id": "ds-broken",
            "name": "ftp-broken",
            "ds_type": "ftp",
            "config": corrupted_config,
            "group_id": "grp-1",
            "created_by": "u-1",
        }),
    )

    config = service._get_full_config("ds-broken")  # strict default False

    assert config is not None
    assert config["password"] is None  # decrypt ha fallito silently, campo a None
