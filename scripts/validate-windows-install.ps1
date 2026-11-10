# =============================================================================
# Plenora-ETL - Validazione post-install Windows nativo
# =============================================================================
#
# Runner non distruttivo per una installazione nativa Windows gia' completata.
# Non riavvia servizi, non modifica .env, non cancella dati. Verifica:
#   - servizi Windows attesi;
#   - /ready attraverso l'URL operativo;
#   - frontend statico se Caddy e' installato;
#   - Grafana (se installato): solo su 127.0.0.1:3300, /grafana senza
#     sessione Plenora = 401, pagine Plenora ancora con le loro intestazioni;
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
    [switch]$RequireObjectStorage = $true,
    # Grafana opzionale (-InstallGrafana): controllato se installato,
    # obbligatorio con -RequireGrafana.
    [switch]$RequireGrafana,
    # Alias deprecato per vecchi runbook.
    [switch]$RequireMinio,
    [switch]$RunE2E,
    [switch]$Integration,
    [switch]$SkipDeps,
    [string]$PytestArgs = ''
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
if ($RequireMinio) { $RequireObjectStorage = $true }

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
                $beatDetail = [string]$response.checks.beat_heartbeat.detail
                if ($beatDetail -match '^skipped:\s*disabled') {
                    $lastError = "beat heartbeat disabilitato: $beatDetail"
                    Start-Sleep -Seconds 2
                    continue
                }
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
        # P31: intestazioni di sicurezza servite da Caddy (security-headers.caddy
        # generato dall'installer). Report-Only solo se FRONTEND_CSP_REPORT_ONLY.
        $csp = $response.Headers['Content-Security-Policy']
        if (-not $csp) { $csp = $response.Headers['Content-Security-Policy-Report-Only'] }
        if (-not $csp -or $csp -notmatch "default-src 'self'" -or $csp -notmatch "frame-ancestors 'none'" -or
            $csp -match 'unsafe-inline|unsafe-eval') {
            Fail "frontend senza Content-Security-Policy attesa su ${Url}: '$csp'"
        }
        if ($response.Headers['X-Content-Type-Options'] -ne 'nosniff') {
            Fail "frontend senza X-Content-Type-Options: nosniff su $Url"
        }
        Ok "intestazioni di sicurezza presenti (CSP: $csp)"
    } catch {
        Fail "frontend non raggiungibile su ${Url}: $($_.Exception.Message)"
    }
}

function Test-GrafanaProxy([string]$BaseUrl) {
    # Grafana non deve essere raggiungibile se non da Caddy: ascolto solo loopback.
    # Socket in ascolto = porta remota 0 (alcune build di Windows riportano
    # State vuoto anche per i listener: non filtrare su -State Listen).
    $listen = @(Get-NetTCPConnection -LocalPort 3300 -ErrorAction SilentlyContinue |
        Where-Object { $_.RemotePort -eq 0 })
    if ($listen.Count -eq 0) { Fail 'Grafana non in ascolto su 3300' }
    $exposed = @($listen | Where-Object { $_.LocalAddress -notin @('127.0.0.1', '::1') })
    if ($exposed.Count -gt 0) { Fail "Grafana in ascolto anche su $($exposed[0].LocalAddress):3300 (atteso solo 127.0.0.1)" }
    Ok 'Grafana in ascolto solo su 127.0.0.1:3300'

    # Senza sessione Plenora (e con un'identita' finta) /grafana deve dire 401:
    # Caddy toglie X-WEBAUTH-* e forward_auth chiede al backend.
    $url = $BaseUrl.TrimEnd('/') + '/grafana/'
    $status = 0
    $headers = $null
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 10 -MaximumRedirection 0 `
            -Headers @{ 'X-WEBAUTH-USER' = 'validate-windows-install'; 'X-WEBAUTH-ROLE' = 'Admin' }
        $status = [int]$response.StatusCode
    } catch {
        if (-not $_.Exception.Response) { Fail "/grafana non raggiungibile su ${url}: $($_.Exception.Message)" }
        $status = [int]$_.Exception.Response.StatusCode
        $headers = $_.Exception.Response.Headers
    }
    if ($status -ne 401) { Fail "/grafana senza sessione Plenora ha risposto HTTP $status (atteso 401) su $url" }
    $csp = if ($headers) { [string]$headers['Content-Security-Policy'] } else { '' }
    # CSP propria del ramo /grafana (frontend_headers.GRAFANA_CSP): incorporabile
    # solo da Plenora e nessun caricamento fuori origine (lasciapassare).
    foreach ($directive in @("frame-ancestors 'self'", "default-src 'self'", "img-src 'self' data: blob:", "connect-src 'self'")) {
        if ($csp -notlike "*$directive*") { Fail "/grafana senza le intestazioni proprie (CSP senza '$directive': '$csp')" }
    }
    if ($headers['X-Frame-Options']) { Fail "/grafana con X-Frame-Options ($($headers['X-Frame-Options'])): non incorporabile" }
    if ($headers['Referrer-Policy'] -ne 'no-referrer') { Fail '/grafana senza Referrer-Policy: no-referrer' }
    Ok "/grafana senza sessione Plenora: 401 con intestazioni proprie ($url)"

    # Il confine del blocco: /grafana-* e' ancora Plenora (SPA) con le sue intestazioni.
    Test-FrontendPage -Url ($BaseUrl.TrimEnd('/') + '/grafana-confine')
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
$storagePresent = Test-OptionalService 'plenora-object-storage' 'Plenora Object Storage' -Required:$RequireObjectStorage
$caddyPresent = Test-OptionalService 'plenora-caddy' 'Plenora Caddy' -Required:$RequireFrontend
$grafanaPresent = Test-OptionalService 'plenora-grafana' 'Plenora Grafana' -Required:$RequireGrafana

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

if ($grafanaPresent) {
    Step 'Grafana dietro Caddy (/grafana)'
    if (-not $caddyPresent) { Fail 'plenora-grafana installato senza plenora-caddy: /grafana non e'' pubblicato' }
    Test-GrafanaProxy -BaseUrl $FrontendUrl
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
