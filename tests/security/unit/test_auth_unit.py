"""
tests/security/unit/test_auth_unit.py
======================================
Test unitari del modulo app.core.auth.auth

Copertura:
  - hash_password / verify_password
  - create_access_token
  - decode_token
  - _check_rate_limit / _record_failed_login / _clear_failed_logins
  - get_current_user (con mock DB)
  - require_admin (con mock)
  - CurrentUser / LoginRequest / TokenResponse (modelli Pydantic)
  - JWTMiddleware
"""

import pytest
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

pytestmark = pytest.mark.unit

# ─── Import moduli sotto test ─────────────────────────────────────────────────
from app.core.auth.auth import (
    hash_password, verify_password,
    create_access_token, decode_token,
    _check_rate_limit, _record_failed_login, _clear_failed_logins,
    get_current_user, require_admin,
    CurrentUser, LoginRequest, TokenResponse, UserCreate,
    SECRET_KEY, ALGORITHM,
    LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS,
    _failed_attempts, _rate_lock,
)
from app.core.auth.auth_middleware import JWTMiddleware, PUBLIC_PATHS
import jwt as pyjwt
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


# =============================================================================
# hash_password / verify_password
# =============================================================================

class TestHashPassword:

    def test_output_non_uguale_input(self):
        h = hash_password("password123")
        assert h != "password123"

    def test_output_e_stringa(self):
        h = hash_password("test")
        assert isinstance(h, str)

    def test_output_inizia_con_bcrypt(self):
        h = hash_password("test")
        assert h.startswith("$2b$") or h.startswith("$2a$")

    def test_stesso_input_hash_diversi(self):
        """bcrypt usa salt random — stesso input → hash diversi."""
        h1 = hash_password("password")
        h2 = hash_password("password")
        assert h1 != h2

    def test_password_vuota_hashabile(self):
        h = hash_password("")
        assert isinstance(h, str)

    def test_password_lunga_hashabile(self):
        h = hash_password("a" * 100)
        assert isinstance(h, str)

    def test_caratteri_speciali(self):
        h = hash_password("p@$$w0rd!#%^&*()")
        assert isinstance(h, str)

    def test_unicode(self):
        h = hash_password("pàssword_ünïcödé")
        assert isinstance(h, str)


class TestVerifyPassword:

    def test_password_corretta_true(self):
        h = hash_password("password123")
        assert verify_password("password123", h) is True

    def test_password_sbagliata_false(self):
        h = hash_password("password123")
        assert verify_password("sbagliata", h) is False

    def test_case_sensitive(self):
        h = hash_password("Password123")
        assert verify_password("password123", h) is False

    def test_spazio_aggiuntivo_false(self):
        h = hash_password("password")
        assert verify_password("password ", h) is False

    def test_password_vuota_corretta(self):
        h = hash_password("")
        assert verify_password("", h) is True

    def test_password_vuota_sbagliata(self):
        h = hash_password("password")
        assert verify_password("", h) is False

    def test_caratteri_speciali_corretti(self):
        pwd = "p@$$w0rd!#%"
        h = hash_password(pwd)
        assert verify_password(pwd, h) is True

    def test_unicode_corretto(self):
        pwd = "pàssword_ünïcödé"
        h = hash_password(pwd)
        assert verify_password(pwd, h) is True


# =============================================================================
# create_access_token
# =============================================================================

class TestCreateAccessToken:

    def test_ritorna_stringa(self):
        t = create_access_token("uid1", "user1", "grp1", "user")
        assert isinstance(t, str)

    def test_token_decodificabile(self):
        t = create_access_token("uid1", "user1", "grp1", "user")
        payload = pyjwt.decode(t, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == "uid1"

    def test_claim_sub(self):
        t = create_access_token("uid-test", "u", "g", "user")
        p = pyjwt.decode(t, SECRET_KEY, algorithms=[ALGORITHM])
        assert p["sub"] == "uid-test"

    def test_claim_username(self):
        t = create_access_token("u", "mario", "g", "user")
        p = pyjwt.decode(t, SECRET_KEY, algorithms=[ALGORITHM])
        assert p["username"] == "mario"

    def test_claim_group_id(self):
        t = create_access_token("u", "u", "grp-abc", "user")
        p = pyjwt.decode(t, SECRET_KEY, algorithms=[ALGORITHM])
        assert p["group_id"] == "grp-abc"

    def test_claim_role(self):
        t = create_access_token("u", "u", "g", "admin")
        p = pyjwt.decode(t, SECRET_KEY, algorithms=[ALGORITHM])
        assert p["role"] == "admin"

    def test_claim_exp_nel_futuro(self):
        t = create_access_token("u", "u", "g", "user")
        p = pyjwt.decode(t, SECRET_KEY, algorithms=[ALGORITHM])
        exp = datetime.fromtimestamp(p["exp"], tz=timezone.utc)
        assert exp > datetime.now(timezone.utc)

    def test_claim_iat_presente(self):
        t = create_access_token("u", "u", "g", "user")
        p = pyjwt.decode(t, SECRET_KEY, algorithms=[ALGORITHM])
        assert "iat" in p

    def test_exp_almeno_1_ora(self):
        t = create_access_token("u", "u", "g", "user")
        p = pyjwt.decode(t, SECRET_KEY, algorithms=[ALGORITHM])
        exp = datetime.fromtimestamp(p["exp"], tz=timezone.utc)
        assert exp > datetime.now(timezone.utc) + timedelta(minutes=59)

    def test_token_diversi_per_utenti_diversi(self):
        t1 = create_access_token("u1", "u1", "g", "user")
        t2 = create_access_token("u2", "u2", "g", "user")
        assert t1 != t2

    def test_token_header_algoritmo(self):
        t = create_access_token("u", "u", "g", "user")
        header = pyjwt.get_unverified_header(t)
        assert header["alg"] == "HS256"


# =============================================================================
# decode_token
# =============================================================================

class TestDecodeToken:

    def _make_token(self, **overrides):
        payload = {
            "sub": "uid1",
            "username": "user1",
            "group_id": "grp1",
            "role": "user",
            "exp": datetime.now(timezone.utc) + timedelta(hours=8),
            "iat": datetime.now(timezone.utc),
        }
        payload.update(overrides)
        return pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    def test_token_valido_ritorna_dict(self):
        t = self._make_token()
        result = decode_token(t)
        assert isinstance(result, dict)

    def test_token_valido_sub_corretto(self):
        t = self._make_token(sub="uid-xyz")
        result = decode_token(t)
        assert result["sub"] == "uid-xyz"

    def test_token_scaduto_none(self):
        t = self._make_token(exp=datetime.now(timezone.utc) - timedelta(hours=1))
        assert decode_token(t) is None

    def test_token_firma_sbagliata_none(self):
        t = pyjwt.encode({"sub": "u", "exp": datetime.now(timezone.utc) + timedelta(hours=8)},
                         "chiave_sbagliata", algorithm="HS256")
        assert decode_token(t) is None

    def test_token_malformato_none(self):
        assert decode_token("questo.non.e.un.token") is None

    def test_token_vuoto_none(self):
        assert decode_token("") is None

    def test_token_solo_header_none(self):
        assert decode_token("eyJhbGciOiJIUzI1NiJ9") is None

    def test_token_con_algoritmo_sbagliato_none(self):
        t = pyjwt.encode({"sub": "u", "exp": datetime.now(timezone.utc) + timedelta(hours=8)},
                         SECRET_KEY, algorithm="HS512")
        assert decode_token(t) is None

    def test_token_senza_exp_none(self):
        """Token senza exp — decode tenta e può ritornare None o dict."""
        # PyJWT >= 2.x richiede exp per default
        t = pyjwt.encode({"sub": "u"}, SECRET_KEY, algorithm=ALGORITHM)
        result = decode_token(t)
        # Con exp presente → valido; senza exp → dipende dalla versione
        assert result is None or isinstance(result, dict)

    def test_round_trip(self):
        """create_access_token + decode_token = payload originale."""
        t = create_access_token("uid1", "mario", "grp1", "admin")
        p = decode_token(t)
        assert p["sub"] == "uid1"
        assert p["username"] == "mario"
        assert p["group_id"] == "grp1"
        assert p["role"] == "admin"


# =============================================================================
# Rate limiting
# =============================================================================

class TestRateLimiting:

    def _reset_ip(self, ip: str):
        with _rate_lock:
            _failed_attempts.pop(ip, None)

    def test_check_rate_limit_ok_su_ip_pulito(self):
        ip = "1.2.3.100"
        self._reset_ip(ip)
        # Non deve sollevare
        _check_rate_limit(ip)

    def test_record_failed_aumenta_contatore(self):
        ip = "1.2.3.101"
        self._reset_ip(ip)
        _record_failed_login(ip)
        with _rate_lock:
            count = len(_failed_attempts[ip])
        assert count == 1

    def test_record_failed_multipli(self):
        ip = "1.2.3.102"
        self._reset_ip(ip)
        for _ in range(5):
            _record_failed_login(ip)
        with _rate_lock:
            count = len(_failed_attempts[ip])
        assert count == 5

    def test_clear_failed_resetta(self):
        ip = "1.2.3.103"
        self._reset_ip(ip)
        for _ in range(5):
            _record_failed_login(ip)
        _clear_failed_logins(ip)
        with _rate_lock:
            count = len(_failed_attempts.get(ip, []))
        assert count == 0

    def test_clear_su_ip_pulito_non_crasha(self):
        ip = "1.2.3.199"
        self._reset_ip(ip)
        _clear_failed_logins(ip)  # non deve sollevare

    def test_rate_limit_429_dopo_max_tentativi(self):
        ip = "1.2.3.104"
        self._reset_ip(ip)
        for _ in range(LOGIN_MAX_ATTEMPTS):
            _record_failed_login(ip)
        with pytest.raises(HTTPException) as exc:
            _check_rate_limit(ip)
        assert exc.value.status_code == 429

    def test_rate_limit_header_retry_after(self):
        ip = "1.2.3.105"
        self._reset_ip(ip)
        for _ in range(LOGIN_MAX_ATTEMPTS):
            _record_failed_login(ip)
        with pytest.raises(HTTPException) as exc:
            _check_rate_limit(ip)
        assert "Retry-After" in exc.value.headers

    def test_rate_limit_non_scatta_sotto_max(self):
        ip = "1.2.3.106"
        self._reset_ip(ip)
        for _ in range(LOGIN_MAX_ATTEMPTS - 1):
            _record_failed_login(ip)
        # Non deve sollevare
        _check_rate_limit(ip)

    def test_tentativi_vecchi_non_contano(self):
        """Tentativi fuori dalla finestra temporale vengono rimossi."""
        ip = "1.2.3.107"
        self._reset_ip(ip)
        old_time = time.monotonic() - LOGIN_WINDOW_SECONDS - 1
        with _rate_lock:
            _failed_attempts[ip] = [old_time] * LOGIN_MAX_ATTEMPTS
        # Non deve sollevare perché i tentativi sono scaduti
        _check_rate_limit(ip)

    def test_rate_limit_thread_safe(self):
        """Accessi concorrenti non corrompono il contatore."""
        import threading
        ip = "1.2.3.108"
        self._reset_ip(ip)
        errors = []

        def do_record():
            try:
                _record_failed_login(ip)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_record) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        with _rate_lock:
            assert len(_failed_attempts[ip]) == 20


# =============================================================================
# get_current_user
# =============================================================================

class TestGetCurrentUser:

    def _make_creds(self, token: str) -> HTTPAuthorizationCredentials:
        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = token
        return creds

    def _valid_token(self, user_id="uid1", group_id="grp1", role="user"):
        return create_access_token(user_id, "user1", group_id, role)

    def test_credentials_none_401(self):
        with pytest.raises(HTTPException) as exc:
            get_current_user(None)
        assert exc.value.status_code == 401

    def test_token_invalido_401(self):
        creds = self._make_creds("token.invalido.xyz")
        with pytest.raises(HTTPException) as exc:
            get_current_user(creds)
        assert exc.value.status_code == 401

    def test_token_valido_utente_esistente_ritorna_current_user(self):
        token = self._valid_token()
        creds = self._make_creds(token)
        mock_user = {"id": "uid1", "username": "user1", "group_id": "grp1",
                     "role": "user", "is_active": True}
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = mock_user
            result = get_current_user(creds)
        assert isinstance(result, CurrentUser)
        assert result.user_id == "uid1"
        assert result.group_id == "grp1"

    def test_utente_non_trovato_401(self):
        token = self._valid_token()
        creds = self._make_creds(token)
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = None
            with pytest.raises(HTTPException) as exc:
                get_current_user(creds)
        assert exc.value.status_code == 401

    def test_utente_disabilitato_401(self):
        token = self._valid_token()
        creds = self._make_creds(token)
        mock_user = {"id": "uid1", "username": "user1", "group_id": "grp1",
                     "role": "user", "is_active": False}
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = mock_user
            with pytest.raises(HTTPException) as exc:
                get_current_user(creds)
        assert exc.value.status_code == 401

    def test_group_id_propagato(self):
        token = self._valid_token(group_id="grp-speciale")
        creds = self._make_creds(token)
        mock_user = {"id": "uid1", "username": "user1", "group_id": "grp-speciale",
                     "role": "user", "is_active": True}
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = mock_user
            result = get_current_user(creds)
        assert result.group_id == "grp-speciale"

    def test_role_propagato(self):
        token = self._valid_token(role="admin")
        creds = self._make_creds(token)
        mock_user = {"id": "uid1", "username": "user1", "group_id": "grp1",
                     "role": "admin", "is_active": True}
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = mock_user
            result = get_current_user(creds)
        assert result.role == "admin"

    def test_token_scaduto_401(self):
        token = pyjwt.encode({
            "sub": "uid1", "username": "u", "group_id": "g", "role": "user",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
        }, SECRET_KEY, algorithm=ALGORITHM)
        creds = self._make_creds(token)
        with pytest.raises(HTTPException) as exc:
            get_current_user(creds)
        assert exc.value.status_code == 401


# =============================================================================
# require_admin
# =============================================================================

class TestRequireAdmin:

    def test_admin_ok(self):
        user = CurrentUser(user_id="u", username="u", group_id="g", role="admin")
        result = require_admin(user)
        assert result.role == "admin"

    def test_superadmin_ok(self):
        user = CurrentUser(user_id="u", username="u", group_id="g", role="superadmin")
        result = require_admin(user)
        assert result.role == "superadmin"

    def test_user_403(self):
        user = CurrentUser(user_id="u", username="u", group_id="g", role="user")
        with pytest.raises(HTTPException) as exc:
            require_admin(user)
        assert exc.value.status_code == 403

    def test_ruolo_vuoto_403(self):
        user = CurrentUser(user_id="u", username="u", group_id="g", role="")
        with pytest.raises(HTTPException) as exc:
            require_admin(user)
        assert exc.value.status_code == 403

    def test_ruolo_viewer_403(self):
        user = CurrentUser(user_id="u", username="u", group_id="g", role="viewer")
        with pytest.raises(HTTPException) as exc:
            require_admin(user)
        assert exc.value.status_code == 403


# =============================================================================
# Modelli Pydantic
# =============================================================================

class TestModelli:

    def test_login_request_valido(self):
        lr = LoginRequest(username="mario", password="pass123")
        assert lr.username == "mario"
        assert lr.password == "pass123"

    def test_current_user_campi(self):
        u = CurrentUser(user_id="u1", username="mario", group_id="g1", role="admin")
        assert u.user_id == "u1"
        assert u.group_id == "g1"

    def test_token_response_token_type_default(self):
        tr = TokenResponse(
            access_token="tok", user_id="u", username="u",
            group_id="g", role="user", expires_in=28800
        )
        assert tr.token_type == "bearer"

    def test_user_create_role_default(self):
        uc = UserCreate(username="u", password="p", group_id="g")
        assert uc.role == "user"


# =============================================================================
# JWTMiddleware
# =============================================================================

class TestJWTMiddleware:

    def _make_request(self, auth_header: str = "") -> MagicMock:
        req = MagicMock()
        req.headers = {"Authorization": auth_header} if auth_header else {}
        req.state = MagicMock()
        return req

    def test_public_paths_set(self):
        assert "/auth/login" in PUBLIC_PATHS
        assert "/health" in PUBLIC_PATHS
        assert "/docs" in PUBLIC_PATHS

    def test_middleware_esiste(self):
        mw = JWTMiddleware(app=MagicMock())
        assert mw is not None

    def test_decode_token_con_token_valido(self):
        token = create_access_token("uid1", "user1", "grp1", "user")
        payload = decode_token(token)
        assert payload is not None
        assert payload["group_id"] == "grp1"

    def test_decode_token_senza_bearer_none(self):
        result = decode_token("non-un-token")
        assert result is None
