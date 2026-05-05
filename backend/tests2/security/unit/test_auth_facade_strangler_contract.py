"""Source-bound guard — BE-GO-READY-AUTH-ENGINE-005-FACADE-STRANGLER-PHASE-1.

Locks Phase-1 of the auth-facade strangler: the 3 callsites migrated
in this stream MUST NOT regress to importing from `app.core.auth.auth`.

Migrated callsites:
  1. `app/core/auth/auth_middleware.py`
       → `decode_token` from `app.shared.jwt_token` (was facade hop).
  2. `app/core/auth/ownership.py`
       → `ROLES_ADMIN_OR_ABOVE` from `app.core.auth.roles` (was lazy
       import from facade inside `_check_ownership`).
  3. `app/shared/authz_helpers.py`
       → `CurrentUser` from `app.core.auth.models`,
       `assert_group_access` + `assert_resource_visible` from
       `app.core.auth.ownership`.

Why source-bound:
  Importing the modules at test time would still drag the facade via
  transitive imports (e.g. `auth.models` is OK, but loading `auth.py`
  itself runs `import app.core.auth.disclosure` etc.). The contract we
  want to lock is purely structural: NO `from app.core.auth.auth ...`
  ImportFrom node in the migrated files.

Stays source-bound to mirror `tests2/shared/test_architecture_legacy_patterns.py`
posture: zero app imports, zero side effects.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_APP = _BACKEND_ROOT / "app"


_MIGRATED_FILES = (
    _APP / "core" / "auth" / "auth_middleware.py",
    _APP / "core" / "auth" / "ownership.py",
    _APP / "shared" / "authz_helpers.py",
)

_FACADE_MODULE = "app.core.auth.auth"


def _imports_facade(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, names_imported)] for each `from app.core.auth.auth import ...`
    found anywhere in the file (top-level OR nested inside funcs).
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == _FACADE_MODULE:
                names = ", ".join(a.name for a in node.names)
                hits.append((node.lineno, names))
    return hits


@pytest.mark.parametrize(
    "path",
    _MIGRATED_FILES,
    ids=lambda p: str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"),
)
def test_migrated_file_does_not_import_from_facade(path: Path):
    """Each migrated file must import directly from leaf modules
    (`app.shared.jwt_token`, `app.core.auth.roles`, `app.core.auth.models`,
    `app.core.auth.ownership`) — NOT from the facade `app.core.auth.auth`."""
    assert path.exists(), f"migrated file missing: {path}"
    hits = _imports_facade(path)
    assert hits == [], (
        f"{path.name} regressed to facade-import:\n"
        + "\n".join(f"  line {ln}: from app.core.auth.auth import {nm}" for ln, nm in hits)
        + "\nUse the leaf module instead "
        "(jwt_token / roles / models / ownership / disclosure)."
    )


def test_auth_middleware_imports_decode_token_from_jwt_leaf():
    """Lock the exact leaf source: `decode_token` must come from
    `app.shared.jwt_token`, not the facade."""
    path = _APP / "core" / "auth" / "auth_middleware.py"
    text = path.read_text(encoding="utf-8")
    assert "from app.shared.jwt_token import decode_token" in text, (
        "auth_middleware.py must import decode_token from app.shared.jwt_token "
        "(strangler Phase-1)."
    )


def test_ownership_imports_roles_from_leaf_module():
    """Lock the eager top-level import of `ROLES_ADMIN_OR_ABOVE` from
    `app.core.auth.roles`. Pre-Phase-1 this was a lazy import from the
    facade inside `_check_ownership`."""
    path = _APP / "core" / "auth" / "ownership.py"
    text = path.read_text(encoding="utf-8")
    assert (
        "from app.core.auth.roles import ROLES_ADMIN_OR_ABOVE" in text
    ), (
        "ownership.py must import ROLES_ADMIN_OR_ABOVE from "
        "app.core.auth.roles (leaf module). The legacy lazy import "
        "from the facade has been removed."
    )
    # Defense in depth: lazy facade import body must be gone.
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_check_ownership":
            body_src = ast.unparse(node)
            assert (
                "from app.core.auth.auth import ROLES_ADMIN_OR_ABOVE" not in body_src
            ), (
                "_check_ownership must not contain the legacy lazy "
                "`from app.core.auth.auth import ROLES_ADMIN_OR_ABOVE` line."
            )


def test_authz_helpers_imports_models_and_ownership_leaves():
    """Lock the split: `CurrentUser` from `auth.models`, ownership
    helpers from `auth.ownership`. Pre-Phase-1 all 3 came from the
    facade in a single ImportFrom block."""
    path = _APP / "shared" / "authz_helpers.py"
    text = path.read_text(encoding="utf-8")
    assert "from app.core.auth.models import CurrentUser" in text
    assert "from app.core.auth.ownership import" in text
    assert "assert_group_access" in text
    assert "assert_resource_visible" in text
