"""
tests/security/unit/test_block_F_D_auth.py
==========================================
Unit test per il Blocco F-D: 2 fix auth hardening.

Copre:
  - F10 JWT middleware: token scaduti/invalidi → request.state pulito + log warning
  - F11 crypto.py: rifiuta default key in prod, key troppo corte, opt-in dev
"""

import asyncio
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# F10 — JWT Middleware: token scaduto / invalido
# ============================================================================

class TestJWTMiddlewareTokenExpired:
    """Test sincroni che invocano `dispatch` async via asyncio.run."""

    def _make_mw_request(self, auth_header):
        from app.core.auth.auth_middleware import JWTMiddleware
        mw = JWTMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers.get.return_value = auth_header
        request.state = MagicMock()
        request.url.path = "/api/test"
        call_next = AsyncMock(return_value=MagicMock())
        return mw, request, call_next

    def test_valid_token_populates_state(self):
        mw, request, call_next = self._make_mw_request("Bearer valid-token-xyz")
        with patch("app.core.auth.auth_middleware.decode_token") as mock_dec:
            mock_dec.return_value = {
                "sub": "u1", "username": "alice",
                "group_id": "g1", "role": "user",
            }
            asyncio.run(mw.dispatch(request, call_next))
        assert request.state.group_id == "g1"
        assert request.state.user_id == "u1"
        assert request.state.username == "alice"
        assert request.state.role == "user"

    def test_expired_token_clears_state(self):
        """Token presente ma scaduto: decode_token ritorna None → state pulito."""
        mw, request, call_next = self._make_mw_request("Bearer expired-token")
        with patch("app.core.auth.auth_middleware.decode_token", return_value=None):
            asyncio.run(mw.dispatch(request, call_next))
        assert request.state.group_id == ""
        assert request.state.user_id == ""
        assert request.state.username == ""
        assert request.state.role == ""

    def test_no_auth_header_clears_state(self):
        mw, request, call_next = self._make_mw_request("")
        asyncio.run(mw.dispatch(request, call_next))
        assert request.state.group_id == ""
        assert request.state.role == ""

    def test_call_next_always_invoked(self):
        """Il middleware non blocca mai la richiesta — propaga sempre a call_next."""
        mw, request, call_next = self._make_mw_request("Bearer expired")
        with patch("app.core.auth.auth_middleware.decode_token", return_value=None):
            asyncio.run(mw.dispatch(request, call_next))
        call_next.assert_called_once_with(request)


# ============================================================================
# F11 — crypto.py: rifiuto default key in prod, key corte, opt-in dev
# ============================================================================

class TestCryptoKeyHardening:

    def setup_method(self):
        # Reset cache Fernet prima di ogni test (env var possono cambiare)
        from app.shared.crypto import _reset_cache_for_tests
        _reset_cache_for_tests()
        # Salva le env var originali per ripristinarle
        self._saved_env = {
            k: os.environ.get(k) for k in (
                "URBY_ENCRYPTION_KEY", "CONN_ENCRYPT_KEY",
                "SECRET_KEY", "JWT_SECRET_KEY",
                "ENVIRONMENT", "ENV", "URBY_DISABLE_DEFAULT_KEY",
            )
        }
        # Pulisci tutte
        for k in self._saved_env:
            if k in os.environ:
                del os.environ[k]

    def teardown_method(self):
        # Ripristina env originali
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            elif k in os.environ:
                del os.environ[k]
        from app.shared.crypto import _reset_cache_for_tests
        _reset_cache_for_tests()

    def test_valid_key_accepted(self):
        from app.shared.crypto import _get_raw_key
        os.environ["URBY_ENCRYPTION_KEY"] = "a-very-secure-random-key-12345"
        assert _get_raw_key() == "a-very-secure-random-key-12345"

    def test_short_key_rejected(self):
        from app.shared.crypto import _get_raw_key
        os.environ["URBY_ENCRYPTION_KEY"] = "short"  # 5 char
        with pytest.raises(RuntimeError) as exc:
            _get_raw_key()
        assert "troppo corta" in str(exc.value).lower()

    def test_default_key_rejected_in_prod(self):
        from app.shared.crypto import _get_raw_key, _DEV_DEFAULT_KEY
        os.environ["URBY_ENCRYPTION_KEY"] = _DEV_DEFAULT_KEY
        os.environ["ENVIRONMENT"] = "production"
        with pytest.raises(RuntimeError) as exc:
            _get_raw_key()
        assert "default" in str(exc.value).lower() or "rischio" in str(exc.value).lower()

    def test_default_key_warned_in_dev(self):
        """In dev, la dev key passata via env genera warning ma non blocca."""
        from app.shared.crypto import _get_raw_key, _DEV_DEFAULT_KEY
        os.environ["URBY_ENCRYPTION_KEY"] = _DEV_DEFAULT_KEY
        os.environ["ENVIRONMENT"] = "development"
        # Non solleva
        result = _get_raw_key()
        assert result == _DEV_DEFAULT_KEY

    def test_no_key_fails_fast_in_prod(self):
        from app.shared.crypto import _get_raw_key
        os.environ["ENVIRONMENT"] = "production"
        with pytest.raises(RuntimeError) as exc:
            _get_raw_key()
        assert "nessuna chiave" in str(exc.value).lower()

    def test_no_key_uses_default_in_dev(self):
        from app.shared.crypto import _get_raw_key, _DEV_DEFAULT_KEY
        os.environ["ENVIRONMENT"] = "development"
        result = _get_raw_key()
        assert result == _DEV_DEFAULT_KEY

    def test_disable_default_key_opt_in(self):
        """URBY_DISABLE_DEFAULT_KEY=1 in dev forza fail-fast."""
        from app.shared.crypto import _get_raw_key
        os.environ["ENVIRONMENT"] = "development"
        os.environ["URBY_DISABLE_DEFAULT_KEY"] = "1"
        with pytest.raises(RuntimeError) as exc:
            _get_raw_key()
        assert "URBY_DISABLE_DEFAULT_KEY" in str(exc.value)

    def test_priority_order(self):
        """URBY_ENCRYPTION_KEY ha priorità su CONN_ENCRYPT_KEY/SECRET_KEY/JWT_SECRET_KEY."""
        from app.shared.crypto import _get_raw_key
        os.environ["URBY_ENCRYPTION_KEY"] = "primary-key-1234567890"
        os.environ["CONN_ENCRYPT_KEY"] = "fallback-key-abcdefghij"
        os.environ["SECRET_KEY"] = "another-fallback-key12345"
        assert _get_raw_key() == "primary-key-1234567890"

    def test_fallback_to_secret_key(self):
        from app.shared.crypto import _get_raw_key
        os.environ["SECRET_KEY"] = "fallback-secret-key-12345678"
        assert _get_raw_key() == "fallback-secret-key-12345678"
