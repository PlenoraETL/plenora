"""
Contract test per la redazione di `DiskStatus.path` e `DiskStatus.usage_path`.

Bug coperto (plenora-bug-admin-api P1):
  `host_resources.disk` nel payload di `GET /admin/system-status`
  esponeva `disk.path` (DATA_DIR, es. `/var/plenora/data`) e
  `disk.usage_path` (filesystem montato, es. `/var`). Anche con
  `require_superadmin`, queste informazioni rivelano la struttura del
  filesystem del server e facilitano lateral movement post-compromise.

Post-fix: `Field(exclude=True)` su entrambi i campi in `DiskStatus`. Il
model resta completo internamente per logging/troubleshooting, ma
`model_dump()` non li include nel payload API.
"""

from __future__ import annotations

import pytest

from app.core.admin_system_status.models import DiskStatus


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


class TestDiskStatusPathRedacted:
    def _make(self) -> DiskStatus:
        return DiskStatus(
            path="/var/plenora/data",
            usage_path="/var",
            total_gb=100.0,
            used_gb=50.0,
            free_gb=50.0,
            percent=50.0,
        )

    def test_model_dump_excludes_path(self):
        disk = self._make()
        dumped = disk.model_dump()
        assert "path" not in dumped, (
            "DiskStatus.path NON deve essere nel payload API: rivela "
            "la struttura del filesystem server."
        )

    def test_model_dump_excludes_usage_path(self):
        disk = self._make()
        dumped = disk.model_dump()
        assert "usage_path" not in dumped, (
            "DiskStatus.usage_path NON deve essere nel payload API."
        )

    def test_safe_fields_still_in_dump(self):
        """Sanity: i campi non-sensitive devono restare presenti."""
        disk = self._make()
        dumped = disk.model_dump()
        assert dumped["total_gb"] == 100.0
        assert dumped["used_gb"] == 50.0
        assert dumped["free_gb"] == 50.0
        assert dumped["percent"] == 50.0

    def test_internal_access_still_works(self):
        """`path` e `usage_path` restano disponibili come attributi
        Python per uso interno (logging, troubleshooting). Solo la
        serializzazione API e' redatta.
        """
        disk = self._make()
        assert disk.path == "/var/plenora/data"
        assert disk.usage_path == "/var"
