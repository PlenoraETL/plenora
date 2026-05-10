"""
Contract test BE-GO-READY-AUTH-ENGINE-004 — groups_router extraction.

Lock i 6 invariant dell'estrazione:
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# 1. Source location


def test_groups_router_module_exists():
    p = _BACKEND_ROOT / "app" / "core" / "auth" / "groups_router.py"
    assert p.exists(), (
        "Atteso: app/core/auth/groups_router.py creato da "
        "BE-GO-READY-AUTH-ENGINE-004."
    )


def test_groups_endpoints_no_longer_inline_in_auth_py():
    """Source-bound: gli endpoint `@groups_router.<verb>(...)` non
    devono piu' esistere in `auth.py` (sono in `groups_router.py`)."""
    auth_src = (
        _BACKEND_ROOT / "app" / "core" / "auth" / "auth.py"
    ).read_text(encoding="utf-8")
    forbidden_patterns = [
        '@groups_router.get("",',
        '@groups_router.post("",',
        '@groups_router.get("/{group_id}/summary"',
        '@groups_router.delete("/{group_id}"',
    ]
    for pat in forbidden_patterns:
        assert pat not in auth_src, (
            f"REGRESSIONE: pattern `{pat}` ancora presente in auth.py "
            f"dopo BE-GO-READY-AUTH-ENGINE-004."
        )
    # Anche la definizione del router non deve essere piu' in auth.py.
    assert 'groups_router = APIRouter(prefix="/auth/groups"' not in auth_src, (
        "REGRESSIONE: `groups_router = APIRouter(...)` deve vivere in "
        "groups_router.py, non in auth.py."
    )


def test_groups_endpoints_present_in_new_module():
    """Source-bound: i 4 endpoint sono ora attaccati a `groups_router`
    nel nuovo modulo."""
    src = (
        _BACKEND_ROOT / "app" / "core" / "auth" / "groups_router.py"
    ).read_text(encoding="utf-8")
    expected = [
        '@groups_router.get("",',
        '@groups_router.post("",',
        '@groups_router.get("/{group_id}/summary")',
        '@groups_router.delete("/{group_id}",',
    ]
    for pat in expected:
        assert pat in src, (
            f"Atteso: `{pat}` definito in groups_router.py "
            f"(BE-GO-READY-AUTH-ENGINE-004)."
        )


# 2 + 3. KEEP_COMPAT re-export


def test_groups_router_importable_from_auth_module():
    """KEEP_COMPAT: `from app.core.auth.auth import groups_router`
    deve continuare a funzionare e puntare allo stesso oggetto del
    modulo nuovo."""
    from app.core.auth.auth import groups_router as via_auth
    from app.core.auth.groups_router import groups_router as via_new

    assert via_auth is via_new


def test_group_crud_handlers_canonical_home_is_groups_router():
    """BE-GO-READY-AUTH-ENGINE-021-USERS-GROUPS-REEXPORT-REMOVAL:
    i 4 handler vivono ESCLUSIVAMENTE in `app.core.auth.groups_router`
    (canonical home post-AUTH-ENGINE-004 extraction). Il facade
    `app.core.auth.auth` NON li re-esporta piu'.

    Pre-021 il facade re-esportava `list_groups` / `create_group` /
    `get_group_summary` / `delete_group` per supportare due test che
    li chiamavano via `auth_mod.<name>`. Quei test sono stati migrati
    al modulo canonico e il re-export e' stato rimosso. Il lock
    simmetrico (assenza dal facade) vive in
    `test_auth_models_and_ownership_module_contract.py::
    test_auth_no_longer_reexports_removed_handlers`.
    """
    from app.core.auth import auth as auth_mod
    from app.core.auth import groups_router as gr_mod

    for name in ("list_groups", "create_group", "get_group_summary",
                 "delete_group"):
        # Positive lock: handler is callable from canonical home.
        assert callable(getattr(gr_mod, name, None)), (
            f"REGRESSIONE: `{name}` non e' callable da "
            f"`app.core.auth.groups_router`."
        )
        # Negative lock: handler is NO LONGER on the facade.
        assert not hasattr(auth_mod, name), (
            f"REGRESSIONE BE-GO-READY-AUTH-ENGINE-021: `{name}` e' "
            f"stato re-introdotto su `app.core.auth.auth`. Il canonical "
            f"home e' `app.core.auth.groups_router`; chiama via quel "
            f"modulo invece di `auth_mod.<name>`."
        )


# 4. URL paths preserved (router-level)


def test_groups_router_url_paths_locked():
    """Lock dei 4 path completi (prefix incluso) esposti dal router
    nuovo. Drift in piu' o in meno = failure."""
    from app.core.auth.groups_router import groups_router

    expected = {
        ("GET", "/auth/groups"),
        ("POST", "/auth/groups"),
        ("GET", "/auth/groups/{group_id}/summary"),
        ("DELETE", "/auth/groups/{group_id}"),
    }
    found = set()
    for route in groups_router.routes:
        for method in (route.methods or set()):
            if method in ("HEAD", "OPTIONS"):
                continue
            found.add((method, route.path))
    assert found == expected, (
        f"Drift URL paths groups_router: atteso {expected}, "
        f"trovato {found}."
    )


def test_groups_router_prefix_and_tags():
    from app.core.auth.groups_router import groups_router

    assert groups_router.prefix == "/auth/groups"
    assert "groups" in groups_router.tags


# 5. Mount in API composition root (source-bound)


def test_api_composition_mounts_groups_router():
    """Source-bound check: `api.py` include il nuovo router e `main.py`
    monta l'api_router con prefix `/api`, mantenendo i path effettivi
    `/api/auth/groups*` invariati rispetto al Previous contract."""
    api_src = (_BACKEND_ROOT / "app" / "core" / "api.py").read_text(
        encoding="utf-8"
    )
    main_src = (_BACKEND_ROOT / "app" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "from app.core.auth.groups_router import groups_router" in api_src
    assert "api_router.include_router(groups_router)" in api_src
    assert "from app.core.api import api_router" in main_src
    assert 'app.include_router(api_router, prefix="/api")' in main_src


# 6. No circular import (top-level imports)


def test_groups_router_does_not_top_level_import_auth_dependencies():
    """Mirror del fix BE-GO-READY-AUTH-ENGINE-003: l'import di
    `require_admin / require_superadmin / get_current_user` da
    `app.core.auth.auth` al top-level di `groups_router.py` creerebbe
    un cycle (`groups_router -> auth.py -> groups_router`) perche'
    `auth.py` ri-esporta `groups_router`. Il modulo deve usare
    proxy-locali con import lazy dentro `def`-body.
    """
    src = (
        _BACKEND_ROOT / "app" / "core" / "auth" / "groups_router.py"
    ).read_text(encoding="utf-8")
    # Skip the module docstring (which legitimately documents the
    # forbidden patterns as anti-pattern). Use `ast` to walk only
    # actual module-level Import / ImportFrom nodes.
    import ast as _ast

    tree = _ast.parse(src)
    forbidden_names = {"require_admin", "require_superadmin", "get_current_user"}
    violations = []
    for node in tree.body:
        # Only top-level imports (not inside function defs / classes).
        if isinstance(node, _ast.ImportFrom) and node.module == "app.core.auth.auth":
            for alias in node.names:
                if alias.name in forbidden_names:
                    violations.append(alias.name)
    assert not violations, (
        f"REGRESSIONE: top-level import {sorted(violations)} da "
        f"`app.core.auth.auth` in groups_router.py crea cycle con "
        f"`auth.py` (che ri-esporta groups_router). Usa un "
        f"proxy-locale con import lazy."
    )


def test_groups_router_uses_lazy_proxies_for_dependencies():
    """Lock positivo della struttura proxy-lazy: il modulo deve
    definire i 3 proxy locali per `get_current_user`, `require_admin`,
    `require_superadmin` con import lazy dentro il corpo della
    funzione."""
    src = (
        _BACKEND_ROOT / "app" / "core" / "auth" / "groups_router.py"
    ).read_text(encoding="utf-8")
    assert "def _get_current_user_proxy(" in src
    assert "def require_admin(" in src
    assert "def require_superadmin(" in src
    # Lazy: l'import deve essere dentro la funzione (cerco la
    # sequenza "def <name>(...):\n    ... from app.core.auth.auth import").
    assert "from app.core.auth.dependencies import get_current_user" in src
    assert (
        "from app.core.auth.dependencies import require_admin as _require_admin"
        in src
    )
    assert (
        "from app.core.auth.dependencies import "
        "require_superadmin as _require_superadmin"
        in src
    )
