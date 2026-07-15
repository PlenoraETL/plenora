"""Security and fail-fast locks for the native Windows PostgreSQL bootstrap."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "scripts" / "install-native.ps1"
VALIDATOR = ROOT / "scripts" / "validate-windows-install.ps1"


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


def test_native_install_enables_runtime_heartbeat_and_lease_checks() -> None:
    installer = _installer()

    assert "'READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS' -Value '180'" in installer
    assert "'READINESS_WORKER_HEARTBEAT_MAX_AGE_SECONDS' -Value '45'" in installer
    assert "'WORKER_HEARTBEAT_INTERVAL_SECONDS' -Value '10'" in installer
    assert "'TASK_EXECUTION_LEASE_SECONDS' -Value '45'" in installer


def test_windows_validator_rejects_disabled_beat_heartbeat() -> None:
    validator = VALIDATOR.read_text(encoding="utf-8-sig")

    assert "beat heartbeat disabilitato" in validator
    assert "^skipped:\\s*disabled" in validator


def test_native_installer_provisions_modern_sql_server_odbc_driver() -> None:
    installer = _installer()

    assert "ODBC Driver 18 for SQL Server" in installer
    assert "https://go.microsoft.com/fwlink/?linkid=2358430" in installer
    assert "IACCEPTMSODBCSQLLICENSETERMS=YES" in installer
    assert "Get-OdbcDriver -Platform '64-bit'" in installer


def test_dependency_update_stops_services_once_and_cleans_pip_residuals() -> None:
    installer = _installer()

    stop_call = "$stoppedAppServiceIds = @(Stop-PlenoraAppServicesForDependencyUpdate)"
    pip_call = "python -m pip install --require-hashes -r $requirementsLock"
    assert stop_call in installer
    assert installer.index(stop_call) < installer.index(pip_call)
    assert "$orderedIds | Select-Object -Unique" in installer
    assert "Remove-StalePipDirectories -CondaEnvRoot" in installer
    assert "-Directory -Filter '~*'" in installer
    assert "Start-PlenoraAppServicesAfterDependencyFailure" in installer
