# =============================================================================
# Plenora-ETL — Runner suite e2e-v2 su Windows nativo (fixture su host remoto)
# =============================================================================
#
# Esegue la suite E2E v2 "vera" (backend/test-e2e-v2, gli scenari numerati che
# pilotano l'app via HTTP) contro l'INSTALLAZIONE NATIVA Windows, con le
# fixture Docker (PostgreSQL, fake-REST, FTP/SFTP, MinIO) su un HOST separato
# raggiunto via rete (tipicamente la macchina host della VM).
#
#   VM (nativo)  ── rete ──▶  HOST (docker compose delle fixture e2e-v2)
#     app reale                PG :55432, fake-REST :58080, FTP :2121, ...
#     servizi Windows
#
# Il harness e2e-v2 e' Docker-first: con PLENORA_E2E_NATIVE=1 (impostato qui)
# i suoi due agganci Docker — run_sql e la prova in-process test_073 — passano
# per psql.exe e `conda run`, e l'attach di rete diventa no-op (shim in
# test/functional/_e2e.py).
#
# I test cross-db (MySQL/SQL Server/Oracle) si auto-SKIPpano se
# PLENORA_E2E_CROSS_DB non e' settato: questo runner NON lo setta → subset core.
#
# Uso (PowerShell, admin per il restart servizi al primo giro):
#   powershell -ExecutionPolicy Bypass -File scripts\run-e2e-v2-windows.ps1 `
#       -FixtureHost 192.168.x.x
#   ... -Tests 001,003,072,704   esegue SOLO quei test_NNN.py (smoke del pipe)
#   ... -KeepData                non cancella i progetti creati (ispezione)
#   ... -SkipServiceRestart      non tocca i servizi (SSRF opt-in gia' attivo)
#
# NB: gli e2e-v2 scrivono nel DB APPLICATIVO reale della VM (run_sql fa UPDATE
# diretti su plenora.projects) e creano progetti/connessioni come superadmin.
# I test fanno cleanup a successo, ma e' una box di collaudo: non usarlo su un
# ambiente cliente.
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$FixtureHost,
    [int]$PgPort = 55432,
    [int]$FakeRestPort = 58080,
    [int]$FtpPort = 2121,
    [int]$SftpPort = 2222,
    [int]$MinioPort = 59000,
    [string]$AdminUser = 'admin',
    [string]$AdminPassword = 'plenora_admin',
    [string[]]$Tests,
    [int]$TaskTimeoutSeconds = 120,
    [switch]$KeepData,
    [switch]$SkipServiceRestart
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$E2eTestDir = Join-Path $BackendDir 'test-e2e-v2\test'
$FunctionalDir = Join-Path $E2eTestDir 'functional'
$CondaEnv = 'plenora'

function Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

# -----------------------------------------------------------------------------
# Toolchain
# -----------------------------------------------------------------------------
$condaBat = @(
    "$env:ProgramData\miniforge3\condabin\conda.bat",
    "$env:USERPROFILE\miniforge3\condabin\conda.bat",
    "$env:LOCALAPPDATA\miniforge3\condabin\conda.bat"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $condaBat) { Fail 'conda.bat non trovato (Miniforge)' }

$psql = @(
    'C:\Program Files\PostgreSQL\17\bin\psql.exe',
    'C:\Program Files\PostgreSQL\16\bin\psql.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $psql) { Fail 'psql.exe non trovato (PostgreSQL 16/17)' }

$envFile = Join-Path $BackendDir '.env'
if (-not (Test-Path $envFile)) { Fail 'backend\.env non trovato: eseguire prima l''installer' }

if (-not (Test-Path $FunctionalDir)) { Fail "Suite e2e-v2 non trovata: $FunctionalDir" }

# -----------------------------------------------------------------------------
# Credenziali DB applicativo dalla DATABASE_URL del .env (per run_sql/psql)
# -----------------------------------------------------------------------------
$dbUrl = ''
foreach ($line in Get-Content $envFile) {
    if ($line -match '^DATABASE_URL=(.+)$') { $dbUrl = $Matches[1].Trim() }
}
if (-not $dbUrl) { Fail 'DATABASE_URL non trovata nel .env' }
if ($dbUrl -notmatch '^postgres(ql)?://([^:]+):([^@]*)@([^:/]+):(\d+)/(.+)$') {
    Fail "DATABASE_URL non parsabile: $dbUrl"
}
$appDbUser = $Matches[2]
$appDbPass = $Matches[3]
$appDbHost = $Matches[4]
$appDbPort = $Matches[5]
$appDbName = ($Matches[6] -split '\?')[0]
Ok "DB applicativo: $appDbUser@${appDbHost}:$appDbPort/$appDbName"

# -----------------------------------------------------------------------------
# SSRF opt-in: il backend chiamera' la fake-REST su IP privato dell'host.
# Senza PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND=1 validate_external_url blocca.
# -----------------------------------------------------------------------------
Step 'SSRF opt-in per rete privata (fake-REST su host)'
function Set-EnvValue([string]$path, [string]$key, [string]$value) {
    $lines = @(Get-Content $path)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$([regex]::Escape($key))=") { $lines[$i] = "$key=$value"; $found = $true }
    }
    if (-not $found) { $lines += "$key=$value" }
    Set-Content -Path $path -Value $lines -Encoding utf8
}

$already = $false
foreach ($line in Get-Content $envFile) {
    if ($line -match '^PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND=(.+)$') {
        if ($Matches[1].Trim() -match '^(1|true|yes|on)$') { $already = $true }
    }
}
if ($already) {
    Ok 'PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND gia'' attivo nel .env'
} else {
    Set-EnvValue $envFile 'PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND' '1'
    Ok 'PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND=1 scritto nel .env'
}

if ($SkipServiceRestart) {
    Warn 'restart servizi saltato (-SkipServiceRestart): assicurati che il backend abbia gia'' l''opt-in in env'
} elseif (-not $already) {
    Step 'Restart servizi backend/worker (ricaricano il .env)'
    $services = @(Get-Service -Name 'plenora-backend', 'plenora-worker*' -ErrorAction SilentlyContinue)
    if ($services.Count -eq 0) {
        Warn "plenora-backend/plenora-worker* non trovati (dev senza -InstallServices?): riavvia a mano backend e worker per l'opt-in"
    }
    foreach ($s in $services) {
        $svc = $s.Name
        if ($s) {
            Restart-Service -Name $svc -Force
            Ok "$svc riavviato"
        }
    }
}

# -----------------------------------------------------------------------------
# Preflight: fixture raggiungibili + backend pronto
# -----------------------------------------------------------------------------
Step 'Preflight rete fixture + backend'
function Test-Port([string]$computer, [int]$port) {
    return (Test-NetConnection -ComputerName $computer -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue)
}
if (-not (Test-Port $FixtureHost $PgPort)) {
    Fail "PostgreSQL fixture NON raggiungibile su ${FixtureHost}:$PgPort — lo stack fixture e' avviato sull'host e la porta e' pubblicata/aperta nel firewall?"
}
Ok "PG fixture ok: ${FixtureHost}:$PgPort"
if (-not (Test-Port $FixtureHost $FakeRestPort)) {
    Fail "fake-REST NON raggiungibile su ${FixtureHost}:$FakeRestPort"
}
Ok "fake-REST ok: ${FixtureHost}:$FakeRestPort"

$apiBase = 'http://127.0.0.1:5004/api'
try {
    $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:5004/ready' -TimeoutSec 10
    Ok 'backend /ready risponde'
} catch {
    Fail "backend non pronto su 127.0.0.1:5004 (/ready): $($_.Exception.Message)"
}

# -----------------------------------------------------------------------------
# Env per il processo di test (ereditato da `conda run`)
# -----------------------------------------------------------------------------
Step 'Ambiente dei test e2e-v2 (native mode)'
$fakeBase = "http://${FixtureHost}:$FakeRestPort"

# Shim native
$env:PLENORA_E2E_NATIVE = '1'
$env:PLENORA_E2E_API_BASE_URL = $apiBase
$env:PLENORA_E2E_USERNAME = $AdminUser
$env:PLENORA_E2E_PASSWORD = $AdminPassword
$env:PLENORA_E2E_TASK_TIMEOUT_SECONDS = "$TaskTimeoutSeconds"

# DB applicativo per run_sql (psql.exe locale)
$env:PLENORA_E2E_PSQL = $psql
$env:PLENORA_E2E_APP_DB_HOST = $appDbHost
$env:PLENORA_E2E_APP_DB_PORT = $appDbPort
$env:PLENORA_E2E_APP_DB_USER = $appDbUser
$env:PLENORA_E2E_APP_DB_NAME = $appDbName
$env:PLENORA_E2E_APP_DB_PASSWORD = $appDbPass

# Backend python in-process per test_073 (conda run nella env)
$env:PLENORA_E2E_CONDA = $condaBat
$env:PLENORA_E2E_CONDA_ENV = $CondaEnv
$env:PLENORA_E2E_BACKEND_CWD = $BackendDir

# Fixture PostgreSQL sorgente (host remoto)
$env:PLENORA_E2E_SOURCE_DB_HOST = $FixtureHost
$env:PLENORA_E2E_SOURCE_DB_PORT = "$PgPort"
$env:PLENORA_E2E_SOURCE_DB_SCHEMA = 'public'
$env:POSTGRES_USER = 'plenora'
$env:POSTGRES_PASSWORD = 'plenora_test_pwd'
$env:POSTGRES_DB = 'plenora_test'

# Fixture fake-REST: base = quella che usa il BACKEND (embedded nei payload),
# host = quella che usa il processo di test per seed/reset, sink = URL CARICA.
$env:PLENORA_E2E_FAKE_REST_BASE_URL = $fakeBase
$env:PLENORA_E2E_FAKE_REST_HOST_URL = $fakeBase
$env:PLENORA_E2E_FAKE_REST_CONTAINER_SINK_URL = "$fakeBase/data/sink/records"
$env:PLENORA_E2E_FAKE_REST_API_KEY = 'PLENORA_TEST_KEY'

# Fixture file transfer
$env:PLENORA_E2E_FTP_HOST = $FixtureHost
$env:PLENORA_E2E_FTP_PORT = "$FtpPort"
$env:PLENORA_E2E_SFTP_HOST = $FixtureHost
$env:PLENORA_E2E_SFTP_PORT = "$SftpPort"
$env:FTP_USER = 'tester'; $env:FTP_PASSWORD = 'plenora_ftp_pwd'
$env:SFTP_USER = 'tester'; $env:SFTP_PASSWORD = 'plenora_sftp_pwd'

# Subset core: cross-db OFF (i test si auto-skippano).
Remove-Item Env:\PLENORA_E2E_CROSS_DB -ErrorAction SilentlyContinue
if ($KeepData) { $env:PLENORA_E2E_KEEP_ON_SUCCESS = '1' }
Ok "native mode; fixture su $FixtureHost; cross-db OFF (subset core)"

# -----------------------------------------------------------------------------
# Esecuzione
# -----------------------------------------------------------------------------
if ($Tests -and $Tests.Count -gt 0) {
    Step "Esecuzione mirata: $($Tests.Count) test"
    $failed = @()
    foreach ($t in $Tests) {
        $name = $t.Trim()
        if ($name -notmatch '^test_') { $name = "test_$name" }
        if ($name -notmatch '\.py$') { $name = "$name.py" }
        $path = Join-Path $FunctionalDir $name
        if (-not (Test-Path $path)) { Warn "assente: $name"; $failed += $name; continue }
        Write-Host "`n==> $name" -ForegroundColor Magenta
        & $condaBat run -n $CondaEnv --cwd $FunctionalDir python $name
        if ($LASTEXITCODE -ne 0) { $failed += $name }
    }
    if ($failed.Count -gt 0) {
        Write-Host "`n[FAIL] rossi: $($failed -join ', ')" -ForegroundColor Red
        exit 1
    }
    Ok 'tutti i test mirati verdi'
    exit 0
} else {
    Step 'Esecuzione suite completa (run_all.py; cross-db si auto-skippa)'
    & $condaBat run -n $CondaEnv --cwd $E2eTestDir python run_all.py
    $rc = $LASTEXITCODE
    if ($rc -eq 0) {
        Ok 'suite e2e-v2 (subset core) VERDE su Windows nativo'
    } else {
        Write-Host "[FAIL] run_all exit=$rc — riportare l'output" -ForegroundColor Red
    }
    exit $rc
}
