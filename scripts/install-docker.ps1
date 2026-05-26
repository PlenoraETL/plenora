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
    [switch]$RequireSqlServerOdbc,
    [string]$FrontendApiUrl = $env:FRONTEND_VITE_API_URL
)

$ErrorActionPreference = 'Stop'

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
        return
    }

    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "PLENORA_ENCRYPTION_KEY"))) {
        Set-DockerEnvValue "PLENORA_ENCRYPTION_KEY" (New-FernetKey)
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "JWT_SECRET_KEY"))) {
        Set-DockerEnvValue "JWT_SECRET_KEY" (New-TokenUrlSafe)
    }

    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "PLENORA_ENCRYPTION_KEY"))) {
        throw "PLENORA_ENCRYPTION_KEY is still empty in .env.docker."
    }
    if ([string]::IsNullOrWhiteSpace((Get-DockerEnvValue "JWT_SECRET_KEY"))) {
        throw "JWT_SECRET_KEY is still empty in .env.docker."
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
    Invoke-External "docker" @("compose", "exec", "-T", "worker", "python", "-c", "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))")
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

if (-not [string]::IsNullOrWhiteSpace($FrontendApiUrl)) {
    $env:FRONTEND_VITE_API_URL = $FrontendApiUrl
    Write-Host "Using FRONTEND_VITE_API_URL=$FrontendApiUrl"
}

Write-Step "Build and start single-server stack"
Invoke-External "docker" @("compose", "config")
Invoke-External "docker" @("compose", "build", "backend", "frontend")
Invoke-External "docker" @("compose", "up", "-d", "postgres", "redis")
Invoke-External "docker" @("compose", "up", "-d", "--wait", "backend")
Invoke-External "docker" @("compose", "up", "-d", "worker", "beat")
Invoke-External "docker" @("compose", "up", "-d", "--wait", "frontend")

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
    Write-Host "  docker compose logs --tail 200 backend worker beat frontend"
    Write-Host "  docker compose stop"
}
