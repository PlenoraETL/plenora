"""
Tests the file service filesystem behavior.

Post Punto 3 cleanup (2026-05-25): `DefaultFileSystem.extract_zip`
rimosso (zero caller runtime). I caller RAM-only navigano lo ZIP via
`zipfile.ZipFile(BytesIO(...))` + `validate_zip_bomb_limits` +
`z.read(name)` su bytes. Per i flussi che richiedono multi-file
formats (es. Shapefile), si usa `/vsizip//vsimem/...` via
`app.core.io.vsimem`.
"""

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def test_default_filesystem_extract_zip_tombstoned_post_punto3():
    """Sentinel: `DefaultFileSystem.extract_zip` non e' piu' esposto.
    Sostituto canonical: `zipfile.ZipFile(BytesIO(...))` +
    `validate_zip_bomb_limits` + `z.read(name)` su bytes."""
    from app.core.files.service_deps import DefaultFileSystem, FileSystemProtocol

    assert not hasattr(DefaultFileSystem, "extract_zip"), (
        "DefaultFileSystem.extract_zip ancora presente: cleanup Punto 3 incompleto."
    )
    assert not hasattr(FileSystemProtocol, "extract_zip"), (
        "FileSystemProtocol.extract_zip ancora presente: sincronizzare il protocol."
    )
