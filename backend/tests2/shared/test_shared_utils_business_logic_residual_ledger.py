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
        path="app/core/manipola/transforms/spatial_helpers.py",
        symbol="get_utm_crs_for_geometry",
        status="DONE",
        owner="TRANSFORMATION_ENGINE",
        removal_condition=(
            "Manipola spatial transforms own metric CRS selection for spatial operations."
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
        status="REMOVED",
        owner="AUTH_SECURITY_ENGINE",
        removal_condition=(
            "Removed compatibility re-export; callers must use app.core.auth.visibility "
            "or app.core.auth.shares directly."
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
        path = _BACKEND_ROOT / entry.path
        if entry.status == "REMOVED":
            assert not path.exists(), entry.path
            continue
        assert path.exists(), entry.path


def test_static_parameter_processing_is_not_template_variables_business_logic() -> None:
    template_variables = _BACKEND_ROOT / "app" / "utils" / "template_variables.py"
    text = template_variables.read_text(encoding="utf-8")

    assert "def process_static_fields" not in text

    offenders: list[str] = []
    for path in (_BACKEND_ROOT / "app").rglob("*.py"):
        module_text = path.read_text(encoding="utf-8")
        if "app.utils.template_variables import process_static_fields" in module_text:
            offenders.append(path.relative_to(_BACKEND_ROOT).as_posix())

    assert offenders == []


def test_utm_crs_selection_is_not_owned_by_app_utils_geo() -> None:
    geo_utils = (_BACKEND_ROOT / "app" / "utils" / "geo.py").read_text(
        encoding="utf-8-sig"
    )

    assert "def get_utm_crs_for_geometry" not in geo_utils


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


def test_shared_sharing_compat_module_is_removed() -> None:
    assert not (_BACKEND_ROOT / "app" / "shared" / "sharing.py").exists()


def test_production_runtime_does_not_import_shared_sharing() -> None:
    offenders: list[str] = []
    for path in (_BACKEND_ROOT / "app").rglob("*.py"):
        if path.parts[-3:] == ("app", "shared", "sharing.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if "app.shared.sharing" in text:
            offenders.append(path.relative_to(_BACKEND_ROOT).as_posix())

    assert offenders == []
