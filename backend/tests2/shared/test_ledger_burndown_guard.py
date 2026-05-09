"""Batch 106 Wave 1 ledger burn-down guard.

These ceilings are intentionally non-exact: removing rows is allowed without
editing this guard, but adding active residuals or more REMOVED tombstones must
be an explicit batch decision.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable

import pytest

from tests2.shared import test_core_db_typed_boundary_residual_ledger as core_db_ledger
from tests2.shared import test_shared_utils_business_logic_residual_ledger as shared_utils_ledger


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


@dataclass(frozen=True)
class BurnDownTarget:
    name: str
    entries: tuple[object, ...]
    active_ceilings: dict[str, int]
    removed_ceilings: dict[str, int]
    symbol_exists: Callable[[object], bool] | None = None


def _ledger_domain(entry: object) -> str:
    path = getattr(entry, "path")
    parts = path.split("/")
    if len(parts) >= 3:
        return "/".join(parts[:3])
    return path


def _status_counts(entries: Iterable[object], status: str) -> Counter[str]:
    return Counter(
        _ledger_domain(entry)
        for entry in entries
        if getattr(entry, "status") == status
    )


def _active_counts(entries: Iterable[object]) -> Counter[str]:
    return Counter(
        _ledger_domain(entry)
        for entry in entries
        if getattr(entry, "status") not in {"DONE", "REMOVED"}
    )


_BURN_DOWN_TARGETS: tuple[BurnDownTarget, ...] = (
    BurnDownTarget(
        name="core_db_typed_boundary",
        entries=core_db_ledger._LEDGER,
        active_ceilings={
            "app/core/admin_system_status": 3,
            "app/core/arricchisci": 1,
            "app/core/auth": 3,
            "app/core/connections": 8,
            "app/core/datasources": 4,
            "app/core/eseguibili": 6,
            "app/core/features": 3,
            "app/core/orchestrator": 14,
            "app/core/projects": 7,
            "app/core/results": 7,
            "app/core/scheduler": 3,
            "workers/tasks/carica_task.py": 1,
        },
        removed_ceilings={
            "app/core/orchestrator": 1,
        },
        symbol_exists=core_db_ledger._symbol_exists,
    ),
    BurnDownTarget(
        name="shared_utils_business_logic",
        entries=shared_utils_ledger._LEDGER,
        active_ceilings={
            "app/core/arricchisci": 1,
            "app/core/auth": 2,
            "app/utils/geo.py": 1,
        },
        removed_ceilings={
            "app/shared/sharing.py": 1,
        },
    ),
)


@pytest.mark.parametrize("target", _BURN_DOWN_TARGETS, ids=lambda target: target.name)
def test_batch_106_active_ledger_residual_counts_do_not_increase(
    target: BurnDownTarget,
) -> None:
    counts = _active_counts(target.entries)
    increases = {
        domain: count
        for domain, count in sorted(counts.items())
        if count > target.active_ceilings.get(domain, 0)
    }

    assert not increases, (
        f"{target.name} active ledger residuals increased. Trim the residual, "
        "or update this burn-down guard only with an explicit batch decision. "
        f"Current counts: {dict(sorted(counts.items()))}; "
        f"ceilings: {target.active_ceilings}"
    )


@pytest.mark.parametrize("target", _BURN_DOWN_TARGETS, ids=lambda target: target.name)
def test_batch_106_removed_ledger_tombstones_do_not_accumulate(
    target: BurnDownTarget,
) -> None:
    counts = _status_counts(target.entries, "REMOVED")
    increases = {
        domain: count
        for domain, count in sorted(counts.items())
        if count > target.removed_ceilings.get(domain, 0)
    }

    assert not increases, (
        f"{target.name} REMOVED ledger tombstones increased. Delete stale "
        "REMOVED rows instead of accumulating tombstones. "
        f"Current counts: {dict(sorted(counts.items()))}; "
        f"ceilings: {target.removed_ceilings}"
    )


@pytest.mark.parametrize("target", _BURN_DOWN_TARGETS, ids=lambda target: target.name)
def test_batch_106_removed_ledger_symbols_stay_absent(
    target: BurnDownTarget,
) -> None:
    if target.symbol_exists is None:
        return

    reintroduced = [
        entry
        for entry in target.entries
        if getattr(entry, "status") == "REMOVED" and target.symbol_exists(entry)
    ]

    assert not reintroduced, (
        f"{target.name} REMOVED ledger entries point at live symbols again. "
        f"Reintroduced symbols: {reintroduced}"
    )
