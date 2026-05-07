"""
Contract test Bug #2 (2026-04-23): test_api_connection decrypt-fail policy.

Blinda il contratto: quando un campo auth cifrato e' non decriptabile
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.shared.crypto import CredentialUnavailableError


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _ensure_database_url_for_module_import(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost:5432/fake")
    yield


def test_decrypt_auth_in_config_strict_raises_on_undecryptable_field():
    """Bug #2: _decrypt_auth_in_config(strict=True) con campo enc:
    non decriptabile → CredentialUnavailableError (invece di None silent)."""
    from app.core.arricchisci._api_connection_helpers import _decrypt_auth_in_config

    corrupted_config = {
        "endpoint": {"url": "https://api.example.test/", "method": "GET"},
        "auth": {
            "type": "bearer",
            "token": "enc:Z0FBQUFBQm5vdEF0b2tlbkNvcnJvdHRvQ2hlRmFsbGlzY2U=",  # token Fernet invalido
        },
    }

    with pytest.raises(CredentialUnavailableError) as exc_info:
        _decrypt_auth_in_config(corrupted_config, strict=True)

    assert "token" in str(exc_info.value).lower()


def test_decrypt_auth_in_config_non_strict_sets_field_to_none():
    """Bug #2 guard: retro-compat — senza strict, decrypt fallito lascia
    None nel campo. Serve per non rompere flussi di enrichment runtime
    (list_api_connections_decrypted) che hanno altre forme di recovery."""
    from app.core.arricchisci._api_connection_helpers import _decrypt_auth_in_config

    corrupted_config = {
        "auth": {
            "type": "bearer",
            "token": "enc:Z0FBQUFBQm5vdEF0b2tlbkNvcnJvdHRvQ2hlRmFsbGlzY2U=",
        },
    }

    result = _decrypt_auth_in_config(corrupted_config)  # strict default False

    assert result["auth"]["token"] is None


def test_test_api_connection_propagates_credential_error_for_router():
    """Bug #2: test_api_connection(api_id) con secret non decriptabile →
    CredentialUnavailableError propagato; il router cattura e 422."""
    from app.core.arricchisci.api_connector import ApiConnectionService

    service = ApiConnectionService()

    # Simula get_api_connection con strict=True che raise
    def _raising_get(api_id, include_token=False, *, strict=False):
        assert strict is True, "test_api_connection deve passare strict=True"
        raise CredentialUnavailableError(
            f"api connection {api_id}: campo auth.token non decriptabile"
        )

    with patch.object(service, "get_api_connection", side_effect=_raising_get):
        with pytest.raises(CredentialUnavailableError) as exc_info:
            service.test_api_connection(api_id="api-broken")

    assert "non decriptabile" in str(exc_info.value)
