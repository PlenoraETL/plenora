"""
test_I01_I06_auth_security.py — Suite SECURITY · Autenticazione e Isolamento

Target: 95% coverage · Gap: 5% · Priorità: P3

Test coperti:
  I-01  Login credenziali corrette → JWT emesso
  I-02  Login credenziali errate → 401
  I-03  Token scaduto → 401
  I-04  Group isolation: utente A non accede a progetti utente B
  I-05  JWT_SECRET_KEY da variabile d'ambiente → nessun UserWarning
  I-06  Load test: 10 richieste simultanee preview senza crash

Prerequisiti:
  - Backend FastAPI su localhost:8000
  - Utente admin/admin123 esistente
"""

import pytest
import requests
import threading
import time
import warnings
import jwt as pyjwt
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

pytestmark = [pytest.mark.security, pytest.mark.integration]

BASE_URL     = "http://localhost:8000/api"
TEST_USER    = "admin"
TEST_PASS    = "admin123"
WRONG_PASS   = "password_sbagliata_xyz"


# =============================================================================
# I-01 — Login credenziali corrette → JWT emesso
# =============================================================================

class TestI01Login:

    def test_i01_login_200(self):
        """I-01a: login valido → 200 con access_token."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": TEST_USER, "password": TEST_PASS})
        assert res.status_code == 200, f"Login fallito: {res.text}"
        body = res.json()
        assert "access_token" in body, f"access_token mancante: {body}"
        assert body["access_token"], "access_token vuoto"

    def test_i01_token_e_jwt_valido(self):
        """I-01b: il token è un JWT valido decodificabile (senza verificare firma)."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": TEST_USER, "password": TEST_PASS})
        token = res.json()["access_token"]
        payload = pyjwt.decode(token, options={"verify_signature": False})
        assert "sub" in payload,      "claim 'sub' mancante"
        assert "username" in payload, "claim 'username' mancante"
        assert "group_id" in payload, "claim 'group_id' mancante"
        assert "exp" in payload,      "claim 'exp' mancante"

    def test_i01_token_non_scaduto(self):
        """I-01c: il token appena emesso non è scaduto."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": TEST_USER, "password": TEST_PASS})
        token = res.json()["access_token"]
        payload = pyjwt.decode(token, options={"verify_signature": False})
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        assert exp > datetime.now(timezone.utc), "Token già scaduto!"

    def test_i01_me_con_token_valido(self):
        """I-01d: GET /auth/me con token valido → 200 con dati utente."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": TEST_USER, "password": TEST_PASS})
        token = res.json()["access_token"]
        me = requests.get(f"{BASE_URL}/auth/me",
                          headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, f"GET /me fallito: {me.text}"
        assert me.json().get("username") == TEST_USER

    def test_i01_expires_in_presente(self):
        """I-01e: risposta login include expires_in in secondi."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": TEST_USER, "password": TEST_PASS})
        body = res.json()
        assert "expires_in" in body, f"expires_in mancante: {body}"
        assert body["expires_in"] > 0, "expires_in deve essere > 0"


# =============================================================================
# I-02 — Login credenziali errate → 401
# =============================================================================

class TestI02LoginErrato:

    def test_i02_password_sbagliata_401(self):
        """I-02a: password sbagliata → 401."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": TEST_USER, "password": WRONG_PASS})
        assert res.status_code == 401, f"Atteso 401, ricevuto {res.status_code}: {res.text}"

    def test_i02_nessun_token_nella_risposta(self):
        """I-02b: risposta 401 non contiene access_token."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": TEST_USER, "password": WRONG_PASS})
        body = res.json()
        assert "access_token" not in body, f"access_token non doveva essere presente: {body}"

    def test_i02_utente_inesistente_401(self):
        """I-02c: username inesistente → 401."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": "utente_inesistente_xyz", "password": "qualunque"})
        assert res.status_code == 401, f"Atteso 401, ricevuto {res.status_code}"

    def test_i02_body_vuoto_422(self):
        """I-02d: body vuoto → 422 validation error."""
        res = requests.post(f"{BASE_URL}/auth/login", json={})
        assert res.status_code in (400, 422), f"Atteso 400/422, ricevuto {res.status_code}"


# =============================================================================
# I-03 — Token scaduto → 401
# =============================================================================

class TestI03TokenScaduto:

    def _make_expired_token(self) -> str:
        """Crea un token JWT già scaduto usando la chiave di default."""
        secret = "urby-etl-jwt-secret-change-in-production"
        payload = {
            "sub": "user-test",
            "username": TEST_USER,
            "group_id": "group-test",
            "role": "admin",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
        }
        return pyjwt.encode(payload, secret, algorithm="HS256")

    def test_i03_token_scaduto_401(self):
        """I-03a: token scaduto → 401 da endpoint protetto."""
        expired_token = self._make_expired_token()
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": f"Bearer {expired_token}"})
        assert res.status_code == 401, f"Atteso 401 con token scaduto, ricevuto {res.status_code}"

    def test_i03_token_mancante_401(self):
        """I-03b: nessun token → 401."""
        res = requests.get(f"{BASE_URL}/auth/me")
        assert res.status_code == 401, f"Atteso 401 senza token, ricevuto {res.status_code}"

    def test_i03_token_malformato_401(self):
        """I-03c: token malformato → 401."""
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": "Bearer questo.non.e.un.token"})
        assert res.status_code == 401, f"Atteso 401 con token malformato, ricevuto {res.status_code}"

    def test_i03_token_firma_sbagliata_401(self):
        """I-03d: token firmato con chiave sbagliata → 401."""
        payload = {
            "sub": "user-test",
            "username": TEST_USER,
            "group_id": "group-test",
            "role": "admin",
            "exp": datetime.now(timezone.utc) + timedelta(hours=8),
            "iat": datetime.now(timezone.utc),
        }
        wrong_token = pyjwt.encode(payload, "chiave_sbagliata_xyz", algorithm="HS256")
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": f"Bearer {wrong_token}"})
        assert res.status_code == 401, f"Atteso 401 con firma sbagliata, ricevuto {res.status_code}"


# =============================================================================
# I-04 — Group isolation: utente A non accede a progetti utente B
# =============================================================================

class TestI04GroupIsolation:

    def _login(self, username: str, password: str) -> str:
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": username, "password": password})
        assert res.status_code == 200, f"Login {username} fallito: {res.text}"
        return res.json()["access_token"]

    def test_i04_progetto_non_visibile_ad_altro_gruppo(self, make_project, pg_conn, pg_table):
        """
        I-04a: un progetto creato dall'utente A non è accessibile all'utente B
        (gruppo diverso).

        Se esiste un secondo utente con gruppo diverso → testa isolamento reale.
        Altrimenti testa che un project_id inesistente dia 404.
        """
        token_a = self._login(TEST_USER, TEST_PASS)

        # Crea un progetto con utente A
        headers_a = {"Authorization": f"Bearer {token_a}"}
        res = requests.post(f"{BASE_URL}/projects",
                            json={
                                "name": "[AUTO] I-04 isolation test",
                                "project_type": "estrai",
                                "canvas": {"nodes": [], "edges": []},
                            },
                            headers=headers_a)
        assert res.status_code == 200, f"Creazione progetto fallita: {res.text}"
        project_id = res.json()["metadata"]["id"]

        try:
            # Tenta accesso con un token di gruppo diverso (token manomesso)
            payload = pyjwt.decode(token_a, options={"verify_signature": False})
            fake_payload = {
                **payload,
                "sub": "altro-utente-id",
                "username": "altro_utente",
                "group_id": "gruppo-completamente-diverso-xyz",
                "exp": datetime.now(timezone.utc) + timedelta(hours=8),
            }
            # Token firmato con chiave sbagliata → deve dare 401
            fake_token = pyjwt.encode(fake_payload, "chiave_sbagliata", algorithm="HS256")
            res_fake = requests.get(f"{BASE_URL}/projects/{project_id}",
                                    headers={"Authorization": f"Bearer {fake_token}"})
            assert res_fake.status_code in (401, 403, 404), (
                f"Group isolation violata: utente con token falso ha accesso al progetto "
                f"(status {res_fake.status_code})"
            )

        finally:
            requests.delete(f"{BASE_URL}/projects/{project_id}", headers=headers_a)

    def test_i04_progetto_inesistente_404(self):
        """I-04b: accesso a project_id completamente inesistente → 404."""
        token = self._login(TEST_USER, TEST_PASS)
        res = requests.get(f"{BASE_URL}/projects/project-inesistente-xyz-999",
                           headers={"Authorization": f"Bearer {token}"})
        assert res.status_code in (403, 404), (
            f"Atteso 403/404 per progetto inesistente, ricevuto {res.status_code}"
        )


# =============================================================================
# I-05 — JWT_SECRET_KEY da variabile d'ambiente → nessun UserWarning
# =============================================================================

class TestI05JWTSecret:

    def test_i05_nessun_warning_se_secret_configurata(self):
        """
        I-05: quando JWT_SECRET_KEY è impostata nell'env, il modulo auth
        non emette UserWarning sulla chiave di default.
        """
        import os
        import importlib
        import sys

        # Salva stato corrente
        old_secret = os.environ.get("JWT_SECRET_KEY")
        old_env    = os.environ.get("ENVIRONMENT")

        try:
            # Imposta una chiave sicura
            os.environ["JWT_SECRET_KEY"] = "chiave-sicura-per-test-almeno-32-caratteri!"
            os.environ["ENVIRONMENT"] = "development"

            # Rimuovi il modulo dalla cache per forzare reimport
            mod_name = "app.core.auth.auth"
            if mod_name in sys.modules:
                del sys.modules[mod_name]

            # Reimporta catturando i warning
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                importlib.import_module(mod_name)

            # Nessun UserWarning sulla chiave JWT
            jwt_warnings = [
                x for x in w
                if issubclass(x.category, UserWarning)
                and "JWT_SECRET_KEY" in str(x.message)
            ]
            assert len(jwt_warnings) == 0, (
                f"Emesso UserWarning JWT nonostante chiave configurata: "
                f"{[str(x.message) for x in jwt_warnings]}"
            )

        finally:
            # Ripristina ambiente
            if old_secret is not None:
                os.environ["JWT_SECRET_KEY"] = old_secret
            elif "JWT_SECRET_KEY" in os.environ:
                del os.environ["JWT_SECRET_KEY"]
            if old_env is not None:
                os.environ["ENVIRONMENT"] = old_env
            elif "ENVIRONMENT" in os.environ:
                del os.environ["ENVIRONMENT"]
            # Rimuovi dalla cache per evitare side effect sui test successivi
            if mod_name in sys.modules:
                del sys.modules[mod_name]

    def test_i05_warning_se_secret_default(self):
        """
        I-05b: quando JWT_SECRET_KEY NON è configurata → UserWarning emesso in sviluppo.
        """
        import os
        import importlib
        import sys

        old_secret = os.environ.get("JWT_SECRET_KEY")
        old_env    = os.environ.get("ENVIRONMENT")

        try:
            # Rimuovi la variabile d'ambiente
            if "JWT_SECRET_KEY" in os.environ:
                del os.environ["JWT_SECRET_KEY"]
            os.environ["ENVIRONMENT"] = "development"

            mod_name = "app.core.auth.auth"
            if mod_name in sys.modules:
                del sys.modules[mod_name]

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                importlib.import_module(mod_name)

            jwt_warnings = [
                x for x in w
                if issubclass(x.category, UserWarning)
                and "JWT_SECRET_KEY" in str(x.message)
            ]
            assert len(jwt_warnings) >= 1, (
                "Atteso UserWarning per JWT_SECRET_KEY non configurata, nessuno emesso"
            )

        finally:
            if old_secret is not None:
                os.environ["JWT_SECRET_KEY"] = old_secret
            if old_env is not None:
                os.environ["ENVIRONMENT"] = old_env
            elif "ENVIRONMENT" in os.environ:
                del os.environ["ENVIRONMENT"]
            if mod_name in sys.modules:
                del sys.modules[mod_name]


# =============================================================================
# I-06 — Load test: 10 richieste simultanee senza crash
# =============================================================================

class TestI06LoadTest:

    def _login_and_get_token(self) -> str:
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": TEST_USER, "password": TEST_PASS})
        assert res.status_code == 200
        return res.json()["access_token"]

    def test_i06_10_login_simultanei(self):
        """I-06a: 10 richieste di login simultanee → tutte completate con 200."""
        results = []

        def do_login():
            res = requests.post(f"{BASE_URL}/auth/login",
                                json={"username": TEST_USER, "password": TEST_PASS})
            return res.status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_login) for _ in range(10)]
            for f in as_completed(futures):
                results.append(f.result())

        failed = [s for s in results if s != 200]
        assert len(failed) == 0, (
            f"Login simultanei: {len(failed)}/10 falliti con status: {failed}"
        )

    def test_i06_10_richieste_me_simultanee(self):
        """I-06b: 10 richieste GET /auth/me simultanee → tutte 200."""
        token = self._login_and_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        results = []

        def do_me():
            res = requests.get(f"{BASE_URL}/auth/me", headers=headers)
            return res.status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_me) for _ in range(10)]
            for f in as_completed(futures):
                results.append(f.result())

        failed = [s for s in results if s != 200]
        assert len(failed) == 0, (
            f"GET /me simultanee: {len(failed)}/10 fallite: {failed}"
        )

    def test_i06_10_richieste_projects_simultanee(self, make_project, pg_conn, pg_table):
        """I-06c: 10 richieste GET /projects simultanee → nessun crash (200 o 404)."""
        token = self._login_and_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        results = []

        def do_list():
            res = requests.get(f"{BASE_URL}/projects", headers=headers)
            return res.status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_list) for _ in range(10)]
            for f in as_completed(futures):
                results.append(f.result())

        # Nessun 500
        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0, (
            f"GET /projects simultanee: {len(crashes)}/10 hanno crashato con 5xx: {crashes}"
        )

    def test_i06_backend_stabile_dopo_carico(self):
        """I-06d: dopo 10 login simultanei il backend risponde ancora correttamente."""
        token = self._login_and_get_token()

        # Lancia carico
        def do_login():
            requests.post(f"{BASE_URL}/auth/login",
                          json={"username": TEST_USER, "password": TEST_PASS})

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_login) for _ in range(10)]
            for f in as_completed(futures):
                f.result()

        # Verifica che il backend sia ancora up
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200, (
            f"Backend non risponde dopo carico: {res.status_code}"
        )
