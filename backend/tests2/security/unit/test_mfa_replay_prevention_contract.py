"""
Contract test per la prevenzione del replay TOTP (RFC 6238 §5).

Bug coperto (plenora-bug-security-auth P1):
  Pre-fix `pyotp.TOTP.verify(valid_window=1)` era stateless: lo stesso
  codice TOTP poteva essere riutilizzato entro la finestra di 90s. Se
  un attaccante catturava il codice (shoulder-surfing, phishing, MitM
  su HTTP non-TLS), poteva riusarlo come secondo fattore prima della
  scadenza dello step.

Post-fix: blacklist short-lived in `plenora.user_mfa_used_codes` (vedi
migration `2026-05-21_add_user_mfa_used_codes.sql`). Dopo ogni verify
riuscito il code-hash viene registrato; un secondo verify dello stesso
codice nello stesso step viene respinto.

Test scope: copre la primitiva pura `_hash_used_code` e il dispatch
`verify_code → _try_register_used_code`. Il path DB (`_try_register_used_code`
esegue INSERT su PG) e' coperto da integration test e2e-v2.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.auth import mfa


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


class TestHashUsedCodeDeterministic:
    """`_hash_used_code` deve essere deterministico e sensibile a entrambi
    gli input (user_id + code) per evitare collisioni inter-utente.
    """

    def test_same_input_yields_same_hash(self):
        h1 = mfa._hash_used_code("u-1", "123456")
        h2 = mfa._hash_used_code("u-1", "123456")
        assert h1 == h2

    def test_different_user_yields_different_hash(self):
        h1 = mfa._hash_used_code("u-1", "123456")
        h2 = mfa._hash_used_code("u-2", "123456")
        assert h1 != h2, (
            "Lo stesso codice TOTP usato da utenti diversi NON deve "
            "collidere: ogni utente ha la sua blacklist."
        )

    def test_different_code_yields_different_hash(self):
        h1 = mfa._hash_used_code("u-1", "111111")
        h2 = mfa._hash_used_code("u-1", "222222")
        assert h1 != h2

    def test_hash_is_hex_sha256(self):
        h = mfa._hash_used_code("u-1", "123456")
        # SHA-256 hex digest = 64 char
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestVerifyCodeAntiReplay:
    """`verify_code` deve chiamare `_try_register_used_code` SUBITO dopo
    `totp.verify()` positivo, e rifiutare se la registrazione fallisce
    (replay rilevato).
    """

    def test_replay_blocked_returns_false_and_skips_db_update(self):
        # Setup: simula un utente con MFA attiva e secret noto.
        fake_row = {
            "user_id": "u-1",
            "secret_encrypted": "enc:fake",
            "enabled": True,
            "enrolled_at": None,
            "last_verified_at": None,
        }

        with patch.object(mfa, "_row_for", return_value=fake_row), \
             patch.object(mfa, "decrypt", return_value="ABCDEFGHIJKLMNOP"), \
             patch("pyotp.TOTP") as MockTOTP, \
             patch.object(mfa, "_try_register_used_code", return_value=False) as spy_register, \
             patch.object(mfa.mfa_store, "touch_last_verified") as spy_touch:

            MockTOTP.return_value.verify.return_value = True

            result = mfa.verify_code("u-1", "123456")

        assert result is False, (
            "Quando _try_register_used_code rifiuta (codice gia' usato), "
            "verify_code deve ritornare False senza accettare il TOTP."
        )
        spy_register.assert_called_once_with("u-1", "123456")
        # Il DB UPDATE last_verified_at (mfa_store.touch_last_verified) NON
        # deve essere eseguito quando il codice viene rifiutato come replay.
        spy_touch.assert_not_called()

    def test_success_calls_register_before_db_update(self):
        fake_row = {
            "user_id": "u-1",
            "secret_encrypted": "enc:fake",
            "enabled": True,
            "enrolled_at": None,
            "last_verified_at": None,
        }
        call_order: list[str] = []

        def _register_spy(*_a, **_kw):
            call_order.append("register")
            return True

        def _touch_spy(*_a, **_kw):
            call_order.append("db_update")

        with patch.object(mfa, "_row_for", return_value=fake_row), \
             patch.object(mfa, "decrypt", return_value="ABCDEFGHIJKLMNOP"), \
             patch("pyotp.TOTP") as MockTOTP, \
             patch.object(mfa, "_try_register_used_code", side_effect=_register_spy), \
             patch.object(mfa.mfa_store, "touch_last_verified", side_effect=_touch_spy):

            MockTOTP.return_value.verify.return_value = True

            result = mfa.verify_code("u-1", "123456")

        assert result is True
        # register MUST be called BEFORE db_update — altrimenti il
        # `last_verified_at` viene aggiornato anche su replay rifiutato.
        assert call_order == ["register", "db_update"]


class TestReplayBlacklistTtl:
    """Lock del TTL: deve essere sufficiente a coprire la finestra TOTP
    (3 step * 30s = 90s minimo).
    """

    def test_ttl_covers_window(self):
        # (TOTP_WINDOW + 2) * 30s — vedi commento in mfa.py.
        # TOTP_WINDOW=1 → TTL = 3 * 30 = 90s
        seconds = mfa._REPLAY_BLACKLIST_TTL.total_seconds()
        assert seconds >= 90, (
            f"TTL della blacklist deve coprire almeno 3 step TOTP (90s); "
            f"trovato {seconds}s"
        )
