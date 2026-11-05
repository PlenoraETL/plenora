# Plenora-ETL - single-server Docker installer for Windows.
#
# Scope:
#   - validates Windows host prerequisites;
#   - optionally installs Git and Docker Desktop through winget;
#   - creates .env.docker from .env.docker.example without overwriting secrets;
#   - builds and starts the single-server Docker Desktop compose stack;
#   - verifies Python DB providers, health, readiness, frontend, and worker ping.

[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$DryRun,
    [switch]$InstallDeps,
    [switch]$SkipSmoke,
    [switch]$SkipAdminBootstrap,
    [switch]$RequireSqlServerOdbc,
    [string]$FrontendApiUrl = $env:FRONTEND_VITE_API_URL,
    [string]$AdminUsername = $env:PLENORA_BOOTSTRAP_ADMIN_USERNAME,
    [string]$AdminPassword = $env:PLENORA_BOOTSTRAP_ADMIN_PASSWORD,
    [string]$AdminGroupId = $env:PLENORA_BOOTSTRAP_ADMIN_GROUP_ID
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($AdminUsername)) {
    $AdminUsername = $env:PLENORA_ADMIN_USERNAME
}
if ([string]::IsNullOrWhiteSpace($AdminPassword)) {
    $AdminPassword = $env:PLENORA_ADMIN_PASSWORD
}
if ([string]::IsNullOrWhiteSpace($AdminGroupId)) {
    $AdminGroupId = 'root'
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $line = "$File $($Arguments -join ' ')"
    Write-Host "+ $line"
    if ($DryRun) {
        return
    }

    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $line"
    }
}

function Invoke-HttpSmoke {
    param([string]$Uri)

    Write-Host "+ GET $Uri"
    if ($DryRun) {
        return
    }

    $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 30
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
        throw "HTTP smoke failed for ${Uri}: status $($response.StatusCode)"
    }
    Write-Host $response.Content
}

function Assert-RepoRoot {
    if (-not (Test-Path -LiteralPath "docker-compose.yml")) {
        throw "docker-compose.yml not found. Run from the repo root."
    }
    if (-not (Test-Path -LiteralPath ".env.docker.example")) {
        throw ".env.docker.example not found. Run from the repo root."
    }
    if (-not (Test-Path -LiteralPath "backend\Dockerfile")) {
        throw "backend\Dockerfile not found. Run from the repo root."
    }
}

function Install-HostDependencies {
    Write-Step "Install Windows host dependencies"

    if (-not (Test-Command "winget")) {
        throw "winget not found. Install Git and Docker Desktop manually, then rerun without -InstallDeps."
    }

    Invoke-External "winget" @("install", "--id", "Git.Git", "-e", "--accept-package-agreements", "--accept-source-agreements")
    Invoke-External "winget" @("install", "--id", "Docker.DockerDesktop", "-e", "--accept-package-agreements", "--accept-source-agreements")

    Write-Host "Docker Desktop may require a logout/reboot and Linux containers enabled before the next step." -ForegroundColor Yellow
}

function Preflight {
    Write-Step "Preflight"

    if (-not (Test-Command "docker")) {
        if ($DryRun) {
            Write-Host "+ docker not found, but dry-run continues"
            return
        }
        throw "docker not found. Install Docker Desktop or rerun with -InstallDeps."
    }

    Invoke-External "docker" @("--version")
    Invoke-External "docker" @("compose", "version")
}

function New-FernetKey {
    $bytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
}

function New-TokenUrlSafe {
    param([int]$Bytes = 48)
    $buffer = [byte[]]::new($Bytes)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    return [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Get-DockerEnvValue {
    param([string]$Key)

    if (-not (Test-Path -LiteralPath ".env.docker")) {
        return ""
    }

    foreach ($line in Get-Content -LiteralPath ".env.docker") {
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        if ($parts[0].Trim() -eq $Key) {
            return $parts[1].Trim()
        }
    }
    return ""
}

function Set-DockerEnvValue {
    param(
        [string]$Key,
        [string]$Value
    )

    Write-Host "+ set $Key in .env.docker"
    if ($DryRun) {
        return
    }

    $lines = @(Get-Content -LiteralPath ".env.docker")
    $updated = $false
    $out = foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if (-not $updated -and -not $trimmed.StartsWith("#") -and $trimmed.Contains("=")) {
            $parts = $trimmed.Split("=", 2)
            if ($parts[0].Trim() -eq $Key) {
                $updated = $true
                "$Key=$Value"
                continue
            }
        }
        $line
    }

    if (-not $updated) {
        $out += "$Key=$Value"
    }

    Set-Content -LiteralPath ".env.docker" -Value $out -Encoding ascii
}

function Ensure-DockerEnv {
    Write-Step "Create or refresh .env.docker"

    if (-not (Test-Path -LiteralPath ".env.docker")) {
        Write-Host "+ copy .env.docker.example .env.docker"
        if (-not $DryRun) {
            Copy-Item -LiteralPath ".env.docker.example" -Destination ".env.docker"
        }
    } else {
        Write-Host ".env.docker already exists; preserving existing non-empty values."
    }

    if ($DryRun) {
        Write-Host "+ generate PLENORA_ENCRYPTION_KEY if empty"
        Write-Host "+ generate JWT_SECRET_KEY if empty"
        Write-Host "+ generate PostgreSQL and RabbitMQ credentials if empty"
        return
    }

    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "PLENORA_ENCRYPTION_KEY"))) {
        Set-DockerEnvValue "PLENORA_ENCRYPTION_KEY" (New-FernetKey)
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "JWT_SECRET_KEY"))) {
        Set-DockerEnvValue "JWT_SECRET_KEY" (New-TokenUrlSafe)
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "POSTGRES_PASSWORD"))) {
        Set-DockerEnvValue "POSTGRES_PASSWORD" (New-TokenUrlSafe)
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "RABBITMQ_DEFAULT_USER"))) {
        Set-DockerEnvValue "RABBITMQ_DEFAULT_USER" "plenora"
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "RABBITMQ_DEFAULT_PASS"))) {
        Set-DockerEnvValue "RABBITMQ_DEFAULT_PASS" (New-TokenUrlSafe)
    }
    foreach ($credentialName in @("POSTGRES_PASSWORD", "RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS")) {
        $credentialValue = Get-DockerEnvValue $credentialName
        if ([string]::IsNullOrWhiteSpace($credentialValue) -or $credentialValue -notmatch '^[A-Za-z0-9_-]+$') {
            throw "$credentialName must contain only URL-safe characters: A-Z a-z 0-9 _ -."
        }
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "DATABASE_URL"))) {
        $pgPassword = Get-DockerEnvValue "POSTGRES_PASSWORD"
        Set-DockerEnvValue "DATABASE_URL" "postgresql://plenora:$pgPassword@postgres:5432/plenora"
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "DRAMATIQ_BROKER_URL"))) {
        $rabbitUser = Get-DockerEnvValue "RABBITMQ_DEFAULT_USER"
        $rabbitPassword = Get-DockerEnvValue "RABBITMQ_DEFAULT_PASS"
        Set-DockerEnvValue "DRAMATIQ_BROKER_URL" "amqp://$rabbitUser`:$rabbitPassword@rabbitmq:5672/"
    }
    if ((Get-DockerEnvValue "ENVIRONMENT") -eq "development") {
        Set-DockerEnvValue "ENVIRONMENT" "production"
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "S3_ACCESS_KEY"))) {
        Set-DockerEnvValue "S3_ACCESS_KEY" "plenora-storage"
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "S3_SECRET_KEY"))) {
        Set-DockerEnvValue "S3_SECRET_KEY" (New-TokenUrlSafe)
    }
    Set-DockerEnvValue "AWS_ACCESS_KEY_ID" (Get-DockerEnvValue "S3_ACCESS_KEY")
    Set-DockerEnvValue "AWS_SECRET_ACCESS_KEY" (Get-DockerEnvValue "S3_SECRET_KEY")
    Set-DockerEnvValue "S3_BUCKET" (Get-DockerEnvValue "OBJECT_STORAGE_BUCKET")

    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "PLENORA_ENCRYPTION_KEY"))) {
        throw "PLENORA_ENCRYPTION_KEY is still empty in .env.docker."
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "JWT_SECRET_KEY"))) {
        throw "JWT_SECRET_KEY is still empty in .env.docker."
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "POSTGRES_PASSWORD"))) {
        throw "POSTGRES_PASSWORD is still empty in .env.docker."
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "RABBITMQ_DEFAULT_PASS"))) {
        throw "RABBITMQ_DEFAULT_PASS is still empty in .env.docker."
    }
}

function Ensure-RabbitMqCredentials {
    Write-Step "RabbitMQ application user"
    if ($DryRun) {
        Write-Host "+ create/update the non-guest RabbitMQ user and permissions"
        return
    }

    $rabbitUser = Get-DockerEnvValue "RABBITMQ_DEFAULT_USER"
    $rabbitPassword = Get-DockerEnvValue "RABBITMQ_DEFAULT_PASS"
    $users = & docker compose exec -T rabbitmq rabbitmqctl list_users --silent
    if ($LASTEXITCODE -ne 0) { throw "Unable to list RabbitMQ users." }
    $userExists = @($users) | Where-Object {
        (($_ -split '\s+')[0]) -eq $rabbitUser
    }
    if ($userExists) {
        & docker compose exec -T rabbitmq rabbitmqctl change_password $rabbitUser $rabbitPassword | Out-Null
    } else {
        & docker compose exec -T rabbitmq rabbitmqctl add_user $rabbitUser $rabbitPassword | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw "Unable to create/update the RabbitMQ application user." }
    & docker compose exec -T rabbitmq rabbitmqctl set_permissions -p / $rabbitUser '.*' '.*' '.*' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to assign RabbitMQ permissions." }
    if ($rabbitUser -ne "guest") {
        & docker compose exec -T rabbitmq rabbitmqctl delete_user guest 2>$null | Out-Null
    }
}

function Ensure-PostgresCredentials {
    Write-Step "PostgreSQL application user"
    if ($DryRun) {
        Write-Host "+ reconcile the PostgreSQL application password"
        return
    }

    $postgresPassword = Get-DockerEnvValue "POSTGRES_PASSWORD"
    & docker compose exec -T -u postgres postgres psql `
        -U plenora -d plenora -v ON_ERROR_STOP=1 `
        "--command=ALTER ROLE plenora WITH PASSWORD '$postgresPassword';" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to reconcile the PostgreSQL application password."
    }
}

function Test-InteractiveInput {
    try {
        return (-not [Console]::IsInputRedirected)
    } catch {
        return $false
    }
}

function ConvertFrom-SecureStringPlainText {
    param([System.Security.SecureString]$Value)

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Collect-AdminBootstrapCredentials {
    Write-Step "Bootstrap superadmin"

    if ($SkipAdminBootstrap) {
        Write-Host "Superadmin bootstrap skipped."
        return
    }

    if ($DryRun) {
        Write-Host "+ prompt for superadmin username/password if not provided"
        Write-Host "+ seed or update superadmin after backend is healthy"
        return
    }

    $interactive = Test-InteractiveInput

    if ([string]::IsNullOrWhiteSpace($script:AdminUsername) -and $interactive) {
        $script:AdminUsername = Read-Host "Superadmin username [admin]"
        if ([string]::IsNullOrWhiteSpace($script:AdminUsername)) {
            $script:AdminUsername = "admin"
        }
    }

    if ([string]::IsNullOrWhiteSpace($script:AdminPassword) -and $interactive) {
        while ($true) {
            $p1 = Read-Host "Superadmin password (min 12 chars)" -AsSecureString
            $p2 = Read-Host "Confirm superadmin password" -AsSecureString
            $plain1 = ConvertFrom-SecureStringPlainText $p1
            $plain2 = ConvertFrom-SecureStringPlainText $p2

            if ($plain1 -ne $plain2) {
                Write-Host "Passwords do not match; try again." -ForegroundColor Yellow
                $plain1 = ""
                $plain2 = ""
                continue
            }
            if ($plain1.Length -lt 12) {
                Write-Host "Password must be at least 12 characters; try again." -ForegroundColor Yellow
                $plain1 = ""
                $plain2 = ""
                continue
            }

            $script:AdminPassword = $plain1
            $plain2 = ""
            break
        }
    }

    if ([string]::IsNullOrWhiteSpace($script:AdminUsername) -or [string]::IsNullOrWhiteSpace($script:AdminPassword)) {
        $script:SkipAdminBootstrap = $true
        Write-Host "Superadmin bootstrap skipped: credentials were not provided and stdin is not interactive." -ForegroundColor Yellow
        Write-Host "Set PLENORA_BOOTSTRAP_ADMIN_USERNAME and PLENORA_BOOTSTRAP_ADMIN_PASSWORD for non-interactive installs."
        return
    }

    if ($script:AdminPassword.Length -lt 12) {
        throw "superadmin password must be at least 12 characters."
    }
}

function Invoke-AdminBootstrap {
    if ($SkipAdminBootstrap) {
        return
    }

    Write-Step "Seed superadmin"
    Write-Host "+ docker compose exec -T -e PLENORA_BOOTSTRAP_ADMIN_USERNAME -e PLENORA_BOOTSTRAP_ADMIN_PASSWORD -e PLENORA_BOOTSTRAP_ADMIN_GROUP_ID backend python -c <bootstrap-script>"
    if ($DryRun) {
        return
    }

    $env:PLENORA_BOOTSTRAP_ADMIN_USERNAME = $AdminUsername
    $env:PLENORA_BOOTSTRAP_ADMIN_PASSWORD = $AdminPassword
    $env:PLENORA_BOOTSTRAP_ADMIN_GROUP_ID = $AdminGroupId

    $script = @'
import os, uuid
from app.core.auth import db
from app.core.security.password_hashing import hash_password

username = os.environ["PLENORA_BOOTSTRAP_ADMIN_USERNAME"]
password = os.environ["PLENORA_BOOTSTRAP_ADMIN_PASSWORD"]
group_id = os.environ.get("PLENORA_BOOTSTRAP_ADMIN_GROUP_ID") or "root"

if not username.strip():
    raise SystemExit("bootstrap superadmin username is empty")
if len(password) < 12:
    raise SystemExit("bootstrap superadmin password must be at least 12 characters")
if not group_id.strip():
    raise SystemExit("bootstrap superadmin group id is empty")

if db.get_group(group_id) is None:
    db.create_group(group_id, "Root", "Bootstrap superadmin group")

existing = db.get_user_by_username(username)
if existing is None:
    db.create_user(
        user_id=f"bootstrap-{uuid.uuid4()}",
        username=username,
        password_hash=hash_password(password),
        group_id=group_id,
        role="superadmin",
        email="admin@example.local",
    )
else:
    db.update_user(existing.id, is_active=True, role="superadmin", group_id=group_id)
    db.update_user_password(existing.id, hash_password(password))

print(f"Superadmin ready: username={username} role=superadmin group_id={group_id}")
'@

    try {
        Invoke-External "docker" @(
            "compose", "exec", "-T",
            "-e", "PLENORA_BOOTSTRAP_ADMIN_USERNAME",
            "-e", "PLENORA_BOOTSTRAP_ADMIN_PASSWORD",
            "-e", "PLENORA_BOOTSTRAP_ADMIN_GROUP_ID",
            "backend", "python", "-c", $script
        )
    } finally {
        [Environment]::SetEnvironmentVariable(
            "PLENORA_BOOTSTRAP_ADMIN_PASSWORD", $null, "Process"
        )
        $script:AdminPassword = ""
    }
}

function Invoke-ProviderCheck {
    Write-Step "Database provider check"

    $required = if ($RequireSqlServerOdbc) { "True" } else { "False" }
    $script = "import psycopg, pymysql, oracledb, pyodbc; drivers=pyodbc.drivers(); print('python db providers ok'); print('odbc_drivers=', drivers); required=$required; ok=any(d in drivers for d in ('ODBC Driver 18 for SQL Server','ODBC Driver 17 for SQL Server')); raise SystemExit(3 if required and not ok else 0)"
    Invoke-External "docker" @("compose", "exec", "-T", "backend", "python", "-c", $script)
}

function Invoke-SmokeChecks {
    Write-Step "Smoke checks"

    Invoke-HttpSmoke "http://127.0.0.1:5004/health"
    Invoke-HttpSmoke "http://127.0.0.1:5004/ready"
    Invoke-HttpSmoke "http://127.0.0.1:3000/"
    $pingScript = @'
import time, sys
from app.core.task_runtime.dispatch import dispatch_task_by_name
from app.core.orchestrator.task_result_store import default_task_result_store
handle = dispatch_task_by_name('cleanroom.ping')
store = default_task_result_store()
deadline = time.monotonic() + 30
state = 'PENDING'
while time.monotonic() < deadline:
    record = store.get(handle.id)
    if record is not None and record.state in ('SUCCESS', 'FAILURE'):
        state = record.state
        break
    time.sleep(0.5)
print(state)
sys.exit(0 if state == 'SUCCESS' else 1)
'@
    Invoke-External "docker" @("compose", "exec", "-T", "worker", "python", "-c", $pingScript)
}

Assert-RepoRoot

if ($InstallDeps) {
    Install-HostDependencies
}

Preflight

if ($Check) {
    Write-Step "Check complete"
    Write-Host "No files were written and no containers were started."
    exit 0
}

Ensure-DockerEnv
Collect-AdminBootstrapCredentials

# P31: stessa regola del frontend/Dockerfile, controllata subito (prima della build).
if (-not [string]::IsNullOrWhiteSpace($FrontendApiUrl) -and -not $FrontendApiUrl.StartsWith('/')) {
    throw "La CSP dell'immagine Docker ammette solo lo stesso origin (connect-src 'self'): FRONTEND_VITE_API_URL assoluto e Sentry nel frontend non sono supportati in Docker. Usa /api same-origin oppure l'installer Windows (FRONTEND_SENTRY_DSN nel backend/.env)."
}
if (-not [string]::IsNullOrWhiteSpace($env:FRONTEND_VITE_SENTRY_DSN)) {
    throw "La CSP dell'immagine Docker ammette solo lo stesso origin (connect-src 'self'): FRONTEND_VITE_API_URL assoluto e Sentry nel frontend non sono supportati in Docker. Usa /api same-origin oppure l'installer Windows (FRONTEND_SENTRY_DSN nel backend/.env)."
}
if (-not [string]::IsNullOrWhiteSpace($FrontendApiUrl)) {
    $env:FRONTEND_VITE_API_URL = $FrontendApiUrl
    Write-Host "Using FRONTEND_VITE_API_URL=$FrontendApiUrl"
}

Write-Step "Build and start single-server stack"
Invoke-External "docker" @("compose", "config")
Invoke-External "docker" @("compose", "build", "backend", "frontend")
Invoke-External "docker" @("compose", "up", "-d", "--wait", "postgres", "rabbitmq", "object-storage")
Ensure-PostgresCredentials
Ensure-RabbitMqCredentials
Invoke-External "docker" @("compose", "up", "-d", "--wait", "backend")
Invoke-External "docker" @("compose", "up", "-d", "worker", "worker-control", "beat")
Invoke-External "docker" @("compose", "up", "-d", "--wait", "frontend")

Invoke-AdminBootstrap
Invoke-ProviderCheck

if (-not $SkipSmoke) {
    Invoke-SmokeChecks
} else {
    Write-Step "Smoke checks skipped"
}

if ($DryRun) {
    Write-Step "Dry run complete"
    Write-Host "No files were written and no containers were started."
} else {
    Write-Step "Install complete"
    Write-Host "Stack is running. Use:"
    Write-Host "  docker compose ps"
    Write-Host "  docker compose logs --tail 200 backend worker worker-control beat frontend"
    Write-Host "  docker compose stop"
}
