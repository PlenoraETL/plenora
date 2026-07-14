"""Security and fail-fast locks for the native Windows PostgreSQL bootstrap."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "scripts" / "install-native.ps1"


def _installer() -> str:
    return INSTALLER.read_text(encoding="utf-8-sig")


def test_postgres_commands_never_fall_back_to_interactive_psql_prompts() -> None:
    installer = _installer()

    assert "& $psql -w -U postgres" in installer
    assert "Autenticazione postgres fallita" in installer
    assert "$LASTEXITCODE -ne 0 -or $authResult -ne '1'" in installer


def test_application_database_password_is_not_the_legacy_default() -> None:
    installer = _installer()

    assert "CREATE USER plenora WITH PASSWORD 'plenora'" not in installer
    assert "DATABASE_URL=postgresql://plenora:plenora@" not in installer
    assert "$dbPassword = New-RandomSecret" in installer
    assert 'Set-EnvValue -File $envFile -Key \'DATABASE_URL\'' in installer


def test_installer_rotates_edb_default_superuser_password() -> None:
    installer = _installer()

    assert "if ($PgSuperPassword -eq 'postgres')" in installer
    assert "ALTER ROLE postgres WITH PASSWORD" in installer
    assert "ZeroFreeBSTR" in installer
    assert "RandomNumberGenerator]::Create()" in installer


def test_rabbitmq_install_waits_for_service_with_a_bounded_deadline() -> None:
    installer = _installer()

    assert "Start-Process $exe -ArgumentList '/S' -Wait" not in installer
    assert "Start-Process $exe -ArgumentList '/S' -PassThru" in installer
    assert "$rabbitDeadline = (Get-Date).AddMinutes(5)" in installer
    assert "Get-Service -Name 'RabbitMQ'" in installer


def test_frontend_build_does_not_depend_on_refreshed_machine_path() -> None:
    installer = _installer()

    assert "Join-Path $env:ProgramFiles 'nodejs\\npm.cmd'" in installer
    assert '$env:PATH = "$nodeDir;$originalPath"' in installer
    assert "& $npm ci" in installer
    assert "& $npm run build" in installer
    assert "& npm ci" not in installer
