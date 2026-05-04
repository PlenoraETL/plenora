"""Contract test BE-GO-READY-AUTH-ENGINE-003 — users_router extraction.

Lock i 5 invariant dell'estrazione:

1. **Source location**: i 5 endpoint admin CRUD vivono in
   `app/core/auth/users_router.py` (non piu' in `auth.py`).
2. **KEEP_COMPAT**: `from app.core.auth.auth import users_router`
   continua a funzionare (re-export pubblico).
3. **KEEP_COMPAT funzionale**: le 5 funzioni-handler sono ancora
   raggiungibili come `auth_mod.<name>(...)` per i test esistenti
   in `test_auth_enumeration_contract.py` /
   `test_update_user_contract.py`.
4. **URL paths preserved**: `/auth/users` GET/POST,
   `/auth/users/{id}` PUT/DELETE,
   `/auth/users/{id}/reset-password` POST. Stessi metodi e
   stesso prefisso del pre-batch.
5. **Mount**: `app.main.app` espone gli endpoint `/api/auth/users*`
   tramite `app.include_router(users_router, prefix="/api")`.

Boundary contract NON toccato: error code, messaggi italiani,
status code, headers, anti-enumeration invariants restano gestiti
dal layer `disclosure` e sono gia' lockati da
`test_auth_disclosure_policy_contract.py` /
`test_auth_enumeration_contract.py` (entrambe suite restano verdi
post-batch).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ---------------------------------------------------------------------------
# 1. Source location
# ---------------------------------------------------------------------------


def test_users_router_module_exists():
    p = _BACKEND_ROOT / "app" / "core" / "auth" / "users_router.py"
    assert p.exists(), (
        "Atteso: app/core/auth/users_router.py creato da "
        "BE-GO-READY-AUTH-ENGINE-003."
    )


def test_users_endpoints_no_longer_inline_in_auth_py():
    """Source-bound: gli endpoint `@router.<verb>("/users...")` non
    devono piu' esistere in `auth.py` (sono in `users_router.py`)."""
    auth_src = (
        _BACKEND_ROOT / "app" / "core" / "auth" / "auth.py"
    ).read_text(encoding="utf-8")
    forbidden_patterns = [
        '@router.get("/users"',
        '@router.post("/users"',
        '@router.put("/users/',
        '@router.post("/users/',
        '@router.delete("/users/',
    ]
    for pat in forbidden_patterns:
        assert pat not in auth_src, (
            f"REGRESSIONE: pattern `{pat}` ancora presente in auth.py "
            f"dopo BE-GO-READY-AUTH-ENGINE-003."
        )


def test_users_endpoints_present_in_new_module():
    """Source-bound: i 5 endpoint sono ora attaccati a `users_router`
    nel nuovo modulo."""
    src = (
        _BACKEND_ROOT / "app" / "core" / "auth" / "users_router.py"
    ).read_text(encoding="utf-8")
    expected = [
        '@users_router.get("",',
        '@users_router.post("",',
        '@users_router.put("/{user_id}",',
        '@users_router.post("/{user_id}/reset-password")',
        '@users_router.delete("/{user_id}",',
    ]
    for pat in expected:
        assert pat in src, (
            f"Atteso: `{pat}` definito in users_router.py "
            f"(BE-GO-READY-AUTH-ENGINE-003)."
        )


# ---------------------------------------------------------------------------
# 2 + 3. KEEP_COMPAT re-export
# ---------------------------------------------------------------------------


def test_users_router_importable_from_auth_module():
    """KEEP_COMPAT: `from app.core.auth.auth import users_router`
    deve continuare a funzionare e puntare allo stesso oggetto del
    modulo nuovo."""
    from app.core.auth.auth import users_router as via_auth
    from app.core.auth.users_router import users_router as via_new

    assert via_auth is via_new


def test_user_crud_handlers_reexported_from_auth_module():
    """KEEP_COMPAT: i 5 handler sono accessibili come
    `auth_mod.list_users`, `auth_mod.create_user`,
    `auth_mod.update_user`, `auth_mod.reset_password`,
    `auth_mod.delete_user` per i test esistenti che li chiamano
    direttamente. Identita' preservata: lo stesso oggetto funzione
    vive nel nuovo modulo."""
    from app.core.auth import auth as auth_mod
    from app.core.auth import users_router as ur_mod

    for name in ("list_users", "create_user", "update_user",
                 "reset_password", "delete_user"):
        assert hasattr(auth_mod, name), (
            f"REGRESSIONE: `{name}` non e' piu' raggiungibile "
            f"come `auth_mod.{name}` dopo l'estrazione."
        )
        assert getattr(auth_mod, name) is getattr(ur_mod, name), (
            f"`auth_mod.{name}` deve essere lo stesso oggetto di "
            f"`users_router_mod.{name}` per preservare i monkeypatch "
            f"esistenti."
        )


# ---------------------------------------------------------------------------
# 4. URL paths preserved (router-level)
# ---------------------------------------------------------------------------


def test_users_router_url_paths_locked():
    """Lock dei 5 path completi (prefix incluso) esposti dal router
    nuovo. Drift in piu' o in meno = failure."""
    from app.core.auth.users_router import users_router

    expected = {
        ("GET", "/auth/users"),
        ("POST", "/auth/users"),
        ("PUT", "/auth/users/{user_id}"),
        ("POST", "/auth/users/{user_id}/reset-password"),
        ("DELETE", "/auth/users/{user_id}"),
    }
    found = set()
    for route in users_router.routes:
        # APIRoute esposti hanno `methods` (set) + `path`. I metodi
        # auto-aggiunti da starlette (HEAD per GET) sono ignorati.
        for method in (route.methods or set()):
            if method in ("HEAD", "OPTIONS"):
                continue
            found.add((method, route.path))
    assert found == expected, (
        f"Drift URL paths users_router: atteso {expected}, "
        f"trovato {found}."
    )


def test_users_router_prefix_and_tags():
    from app.core.auth.users_router import users_router

    assert users_router.prefix == "/auth/users"
    assert "users" in users_router.tags


# ---------------------------------------------------------------------------
# 5. Mount in main.py (source-bound — non importa main per evitare boot)
# ---------------------------------------------------------------------------


def test_main_mounts_users_router_with_api_prefix():
    """Source-bound check: `main.py` include il nuovo router con
    prefix `/api`, mantenendo i path effettivi
    `/api/auth/users*` invariati rispetto al pre-batch."""
    src = (_BACKEND_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "from app.core.auth.users_router import users_router" in src
    assert 'app.include_router(users_router, prefix="/api")' in src


# ---------------------------------------------------------------------------
# 6. Auth.py non importa users_router prima delle dependency necessarie
# ---------------------------------------------------------------------------


def test_auth_module_imports_users_router_after_dependencies():
    """Sanity: `from app.core.auth.users_router import users_router`
    deve essere DOPO la definizione delle dependency riusate
    (`require_admin`, `hash_password`, `CurrentUser`,
    `VALID_ROLES`, `ROLES_ADMIN_OR_ABOVE`). Altrimenti il circular
    import `users_router.py -> auth.py` spaccherebbe il boot."""
    src = (
        _BACKEND_ROOT / "app" / "core" / "auth" / "auth.py"
    ).read_text(encoding="utf-8")
    idx_users_router_import = src.find(
        "from app.core.auth.users_router import"
    )
    assert idx_users_router_import > 0, (
        "Atteso: `from app.core.auth.users_router import ...` in auth.py."
    )
    # `require_admin` definita come `def require_admin(...)`, deve
    # apparire PRIMA dell'import del modulo nuovo.
    idx_require_admin = src.find("def require_admin(")
    assert 0 < idx_require_admin < idx_users_router_import, (
        "REGRESSIONE: `require_admin` deve essere definita prima "
        "dell'import di `users_router` per evitare circular import."
    )
    # `hash_password` re-export da `app.shared.password_hashing`,
    # cercato come substring.
    idx_hash_password = src.find("hash_password,")
    assert 0 < idx_hash_password < idx_users_router_import, (
        "REGRESSIONE: re-export di `hash_password` deve precedere "
        "l'import di `users_router`."
    )
