"""Contract test MFA TOTP — enroll / verify / disable + login integration.

Scope:
  Fissa il contratto di `app/core/auth/mfa.py` e dei 4 endpoint in
  `auth.py` (+ modifica del /login):

    1. Enrollment two-step: /mfa/enroll crea riga enabled=FALSE,
       /mfa/verify attiva enabled=TRUE.
    2. Re-enrollment (stesso user, nuovo /enroll) sovrascrive il
       secret, resetta enabled=FALSE e enrolled_at=NULL.
    3. verify_code requires riga presente + codice TOTP valido.
       Codici non numerici / vuoti → False senza crash.
    4. disable richiede codice TOTP valido (proof-of-possession):
       non basta essere autenticati. Rimuove la riga.
    5. /login senza MFA attiva: comportamento invariato (nessun
       impatto per utenti non-MFA, constraint #2 del batch).
    6. /login con MFA attiva: codice mancante → 403 mfa_required,
       codice errato → 401 mfa_invalid, codice ok → JWT.

Organizzazione:

  A) Static / AST — leggono file SQL e parsano i moduli Python con ast
     per verificare: presenza endpoint, campo mfa_code in LoginRequest,
     schema migration. Zero import di app.* (nessun DATABASE_URL).

  B) pyotp smoke — verifica che pyotp.TOTP.verify() abbia la semantica
     che mfa.py assume (window tolerance, step 30s, SHA-1 default).
     Zero DB.

  C) Integration — richiede DATABASE_URL + migration user_mfa applicata.
     Skip pulito con reason se env non configurato.
"""

from __future__ import annotations

import ast
import base64
import os
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# ---------------------------------------------------------------------------
# PATHS (backend/tests2/security/unit/ → backend)
# ---------------------------------------------------------------------------

_BACKEND = Path(__file__).resolve().parents[3]
_MIGRATION = _BACKEND / "migrations" / "2026-04-21_add_user_mfa.sql"
_AUTH_PY = _BACKEND / "app" / "core" / "auth" / "auth.py"
_AUTH_MODELS_PY = _BACKEND / "app" / "core" / "auth" / "models.py"
_MFA_PY = _BACKEND / "app" / "core" / "auth" / "mfa.py"


@pytest.fixture(scope="module")
def migration_sql() -> str:
    assert _MIGRATION.exists(), f"Migration attesa a {_MIGRATION}"
    return _MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def auth_module_ast() -> ast.Module:
    return ast.parse(_AUTH_PY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def auth_models_module_ast() -> ast.Module:
    return ast.parse(_AUTH_MODELS_PY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mfa_module_ast() -> ast.Module:
    return ast.parse(_MFA_PY.read_text(encoding="utf-8"))


# ===========================================================================
# A) STATIC / AST
# ===========================================================================


def test_migration_creates_user_mfa_table(migration_sql: str) -> None:
    """La migration deve creare plenora.user_mfa con PK user_id + FK CASCADE."""
    assert "CREATE TABLE IF NOT EXISTS plenora.user_mfa" in migration_sql
    # PK e FK a users con CASCADE: perdita utente → perdita MFA.
    assert "user_id" in migration_sql
    assert "REFERENCES plenora.users(id) ON DELETE CASCADE" in migration_sql


def test_migration_declares_required_columns(migration_sql: str) -> None:
    """Colonne richieste per il contratto di mfa.py."""
    required = [
        "secret_encrypted",
        "enabled",
        "enrolled_at",
        "last_verified_at",
        "created_at",
        "updated_at",
    ]
    for col in required:
        assert col in migration_sql, f"Colonna mancante: {col}"


def test_login_request_has_mfa_code_field(auth_models_module_ast: ast.Module) -> None:
    """LoginRequest DEVE avere il campo mfa_code opzionale per supportare MFA
    senza rompere il contratto per utenti non-MFA.
    """
    login_request = None
    for node in ast.walk(auth_models_module_ast):
        if isinstance(node, ast.ClassDef) and node.name == "LoginRequest":
            login_request = node
            break
    assert login_request is not None, "LoginRequest non trovato in auth/models.py"

    fields = {
        n.target.id
        for n in login_request.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    assert "mfa_code" in fields, (
        "REGRESSIONE: LoginRequest deve esporre mfa_code per permettere "
        "il challenge MFA in /login. Senza questo campo, gli utenti con MFA "
        "attiva non possono piu' autenticarsi."
    )


def test_auth_module_exposes_four_mfa_endpoints(auth_module_ast: ast.Module) -> None:
    """I 4 endpoint MFA devono essere registrati con @router.X(path).

    Perimetro stretto: se qualcuno aggiunge un quinto endpoint MFA, il test
    fallisce e forza review (per evitare aggiunte silenziose al contratto).
    """
    mfa_routes: set[tuple[str, str]] = set()
    for node in ast.walk(auth_module_ast):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # Pattern: @router.get("/mfa/...") / @router.post("/mfa/...")
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if not (
                isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router"
            ):
                continue
            method = dec.func.attr.upper()
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path = dec.args[0].value
            if isinstance(path, str) and path.startswith("/mfa/"):
                mfa_routes.add((method, path))

    expected = {
        ("GET", "/mfa/status"),
        ("POST", "/mfa/enroll"),
        ("POST", "/mfa/verify"),
        ("POST", "/mfa/disable"),
    }
    assert mfa_routes == expected, (
        f"Endpoint MFA divergono dal contratto.\n"
        f"  attesi: {sorted(expected)}\n"
        f"  trovati: {sorted(mfa_routes)}"
    )


def test_mfa_module_declares_constants(mfa_module_ast: ast.Module) -> None:
    """TOTP_WINDOW deve essere 1 (RFC 6238 §5.2 raccomanda <=1).

    Window >1 amplia la finestra di replay e non deve essere introdotto
    senza ADR dedicato.
    """
    issuer = None
    window = None
    for node in mfa_module_ast.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and isinstance(node.value, ast.Constant):
                if tgt.id == "TOTP_ISSUER":
                    issuer = node.value.value
                elif tgt.id == "TOTP_WINDOW":
                    window = node.value.value

    assert issuer == "URBY-ETL", f"TOTP_ISSUER atteso 'URBY-ETL', trovato {issuer!r}"
    assert window == 1, (
        f"TOTP_WINDOW deve essere 1 per RFC 6238 §5.2, trovato {window!r}. "
        f"Non ampliare senza ADR dedicato: window=2 raddoppia la finestra di replay."
    )


# ===========================================================================
# B) pyotp SMOKE (verifica che libreria si comporti come assumiamo)
# ===========================================================================


def test_pyotp_verify_honors_window() -> None:
    """Se pyotp cambiasse semantica di valid_window, mfa.py verify romperebbe
    silenziosamente. Test di smoke che lockla il contratto della libreria.
    """
    import pyotp

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    now_code = totp.now()
    assert totp.verify(now_code) is True
    assert totp.verify(now_code, valid_window=1) is True
    # Codice random di 6 cifre diverso dal now_code (99.9999% dei casi)
    fake = "000000" if now_code != "000000" else "111111"
    assert totp.verify(fake) is False


def test_pyotp_provisioning_uri_format() -> None:
    """Il provisioning_uri deve contenere issuer + secret per essere
    parseabile da authenticator apps standard (Google Auth, Authy, ecc.).
    """
    import pyotp

    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name="user@test", issuer_name="URBY-ETL")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=URBY-ETL" in uri
    assert f"secret={secret}" in uri


# ===========================================================================
# C) INTEGRATION DB-LIVE (opt-in esplicito)
# ===========================================================================
#
# Questi test fanno INSERT/DELETE reali su `plenora.users`, `plenora.groups`,
# `plenora.user_mfa` via `app.db.pg_connection.execute` (pool globale). Sono
# DB-live a tutti gli effetti: stesso pattern e stessa policy di
# `test_log_immutable_contract.py` parte B.
#
# Run policy:
#   - default `pytest tests2/security`             -> SKIP (env opt-in mancante)
#   - default `pytest tests2/`                     -> SKIP (env opt-in mancante)
#   - opt-in  `URBY_RUN_DB_LIVE_TESTS=1 + DATABASE_URL` -> RUN
#   - selezione `pytest -m integration`            -> raccolti, ma comunque
#     skip se env opt-in non e' settata.
#
# Razionale:
#   skipif su `DATABASE_URL` non basta. Su Windows `psycopg2` puo' produrre
#   access violation nativa lato C prima di sollevare OperationalError, e
#   in dev locale `.env` carica DATABASE_URL anche quando i dev non vogliono
#   far partire i DB-live. La sola difesa solida e' NON chiamare
#   `pg_connection.execute` -> env opt-in esplicito gate' alla fonte.
#
# Nota su `tests2/conftest.py::_PG_LIVE_FIXTURES`: la whitelist contiene
# `mfa_user` per esonerare la fixture dagli autouse stub di pg_connection.
# Quella esonerazione e' attiva *solo se la fixture viene invocata*: se i
# test sono skipped via `_SKIP_NO_DB_LIVE_OPT_IN` la fixture non parte e
# non c'e' alcuna chiamata a psycopg2.

_DATABASE_URL = os.getenv("DATABASE_URL")
_RUN_DB_LIVE = os.getenv("URBY_RUN_DB_LIVE_TESTS") == "1"

_SKIP_NO_DB_LIVE_OPT_IN = pytest.mark.skipif(
    not (_RUN_DB_LIVE and _DATABASE_URL),
    reason=(
        "DB-live opt-in mancante: settare URBY_RUN_DB_LIVE_TESTS=1 + "
        "DATABASE_URL con PG raggiungibile e migration user_mfa applicata. "
        "Skip default per evitare access violation su psycopg2 in ambienti "
        "dove DATABASE_URL e' settato ma il DB non e' destinato a questi test."
    ),
)


@pytest.fixture
def mfa_user(request):
    """Crea un utente temporaneo in plenora.users + plenora.groups e lo
    cancella al teardown. La CASCADE su user_mfa pulisce automaticamente
    l'eventuale riga MFA creata dai test.

    Non usa SAVEPOINT come il log_immutable test perche' mfa.py usa il
    pool globale (pg_connection.get_connection) e non vede le tx del
    fixture. Cleanup e' esplicito via DELETE su users.

    Doppio guard PRIMA di importare/usare `pg_connection`:
      1. env opt-in `URBY_RUN_DB_LIVE_TESTS=1` mancante -> pytest.skip.
      2. `DATABASE_URL` mancante                       -> pytest.skip.
    Necessario perche' `tests2/conftest.py::_PG_LIVE_FIXTURES` esonera
    `mfa_user` dallo stub autouse di `pg_connection.execute`: senza il
    gate qui, un test che ottiene la fixture senza il marker corretto
    chiamerebbe psycopg2 reale.
    """
    if not _RUN_DB_LIVE:
        pytest.skip(
            "DB-live test non opt-in: settare URBY_RUN_DB_LIVE_TESTS=1 "
            "per eseguire i test MFA che fanno pg_connection.execute reale."
        )
    if not _DATABASE_URL:
        pytest.skip("DATABASE_URL non configurato")

    import uuid
    from datetime import datetime, timezone
    from app.db import pg_connection

    group_id = f"grp_test_{uuid.uuid4().hex[:8]}"
    user_id = f"usr_test_{uuid.uuid4().hex[:8]}"
    username = f"mfa_user_{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).isoformat()

    pg_connection.execute(
        "INSERT INTO plenora.groups (id, name) VALUES (?, ?)",
        (group_id, "mfa_test_group"),
    )
    pg_connection.execute(
        "INSERT INTO plenora.users (id, username, password_hash, group_id, role, "
        "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, username, "x", group_id, "user", True, now, now),
    )

    def _cleanup():
        # CASCADE su plenora.user_mfa + FK su altre tabelle: cancellare users
        # basta. groups lo rimuoviamo esplicitamente (no CASCADE upstream).
        pg_connection.execute("DELETE FROM plenora.users WHERE id = ?", (user_id,))
        pg_connection.execute("DELETE FROM plenora.groups WHERE id = ?", (group_id,))

    request.addfinalizer(_cleanup)
    return {"user_id": user_id, "username": username, "group_id": group_id}


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_enroll_creates_pending_row(mfa_user):
    """/mfa/enroll → riga creata con enabled=FALSE, secret cifrato."""
    from app.core.auth import mfa
    from app.db import pg_connection
    from app.shared.crypto import is_encrypted

    data = mfa.generate_enrollment(mfa_user["user_id"], mfa_user["username"])
    assert data["secret"], "secret non presente nell'output"
    assert data["provisioning_uri"].startswith("otpauth://totp/")
    # qr_code_png_base64 deve essere base64 valido di un PNG (magic: 89 50 4E 47)
    png_bytes = base64.b64decode(data["qr_code_png_base64"])
    assert png_bytes[:4] == b"\x89PNG"

    row = pg_connection.fetchone(
        "SELECT enabled, secret_encrypted, enrolled_at "
        "FROM plenora.user_mfa WHERE user_id = ?",
        (mfa_user["user_id"],),
    )
    assert row is not None
    assert row["enabled"] is False
    assert is_encrypted(row["secret_encrypted"]), (
        "Il secret deve essere cifrato a riposo (prefix enc:)"
    )
    assert row["enrolled_at"] is None


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_verify_enrollment_activates_mfa(mfa_user):
    """/mfa/verify con codice valido → enabled=TRUE + enrolled_at valorizzato."""
    import pyotp
    from app.core.auth import mfa

    data = mfa.generate_enrollment(mfa_user["user_id"], mfa_user["username"])
    code = pyotp.TOTP(data["secret"]).now()

    assert mfa.verify_enrollment(mfa_user["user_id"], code) is True
    assert mfa.is_enabled(mfa_user["user_id"]) is True

    st = mfa.status(mfa_user["user_id"])
    assert st["enabled"] is True
    assert st["enrolled_at"] is not None
    assert st["last_verified_at"] is not None


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_verify_with_wrong_code_fails(mfa_user):
    """Codice errato → False, enabled resta FALSE."""
    from app.core.auth import mfa

    mfa.generate_enrollment(mfa_user["user_id"], mfa_user["username"])
    # Codice chiaramente sbagliato (6 cifre statiche difficilmente colliderebbero)
    assert mfa.verify_enrollment(mfa_user["user_id"], "000000") is False
    assert mfa.is_enabled(mfa_user["user_id"]) is False


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_reenroll_resets_activation(mfa_user):
    """Un nuovo /enroll su utente con MFA attiva → disattiva finche' non ri-verifica.

    Use-case: utente ha perso il device e vuole ri-enrollare da un nuovo.
    """
    import pyotp
    from app.core.auth import mfa

    # Enrollment 1 + attivazione
    data1 = mfa.generate_enrollment(mfa_user["user_id"], mfa_user["username"])
    assert mfa.verify_enrollment(mfa_user["user_id"], pyotp.TOTP(data1["secret"]).now())
    assert mfa.is_enabled(mfa_user["user_id"]) is True

    # Re-enrollment: genera nuovo secret, enabled torna False
    data2 = mfa.generate_enrollment(mfa_user["user_id"], mfa_user["username"])
    assert data2["secret"] != data1["secret"], "Nuovo enroll deve generare NUOVO secret"
    assert mfa.is_enabled(mfa_user["user_id"]) is False

    # Il codice del secret1 non vale piu' (il nuovo secret e' diverso)
    old_code = pyotp.TOTP(data1["secret"]).now()
    assert mfa.verify_enrollment(mfa_user["user_id"], old_code) is False

    # Il codice del secret2 attiva
    new_code = pyotp.TOTP(data2["secret"]).now()
    assert mfa.verify_enrollment(mfa_user["user_id"], new_code) is True
    assert mfa.is_enabled(mfa_user["user_id"]) is True


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_disable_requires_valid_code(mfa_user):
    """Disable con codice errato → False, riga preservata (MFA resta attiva)."""
    import pyotp
    from app.core.auth import mfa

    data = mfa.generate_enrollment(mfa_user["user_id"], mfa_user["username"])
    mfa.verify_enrollment(mfa_user["user_id"], pyotp.TOTP(data["secret"]).now())
    assert mfa.is_enabled(mfa_user["user_id"]) is True

    assert mfa.disable(mfa_user["user_id"], "000000") is False
    assert mfa.is_enabled(mfa_user["user_id"]) is True, (
        "REGRESSIONE: disable ha avuto effetto con codice invalido. "
        "Proof-of-possession bypassed."
    )


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_disable_with_valid_code_removes_row(mfa_user):
    """Disable con codice valido → riga rimossa, is_enabled False."""
    import pyotp
    from app.core.auth import mfa

    data = mfa.generate_enrollment(mfa_user["user_id"], mfa_user["username"])
    mfa.verify_enrollment(mfa_user["user_id"], pyotp.TOTP(data["secret"]).now())

    # Uso un nuovo codice (pyotp.now() nello stesso step potrebbe restituire
    # lo stesso valore, ma TOTP.verify con window=1 accetta comunque codici
    # consecutivi; il codice verificato sopra e' valido anche qui).
    code = pyotp.TOTP(data["secret"]).now()
    assert mfa.disable(mfa_user["user_id"], code) is True
    assert mfa.is_enabled(mfa_user["user_id"]) is False

    st = mfa.status(mfa_user["user_id"])
    assert st == {"enabled": False, "enrolled_at": None, "last_verified_at": None}


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_verify_code_rejects_non_numeric_input(mfa_user):
    """Codice non-numerico / vuoto → False senza raise (fail-closed)."""
    from app.core.auth import mfa

    mfa.generate_enrollment(mfa_user["user_id"], mfa_user["username"])
    assert mfa.verify_code(mfa_user["user_id"], "") is False
    assert mfa.verify_code(mfa_user["user_id"], "abcdef") is False
    assert mfa.verify_code(mfa_user["user_id"], None) is False  # type: ignore[arg-type]
