# =============================================================================
# Plenora-ETL - Validazione post-install Windows nativo
# =============================================================================
#
# Runner non distruttivo per una installazione nativa Windows gia' completata.
# Non riavvia servizi, non modifica .env, non cancella dati. Verifica:
#   - servizi Windows attesi;
#   - /ready attraverso l'URL operativo;
#   - frontend statico se Caddy e' installato;
#   - smoke round-trip dispatch -> RabbitMQ -> worker -> task_results su PG;
#   - opzionalmente la suite e2e-v2 isolata via scripts/run-e2e-windows.ps1.
#
# Uso tipico:
#   powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 -RunE2E
#   powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 -RunE2E -Integration
#
# Se il frontend/Caddy non e' installato, passare il backend diretto:
#   ... -ReadyUrl http://127.0.0.1:5004/ready -SkipFrontendPage
# =============================================================================

[CmdletBinding()]
param(
    [string]$ReadyUrl = 'http://127.0.0.1/ready',
    [string]$FrontendUrl = 'http://127.0.0.1/',
    [int]$ReadyTimeoutSeconds = 90,
    [string]$CondaEnv = 'plenora',
    [switch]$SkipSmoke,
    [switch]$SkipFrontendPage,
    [switch]$RequireFrontend,
    [switch]$RequireMinio,
    [switch]$RunE2E,
    [switch]$Integration,
    [switch]$SkipDeps,
    [string]$PytestArgs = ''
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'

function Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

function ConvertTo-CompactJson([object]$value) {
    try {
        return ($value | ConvertTo-Json -Compress -Depth 8)
    } catch {
        return "$value"
    }
}

function Get-CondaBat {
    $candidates = @(
        "$env:ProgramData\miniforge3\condabin\conda.bat",
        "$env:USERPROFILE\miniforge3\condabin\conda.bat",
        "$env:LOCALAPPDATA\miniforge3\condabin\conda.bat"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    Fail 'conda.bat non trovato (Miniforge). Eseguire prima scripts\install.ps1.'
}

function Test-RequiredServicePattern([string]$Pattern, [string]$Label) {
    $services = @(Get-Service -Name $Pattern -ErrorAction SilentlyContinue)
    if ($services.Count -eq 0) { Fail "Servizio richiesto non trovato: $Label ($Pattern)" }
    $running = @($services | Where-Object { $_.Status -eq 'Running' })
    if ($running.Count -eq 0) {
        $states = ($services | ForEach-Object { "$($_.Name)=$($_.Status)" }) -join ', '
        Fail "$Label non Running: $states"
    }
    Ok "$Label Running ($($running[0].Name))"
}

function Test-OptionalService([string]$Name, [string]$Label, [switch]$Required) {
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $service) {
        if ($Required) { Fail "Servizio richiesto non trovato: $Label ($Name)" }
        Warn "$Label non installato ($Name): controllo saltato"
        return $false
    }
    if ($service.Status -ne 'Running') { Fail "$Label non Running: $($service.Status)" }
    Ok "$Label Running ($Name)"
    return $true
}

function Wait-Ready([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = ''
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $Url -TimeoutSec 10
            if ($response.status -eq 'ready') {
                Ok "/ready verde: $Url"
                Write-Host "  $(ConvertTo-CompactJson $response)"
                return
            }
            $lastError = "status=$($response.status); body=$(ConvertTo-CompactJson $response)"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    Fail "/ready non ready entro ${TimeoutSeconds}s su $Url. Ultimo errore: $lastError"
}

function Test-FrontendPage([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 10
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
            Fail "frontend ha risposto HTTP $($response.StatusCode) su $Url"
        }
        Ok "frontend risponde: $Url"
    } catch {
        Fail "frontend non raggiungibile su ${Url}: $($_.Exception.Message)"
    }
}

# -----------------------------------------------------------------------------
Step 'Preflight repo installato'
# -----------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $BackendDir 'requirements.txt'))) {
    Fail "Repo non trovato: eseguire dalla root del repo Plenora (manca backend\requirements.txt)"
}
if (-not (Test-Path (Join-Path $BackendDir '.env'))) {
    Fail "backend\.env non trovato: eseguire prima l'installer Windows"
}
Ok "Repo: $RepoRoot"

# -----------------------------------------------------------------------------
Step 'Servizi Windows'
# -----------------------------------------------------------------------------
Test-RequiredServicePattern 'postgresql-x64-*' 'PostgreSQL'
Test-RequiredServicePattern 'RabbitMQ' 'RabbitMQ'
Test-RequiredServicePattern 'plenora-backend' 'Plenora backend'
Test-RequiredServicePattern 'plenora-worker*' 'Plenora worker'
Test-RequiredServicePattern 'plenora-beat' 'Plenora beat'
$minioPresent = Test-OptionalService 'plenora-minio' 'Plenora MinIO' -Required:$RequireMinio
$caddyPresent = Test-OptionalService 'plenora-caddy' 'Plenora Caddy' -Required:$RequireFrontend

# -----------------------------------------------------------------------------
Step 'Readiness applicativa'
# -----------------------------------------------------------------------------
Wait-Ready -Url $ReadyUrl -TimeoutSeconds $ReadyTimeoutSeconds

if (-not $SkipFrontendPage) {
    if ($caddyPresent -or $RequireFrontend) {
        Step 'Frontend statico'
        Test-FrontendPage -Url $FrontendUrl
    } else {
        Warn 'frontend page check saltato: plenora-caddy non installato'
    }
}

# -----------------------------------------------------------------------------
Step 'Smoke round-trip nativo'
# -----------------------------------------------------------------------------
if ($SkipSmoke) {
    Warn 'smoke saltato (-SkipSmoke)'
} else {
    $smoke = Join-Path $BackendDir 'smoke_native.py'
    if (-not (Test-Path $smoke)) {
        Fail "smoke_native.py non trovato in ${BackendDir}: e' generato dall'installer, rilanciare scripts\install.ps1"
    }
    $condaBat = Get-CondaBat
    & $condaBat run -n $CondaEnv --cwd $BackendDir python smoke_native.py
    if ($LASTEXITCODE -ne 0) { Fail "smoke_native.py exit=$LASTEXITCODE" }
    Ok 'SMOKE: SUCCESS'
}

# -----------------------------------------------------------------------------
Step 'Suite e2e-v2 isolata'
# -----------------------------------------------------------------------------
if ($RunE2E) {
    $runner = Join-Path $PSScriptRoot 'run-e2e-windows.ps1'
    if (-not (Test-Path $runner)) { Fail "runner e2e-v2 non trovato: $runner" }
    $powershellExe = Join-Path $PSHOME 'powershell.exe'
    if (-not (Test-Path $powershellExe)) { $powershellExe = 'powershell' }

    $runnerArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner)
    if ($Integration) { $runnerArgs += '-Integration' }
    if ($SkipDeps) { $runnerArgs += '-SkipDeps' }
    if ($PytestArgs) { $runnerArgs += @('-PytestArgs', $PytestArgs) }

    & $powershellExe @runnerArgs
    if ($LASTEXITCODE -ne 0) { Fail "run-e2e-windows.ps1 exit=$LASTEXITCODE" }
    Ok 'e2e-v2 isolati verdi'
} else {
    Ok 'e2e-v2 isolati saltati (-RunE2E per abilitarli)'
}

Write-Host ''
Ok 'Validazione Windows post-install completata'
