"""
Tests the datasource crypto behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import pytest

from app.core.datasources.service import DataSourceService
from app.shared.crypto import decrypt, is_encrypted
from tests2.helpers.builders import utc_now


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def test_create_datasource_encrypts_sensitive_fields_before_save(monkeypatch):
    captured = {}
    service = DataSourceService()

    import app.core.datasources.db as datasource_db
    from app.core.datasources.models import DataSourceCreate

    monkeypatch.setattr(service, "_generate_id", lambda name: "ds_test")
    monkeypatch.setattr(
        datasource_db,
        "save_datasource",
        lambda **kwargs: captured.update(kwargs),
    )

    ds = service.create_datasource(
        DataSourceCreate(
            name="SFTP",
            type="sftp",
            root_path="/inbound",
            username="user-1",
            password="pwd-123",
            private_key="PRIVATE-KEY",
            private_key_passphrase="passphrase-123",
            client_secret="secret-123",
        ),
        group_id="grp-1",
        created_by="u-1",
    )

    config = captured["config"]
    for field, plaintext in {
        "password": "pwd-123",
        "private_key": "PRIVATE-KEY",
        "private_key_passphrase": "passphrase-123",
        "client_secret": "secret-123",
    }.items():
        assert is_encrypted(config[field])
        assert decrypt(config[field]) == plaintext

    assert ds.password == "***"
    assert ds.private_key == "***"
    assert ds.client_secret == "***"


def test_get_full_config_decrypts_sensitive_fields_from_storage(monkeypatch):
    service = DataSourceService()

    import app.core.datasources.db as datasource_db
    from app.shared.crypto import encrypt_dict_fields

    row = {
        "group_id": "grp-1",
        "created_by": "u-1",
        "config": {
            "id": "ds_test",
            "name": "SFTP",
            "description": "",
            "type": "sftp",
            "root_path": "/inbound",
            "password": "pwd-123",
            "private_key": "PRIVATE-KEY",
            "private_key_passphrase": "passphrase-123",
            "client_secret": "secret-123",
            "created_at": utc_now().isoformat(),
            "updated_at": utc_now().isoformat(),
        },
    }
    encrypt_dict_fields(
        row["config"],
        ["password", "private_key", "private_key_passphrase", "client_secret"],
    )
    monkeypatch.setattr(
        datasource_db,
        "get_datasource",
        lambda ds_id: datasource_db.DataSourceRow.from_mapping({
            **row,
            "id": "ds_test",
            "name": "SFTP",
            "ds_type": "sftp",
        }),
    )

    config = service._get_full_config("ds_test")

    assert config["password"] == "pwd-123"
    assert config["private_key"] == "PRIVATE-KEY"
    assert config["private_key_passphrase"] == "passphrase-123"
    assert config["client_secret"] == "secret-123"
