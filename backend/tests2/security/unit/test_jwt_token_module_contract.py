"""
Smoke contract: estrazione JWT/session.

Verifica le invarianti minime del refactor:
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# Costanti contratto - se cambiano, il refactor ha modificato comportamento.
_EXPECTED_ALGORITHM = "HS256"
_EXPECTED_CLAIMS = ("sub", "username", "group_id", "role", "exp", "iat", "jti")


def test_jwt_token_module_exposes_expected_symbols():
    from app.core.auth import jwt_token as jt

    assert jt.ALGORITHM == _EXPECTED_ALGORITHM
    assert isinstance(jt.ACCESS_TOKEN_EXPIRE_HOURS, int)
    assert jt.ACCESS_TOKEN_EXPIRE_HOURS > 0
    assert isinstance(jt.SECRET_KEY, str) and jt.SECRET_KEY  # non vuoto
    assert callable(jt.create_access_token)
    assert callable(jt.decode_token)


def test_auth_reexports_jwt_with_function_identity_and_value_equality():
    """auth.py re-esporta le funzioni come stessi oggetti (identita') e le
    costanti come stessi valori (equality). Le costanti sono str/int
    immutabili: identity cross-module non e' garantita ma neppure
    necessaria - basta l'equality e che entrambi puntino allo stesso
    binding del modulo nuovo via `from ... import ...`.
    """
    from app.core.auth import auth as auth_mod
    from app.core.auth import jwt_token as jt

    # Funzioni: identita' object (necessaria per monkeypatch via auth_mod)
    assert auth_mod.create_access_token is jt.create_access_token
    assert auth_mod.decode_token is jt.decode_token

    # Costanti: equality (str/int)
    assert auth_mod.ALGORITHM == jt.ALGORITHM == _EXPECTED_ALGORITHM
    assert auth_mod.ACCESS_TOKEN_EXPIRE_HOURS == jt.ACCESS_TOKEN_EXPIRE_HOURS
    assert auth_mod.SECRET_KEY == jt.SECRET_KEY


def test_create_access_token_round_trip_preserves_claim_contract():
    """Token emesso da create_access_token() decodifica con tutti i 7
    claim attesi e tipi corretti. Contratto di portabilita' (Go/altro
    control-plane): chiunque legge questo payload deve trovare gli stessi
    nomi di claim.
    """
    from app.core.auth import jwt_token as jt

    tok = jt.create_access_token(
        user_id="u-123",
        username="marco",
        group_id="grp-x",
        role="admin",
    )
    payload = jt.decode_token(tok)
    assert payload is not None, "decode di un token appena emesso non puo' fallire"

    # Tutti i 7 claim presenti
    for claim in _EXPECTED_CLAIMS:
        assert claim in payload, f"claim '{claim}' mancante nel payload"

    assert payload["sub"] == "u-123"
    assert payload["username"] == "marco"
    assert payload["group_id"] == "grp-x"
    assert payload["role"] == "admin"
    assert isinstance(payload["jti"], str) and len(payload["jti"]) == 32  # uuid4.hex


def test_decode_token_returns_none_on_garbage_and_expired():
    """Fail-closed: invalid/expired tokens producono None, non eccezioni."""
    from app.core.auth import jwt_token as jt

    assert jt.decode_token("not-a-valid-jwt-at-all") is None
    assert jt.decode_token("aaa.bbb.ccc") is None

    # Token "ben formato" ma scaduto: forgia con la stessa SECRET_KEY/ALGORITHM
    expired_payload = {
        "sub": "u-x",
        "username": "ghost",
        "group_id": "grp",
        "role": "user",
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        "iat": datetime.now(timezone.utc) - timedelta(hours=2),
        "jti": "deadbeef" * 4,
    }
    expired_tok = pyjwt.encode(expired_payload, jt.SECRET_KEY, algorithm=jt.ALGORITHM)
    assert jt.decode_token(expired_tok) is None
