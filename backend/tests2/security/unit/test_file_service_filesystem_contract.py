"""
Tests the file service filesystem behavior.

Storia tombstone:
- 2026-05-25 (Punto 3): `DefaultFileSystem.write_text` + `extract_zip` rimossi.
- 2026-05-26 (Punto 3 follow-up): `DefaultFileSystem.write_binary` rimosso.
- 2026-05-26 (audit follow-up): `DefaultFileSystem` + `FileSystemProtocol`
  rimossi interamente. Zero caller runtime dei metodi DI; il "filesystem
  capability" non e' piu' un protocol del file service.

I caller RAM-only navigano lo ZIP via
`open_zip_bytes_safe(data)` (`app.core.io.zip_guards`) che applica
`validate_zip_bomb_limits` prima del read. Per i flussi multi-file
(es. Shapefile sidecar), si usa `/vsizip//vsimem/...` via
`app.core.io.vsimem`.
"""

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def test_default_filesystem_protocol_tombstoned_post_audit_followup():
    """Sentinel: `DefaultFileSystem` + `FileSystemProtocol` non sono
    piu' esposti da `service_deps`. Sostituti canonical:
      - read bytes da object storage / DB: `app.core.io.object_storage`
      - ZIP RAM-only: `app.core.io.zip_guards.open_zip_bytes_safe`
      - tempdir VSI: `app.core.io.vsimem.vsi_temp_dir`."""
    from app.core.files import service_deps as deps

    for legacy in ("DefaultFileSystem", "FileSystemProtocol"):
        assert not hasattr(deps, legacy), (
            f"service_deps.{legacy} ancora presente: tombstone DI cleanup incompleto."
        )
