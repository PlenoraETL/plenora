"""Contratto di installazione dell'acceleratore XLSX su Windows."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "scripts" / "install-native.ps1"


def _installer() -> str:
    return INSTALLER.read_text(encoding="utf-8-sig")


def test_installer_auto_detects_or_accepts_an_explicit_exporter_archive() -> None:
    installer = _installer()

    assert "[string]$XlsxExporterArchive = ''" in installer
    assert "[string]$XlsxExporterChecksum = ''" in installer
    assert "PLENORA_INSTALL_XLSX_EXPORTER_ARCHIVE" in installer
    assert "PLENORA_INSTALL_XLSX_EXPORTER_CHECKSUM" in installer
    assert "@('C:\\deploy', $RepoRoot)" in installer
    assert "plenora-xlsx-exporter-*-windows-x64.zip" in installer


def test_installer_verifies_package_before_publishing_the_executable() -> None:
    installer = _installer()

    checksum = "Get-FileHash -LiteralPath $archive -Algorithm SHA256"
    expand = "Expand-Archive -LiteralPath $archive"
    publish = "Copy-Item -LiteralPath $candidate -Destination $target -Force"
    assert checksum in installer
    assert "[0-9a-f]{64}" in installer
    assert "self-test --output $selfTestFile" in installer
    assert installer.index(checksum) < installer.index(expand) < installer.index(publish)


def test_installer_has_a_fast_exporter_only_deploy_with_safe_fallbacks() -> None:
    installer = _installer()

    assert "[switch]$SkipXlsxExporter" in installer
    assert "[switch]$XlsxExporterOnly" in installer
    assert "bin\\plenora-xlsx-exporter.exe" in installer
    assert "plenora-xlsx-exporter.previous.exe" in installer
    assert "writer Python attivo" in installer
    assert "servizi precedentemente attivi riavviati" in installer
