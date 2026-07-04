# =============================================================================
# Plenora-ETL — Runner test e2e-v2 su Windows nativo (functional/integration)
# =============================================================================
#
# Lancia le suite e2e-v2 (tests2/functional, opz. tests2/integration)
# sull'installazione nativa Windows (vedi docs/ops/install-windows.md),
# SENZA toccare i servizi di produzione:
#   - DB dedicato `plenora_test` (creato+migrato qui): niente dati di
#     test nel DB reale, niente contesa del leader lock del beat;
#   - broker condiviso ma CODE SUFFISSATE per-run (harness
#     isolate_task_queues): il worker di produzione sulle code default
#     non vede i task di test;
#   - backend/worker/fake-API di test spawnati su porte libere.
#
# Uso (Miniforge Prompt o PowerShell, NON serve admin):
#   powershell -ExecutionPolicy Bypass -File scripts\run-e2e-windows.ps1
#   ... -Integration     esegue anche tests2/integration
#   ... -SkipDeps        salta pip install -r requirements-dev.txt
#   ... -PytestArgs '-k api_enrich'   argomenti extra a pytest
#
# NB: e' pensato per la macchina di collaudo/installazione — non
# lanciarlo su un box di un cliente (crea un DB e traffico di test).
# =============================================================================

[CmdletBinding()]
param(
    [switch]$Integration,
    [switch]$SkipDeps,
    [string]$PytestArgs = ''
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$CondaEnv = 'plenora'
$TestDb = 'plenora_test'

function Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

$condaBat = @(
    "$env:ProgramData\miniforge3\condabin\conda.bat",
    "$env:USERPROFILE\miniforge3\condabin\conda.bat",
    "$env:LOCALAPPDATA\miniforge3\condabin\conda.bat"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $condaBat) { Fail 'conda.bat non trovato (Miniforge)' }

$envFile = Join-Path $BackendDir '.env'
if (-not (Test-Path $envFile)) { Fail 'backend\.env non trovato: eseguire prima l''installer' }

# -----------------------------------------------------------------------------
Step 'Dipendenze dev (pytest & co.)'
# -----------------------------------------------------------------------------
if ($SkipDeps) {
    Ok 'saltate (-SkipDeps)'
} else {
    & $condaBat run -n $CondaEnv --cwd $BackendDir python -m pip install -q -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { Fail "pip install requirements-dev exit=$LASTEXITCODE" }
    Ok 'requirements-dev installate'
}

# -----------------------------------------------------------------------------
Step "DB di test dedicato: $TestDb"
# -----------------------------------------------------------------------------
# L'utente plenora ha CREATEDB (creato cosi' dall'installer): niente
# superuser. Il DB di test evita dati fake nel DB reale e la contesa
# del beat leader lock coi servizi di produzione.
$psql = 'C:\Program Files\PostgreSQL\17\bin\psql.exe'
if (-not (Test-Path $psql)) { Fail "psql non trovato: $psql" }
$env:PGPASSWORD = 'plenora'
# [string] cast: quando il DB non esiste la query non produce righe e
# l'output e' $null -> un .Trim() diretto esploderebbe (visto sul campo).
$exists = ([string](& $psql -U plenora -h 127.0.0.1 -d plenora -tAc "SELECT 1 FROM pg_database WHERE datname='$TestDb'")).Trim()
if ($exists -ne '1') {
    & $psql -U plenora -h 127.0.0.1 -d plenora -c "CREATE DATABASE $TestDb;" | Out-Null
    Ok "$TestDb creato"
} else {
    Ok "$TestDb gia' presente"
}
Remove-Item Env:\PGPASSWORD

# -----------------------------------------------------------------------------
Step 'Ambiente dei test'
# -----------------------------------------------------------------------------
# Broker: il fallback host-run del harness usa guest/guest, ma guest e'
# stato RIMOSSO dall'hardening W2 -> serve l'URL con l'utente plenora,
# preso dal .env (sorgente di verita' delle credenziali).
$brokerUrl = ''
foreach ($line in Get-Content $envFile) {
    if ($line -match '^DRAMATIQ_BROKER_URL=(.+)$') { $brokerUrl = $Matches[1] }
}
if (-not $brokerUrl) { Fail 'DRAMATIQ_BROKER_URL non trovato nel .env' }

# Env di processo: VINCE sul .env (dotenv non sovrascrive) e viene
# ereditato da tutti i subprocess spawnati dal harness.
$env:URBY_RUN_INTEGRATION = '1'
$env:DATABASE_URL = "postgresql://plenora:plenora@127.0.0.1:5432/$TestDb"
$env:DRAMATIQ_BROKER_URL = $brokerUrl
$env:ENVIRONMENT = 'development'   # il runner non deve girare coi gates production
Ok "DATABASE_URL -> $TestDb; broker dal .env; URBY_RUN_INTEGRATION=1"

# -----------------------------------------------------------------------------
Step 'Migrations sul DB di test (bootstrap una tantum)'
# -----------------------------------------------------------------------------
# I fixture di seed toccano le tabelle PRIMA che il primo backend di
# test booti: applichiamo le migrations subito, via bootstrap canonico.
@'
from app.config_env_loader import load_backend_dotenv
load_backend_dotenv()
from app.bootstrap import bootstrap
bootstrap()
print("bootstrap test-db ok")
'@ | Set-Content -Encoding ascii (Join-Path $BackendDir '_bootstrap_testdb.py')
try {
    & $condaBat run -n $CondaEnv --cwd $BackendDir python _bootstrap_testdb.py
    if ($LASTEXITCODE -ne 0) { Fail "bootstrap DB test exit=$LASTEXITCODE" }
} finally {
    Remove-Item (Join-Path $BackendDir '_bootstrap_testdb.py') -ErrorAction SilentlyContinue
}
Ok 'migrations applicate sul DB di test'

# -----------------------------------------------------------------------------
$suites = @('tests2/functional')
if ($Integration) { $suites += 'tests2/integration' }
Step "pytest: $($suites -join ', ')"
# -----------------------------------------------------------------------------
$pyArgs = @('-m', 'pytest') + $suites + @('-q', '-p', 'no:cacheprovider')
if ($PytestArgs) { $pyArgs += ($PytestArgs -split '\s+') }
& $condaBat run -n $CondaEnv --cwd $BackendDir python @pyArgs
$rc = $LASTEXITCODE

if ($rc -eq 0) {
    Ok 'e2e-v2 VERDI su Windows nativo'
} else {
    Write-Host "[FAIL] pytest exit=$rc — riportare l'output (prima volta assoluta di queste suite su Windows: ogni rosso e' un dato)" -ForegroundColor Red
}
exit $rc
