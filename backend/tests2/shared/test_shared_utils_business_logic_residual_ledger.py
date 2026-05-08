"""BE097 C2 ledger for business logic found in app.shared/app.utils."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_ALLOWED_STATUSES = frozenset(
    {
        "DONE",
        "REMOVED",
        "KEEP_PUBLIC_COMPAT",
        "KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        "BLOCKER",
    }
)


@dataclass(frozen=True)
class SharedUtilsBusinessLogicEntry:
    path: str
    symbol: str
    status: str
    owner: str
    removal_condition: str


_LEDGER: tuple[SharedUtilsBusinessLogicEntry, ...] = (
    SharedUtilsBusinessLogicEntry(
        path="app/core/arricchisci/response_parser.py",
        symbol="ResponseParser",
        status="DONE",
        owner="TRANSFORMATION_ENGINE",
        removal_condition="Arricchisci API-generator parser now has a domain-owned canonical module.",
    ),
    SharedUtilsBusinessLogicEntry(
        path="app/utils/response_parser.py",
        symbol="module",
        status="KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        owner="TRANSFORMATION_ENGINE",
        removal_condition=(
            "Delete after external imports switch to app.core.arricchisci.response_parser."
        ),
    ),
    SharedUtilsBusinessLogicEntry(
        path="app/utils/template_variables.py",
        symbol="process_static_fields",
        status="KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        owner="TRANSFORMATION_ENGINE",
        removal_condition=(
            "Move after Arricchisci, Manipola, and artifacts source-loader agree on "
            "one typed static-parameter contract."
        ),
    ),
    SharedUtilsBusinessLogicEntry(
        path="app/utils/geo.py",
        symbol="get_utm_crs_for_geometry",
        status="KEEP_COMPAT_WITH_REMOVAL_CONDITION",
        owner="TRANSFORMATION_ENGINE",
        removal_condition=(
            "Move after Estrai, Manipola, and dataframe IO share a typed geospatial "
            "conversion/CRS contract."
        ),
    ),
    SharedUtilsBusinessLogicEntry(
        path="app/core/auth/visibility.py",
        symbol="visible_resource_ids",
        status="DONE",
        owner="AUTH_SECURITY_ENGINE",
        removal_condition=(
            "Auth visibility policy owns visible-resource checks for shareable resources."
        ),
    ),
    SharedUtilsBusinessLogicEntry(
        path="app/core/auth/shares.py",
        symbol="list_resource_shares",
        status="DONE",
        owner="AUTH_SECURITY_ENGINE",
        removal_condition=(
            "Auth share CRUD owns cross-group sharing row persistence."
        ),
    ),
    SharedUtilsBusinessLogicEntry(
        path="app/shared/sharing.py",
        symbol="module",
        status="KEEP_PUBLIC_COMPAT",
        owner="AUTH_SECURITY_ENGINE",
        removal_condition=(
            "External compatibility imports should switch to app.core.auth.visibility "
            "or app.core.auth.shares."
        ),
    ),
)


def test_c2_shared_utils_business_logic_ledger_uses_only_1_0_statuses() -> None:
    statuses = {entry.status for entry in _LEDGER}
    assert statuses <= _ALLOWED_STATUSES
    assert "MIGRATE_NEXT" not in statuses
    assert "KEEP_COMPAT" not in statuses


def test_c2_shared_utils_business_logic_entries_have_removal_conditions() -> None:
    for entry in _LEDGER:
        assert entry.owner
        assert entry.removal_condition
        if entry.status == "KEEP_COMPAT_WITH_REMOVAL_CONDITION":
            assert "after" in entry.removal_condition.lower()


def test_c2_shared_utils_business_logic_ledger_points_at_existing_files() -> None:
    for entry in _LEDGER:
        assert (_BACKEND_ROOT / entry.path).exists(), entry.path


def test_visibility_policy_runtime_imports_are_auth_owned() -> None:
    offenders: list[str] = []
    for path in (_BACKEND_ROOT / "app").rglob("*.py"):
        if path.parts[-3:] == ("app", "shared", "sharing.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if "app.shared.sharing" not in text:
            continue
        for symbol in (
            "is_resource_visible",
            "is_shareable",
            "visible_resource_ids",
        ):
            if symbol in text:
                offenders.append(str(path.relative_to(_BACKEND_ROOT)))
                break

    assert offenders == []


def test_production_runtime_does_not_import_shared_sharing() -> None:
    offenders: list[str] = []
    for path in (_BACKEND_ROOT / "app").rglob("*.py"):
        if path.parts[-3:] == ("app", "shared", "sharing.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if "app.shared.sharing" in text:
            offenders.append(path.relative_to(_BACKEND_ROOT).as_posix())

    assert offenders == []
