# =============================================================================
# Plenora-ETL — Installer nativo Windows (PROTOTIPO DI SCOPING)
# =============================================================================
#
# Stato: PROTOTIPO (2026-07-04). Nato per la sessione di scoping su VM
# Windows Server 2025 (vedi docs/ops/scoping-windows-native.md). Automatizza
# il bring-up completo: PostgreSQL + Erlang/RabbitMQ + Miniforge/conda
# (GDAL nativo) + pip deps + .env + smoke round-trip sul worker Dramatiq.
#
# NON e' ancora l'installer di produzione: niente servizi Windows (i
# processi si avviano in finestre separate), niente hardening ACL,
# password di default da scoping. `scripts/install.ps1` resta lo stub
# finche' questo prototipo non si laurea con la validazione sulla VM.
#
# Uso (PowerShell da AMMINISTRATORE, dalla root del repo):
#   powershell -ExecutionPolicy Bypass -File scripts\install-native.ps1
#
# Parametri utili:
#   -SkipWinget      salta le install winget (pacchetti gia' presenti)
#   -PgSuperPassword password del superuser postgres (default: chiede)
#   -StartProcesses  a fine setup apre 3 finestre: backend, worker, beat
#
# Ogni step stampa [STEP]/[OK]/[FAIL] e si ferma al primo errore: in
# scoping un fallimento e' un DATO, non un incidente — riportare l'output.
# =============================================================================

[CmdletBinding()]
param(
    [switch]$SkipWinget,
    [string]$PgSuperPassword = '',
    [switch]$StartProcesses
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$DataDir = 'C:\plenora-data'
$CondaEnv = 'plenora'

function Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

function New-RandomSecret {
    # 48 char alfanumerici via RNG crittografico .NET
    $chars = ([char[]]([char]'a'..[char]'z') + [char[]]([char]'A'..[char]'Z') + [char[]]([char]'0'..[char]'9'))
    -join (1..48 | ForEach-Object { $chars | Get-Random })
}

# -----------------------------------------------------------------------------
Step 'Verifica prerequisiti di base'
# -----------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $BackendDir 'requirements.txt'))) {
    Fail "Repo non trovato: eseguire dalla root del repo (manca backend\requirements.txt)"
}
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Fail 'Servono privilegi di Amministratore (winget/servizi).' }
Ok "Repo: $RepoRoot"

# -----------------------------------------------------------------------------
if (-not $SkipWinget) {
    Step 'Install pacchetti di piattaforma (winget)'
    # NB: gli ID winget possono variare col tempo; su fallimento di un ID
    # usare `winget search <nome>` e annotare l'ID corretto nel runbook.
    $pkgs = @(
        'Git.Git',
        'OpenJS.NodeJS.LTS',
        'PostgreSQL.PostgreSQL.17',
        'Erlang.ErlangOTP',          # PRIMA di RabbitMQ
        'VMware.RabbitMQ',
        'CondaForge.Miniforge3'
    )
    foreach ($p in $pkgs) {
        Write-Host "  winget install $p"
        winget install --id $p --accept-package-agreements --accept-source-agreements --silent
        if ($LASTEXITCODE -notin 0, -1978335189) {   # -1978335189 = gia' installato
            Fail "winget install $p exit=$LASTEXITCODE (annotare e cercare l'ID con 'winget search')"
        }
    }
    Ok 'Pacchetti piattaforma installati (o gia'' presenti)'
    Write-Host '  NB: se Miniforge/psql non sono nel PATH, CHIUDERE e riaprire la shell admin e rilanciare con -SkipWinget.' -ForegroundColor Yellow
}

# -----------------------------------------------------------------------------
Step 'PostgreSQL: utente e database plenora'
# -----------------------------------------------------------------------------
$psql = 'C:\Program Files\PostgreSQL\17\bin\psql.exe'
if (-not (Test-Path $psql)) { Fail "psql non trovato in $psql (install PostgreSQL fallita o path diverso)" }
if (-not $PgSuperPassword) {
    $sec = Read-Host 'Password del superuser postgres (impostata dall''installer PG)' -AsSecureString
    $PgSuperPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
$env:PGPASSWORD = $PgSuperPassword
& $psql -U postgres -h 127.0.0.1 -tAc "SELECT 1 FROM pg_roles WHERE rolname='plenora'" | Out-String -OutVariable roleExists | Out-Null
if ($roleExists.Trim() -ne '1') {
    & $psql -U postgres -h 127.0.0.1 -c "CREATE USER plenora WITH PASSWORD 'plenora' CREATEDB;"
}
& $psql -U postgres -h 127.0.0.1 -tAc "SELECT 1 FROM pg_database WHERE datname='plenora'" | Out-String -OutVariable dbExists | Out-Null
if ($dbExists.Trim() -ne '1') {
    & $psql -U postgres -h 127.0.0.1 -c "CREATE DATABASE plenora OWNER plenora;"
}
$env:PGPASSWORD = 'plenora'
$one = (& $psql -U plenora -h 127.0.0.1 -d plenora -tAc 'SELECT 1').Trim()
Remove-Item Env:\PGPASSWORD
if ($one -ne '1') { Fail 'SELECT 1 come utente plenora fallita' }
Ok 'PostgreSQL pronto (db plenora, utente plenora)'

# -----------------------------------------------------------------------------
Step 'RabbitMQ: servizio e porta 5672'
# -----------------------------------------------------------------------------
$svc = Get-Service -Name 'RabbitMQ' -ErrorAction SilentlyContinue
if (-not $svc) { Fail 'Servizio RabbitMQ non trovato (install fallita?)' }
if ($svc.Status -ne 'Running') { Start-Service RabbitMQ; Start-Sleep -Seconds 5 }
$tnc = Test-NetConnection 127.0.0.1 -Port 5672 -WarningAction SilentlyContinue
if (-not $tnc.TcpTestSucceeded) { Fail 'Porta 5672 chiusa: RabbitMQ non in ascolto' }
Ok 'RabbitMQ in ascolto su 5672 (guest/guest, solo localhost)'

# -----------------------------------------------------------------------------
Step "Ambiente conda '$CondaEnv' (python 3.12 + GDAL 3.13 da conda-forge)"
# -----------------------------------------------------------------------------
$condaBat = @(
    "$env:ProgramData\miniforge3\condabin\conda.bat",
    "$env:USERPROFILE\miniforge3\condabin\conda.bat",
    "$env:LOCALAPPDATA\miniforge3\condabin\conda.bat"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $condaBat) { Fail 'conda.bat non trovato (Miniforge non nel path atteso — riaprire la shell o annotare il path reale)' }

$envList = & $condaBat env list
if (-not ($envList | Select-String -Pattern "^\s*$CondaEnv\s")) {
    # NB linux-64 ha richiesto il pin libjxl=0.11.* (gdal 3.13 linkava
    # libjxl.so.0.11). Su win-64 partiamo senza pin: se gdalinfo fallisce
    # per DLL mancanti, aggiungere "libjxl=0.11.*" qui e annotare.
    & $condaBat create -y -n $CondaEnv -c conda-forge `
        python=3.12 gdal=3.13.0 proj-data pyogrio geopandas-base `
        pyproj shapely pandas numpy pyarrow openpyxl pip
    if ($LASTEXITCODE -ne 0) { Fail 'conda create fallita (annotare output resolver)' }
}
$gdalVer = & $condaBat run -n $CondaEnv gdalinfo --version
if ($LASTEXITCODE -ne 0) { Fail "gdalinfo non parte nell'env (DLL? provare pin libjxl)" }
Ok "Env conda pronto: $gdalVer"

# -----------------------------------------------------------------------------
Step 'Dipendenze pip (filtrando i pacchetti forniti da conda)'
# -----------------------------------------------------------------------------
$condaPkgs = 'geopandas', 'numpy', 'openpyxl', 'pandas', 'pyarrow', 'pyogrio', 'pyproj', 'shapely'
$reqPip = Join-Path $BackendDir 'requirements-pip.txt'
Get-Content (Join-Path $BackendDir 'requirements.txt') | Where-Object {
    $pkg = ($_.Trim() -split '[<>=!~\[]')[0].Trim()
    -not ($condaPkgs -contains $pkg)
} | Set-Content -Encoding ascii $reqPip
& $condaBat run -n $CondaEnv python -m pip install -r $reqPip
if ($LASTEXITCODE -ne 0) { Fail 'pip install fallita (annotare il pacchetto incriminato)' }
& $condaBat run -n $CondaEnv python -c "import fastapi, dramatiq, psycopg, pika, apscheduler, croniter; print('deps ok')"
if ($LASTEXITCODE -ne 0) { Fail 'import check fallito' }
Ok 'Dipendenze python complete'

# -----------------------------------------------------------------------------
Step "Configurazione .env + DATA_DIR ($DataDir)"
# -----------------------------------------------------------------------------
$envFile = Join-Path $BackendDir '.env'
if (Test-Path $envFile) {
    Ok '.env gia'' presente: non tocco (rimuoverlo per rigenerarlo)'
} else {
    $jwt = New-RandomSecret
    $enc = New-RandomSecret
    @"
# Generato da install-native.ps1 (scoping) — $(Get-Date -Format 'yyyy-MM-dd HH:mm')
ENVIRONMENT=development
DATABASE_URL=postgresql://plenora:plenora@127.0.0.1:5432/plenora
DB_SCHEMA=plenora
DRAMATIQ_BROKER_URL=amqp://guest:guest@127.0.0.1:5672
JWT_SECRET_KEY=$jwt
PLENORA_ENCRYPTION_KEY=$enc
DATA_DIR=$DataDir
"@ | Set-Content -Encoding ascii $envFile
    Ok '.env generato (secrets random; ENVIRONMENT=development per lo scoping)'
}
New-Item -ItemType Directory -Force $DataDir | Out-Null

# -----------------------------------------------------------------------------
Step 'Smoke script (backend/smoke_native.py)'
# -----------------------------------------------------------------------------
@'
"""Smoke round-trip: dispatch cleanroom.ping -> poll task_results su PG."""
import time
from app.core.task_runtime.dispatch import dispatch_task_by_name
from app.core.orchestrator.task_result_store import default_task_result_store

handle = dispatch_task_by_name("cleanroom.ping")
store = default_task_result_store()
deadline = time.monotonic() + 30
state = "PENDING"
while time.monotonic() < deadline:
    record = store.get(handle.id)
    if record is not None and record.state in ("SUCCESS", "FAILURE"):
        state = record.state
        break
    time.sleep(0.5)
print("SMOKE:", state)
raise SystemExit(0 if state == "SUCCESS" else 1)
'@ | Set-Content -Encoding ascii (Join-Path $BackendDir 'smoke_native.py')
Ok 'smoke_native.py scritto'

# -----------------------------------------------------------------------------
Write-Host ''
Write-Host '=============================================================' -ForegroundColor Green
Write-Host ' Setup completato. Prossimi passi (3 finestre + smoke):' -ForegroundColor Green
Write-Host '=============================================================' -ForegroundColor Green
Write-Host '  (comandi da eseguire in un "Miniforge Prompt", o via -StartProcesses)'
Write-Host "  1) backend:  conda activate $CondaEnv && cd /d $BackendDir && python -m uvicorn app.main:app --host 127.0.0.1 --port 5004"
Write-Host "  2) worker:   conda activate $CondaEnv && cd /d $BackendDir && python -m dramatiq workers.dramatiq_app --processes 1 --threads 4"
Write-Host "  3) beat:     conda activate $CondaEnv && cd /d $BackendDir && python -m workers.beat"
Write-Host "  4) smoke:    conda activate $CondaEnv && cd /d $BackendDir && python smoke_native.py   (atteso: SMOKE: SUCCESS)"
Write-Host ''
Write-Host '  Checkpoint: curl.exe -s http://127.0.0.1:5004/ready' -ForegroundColor Cyan
Write-Host '  RISCHIO n.1: la finestra 2 (worker CLI dramatiq su Windows).' -ForegroundColor Yellow
Write-Host '  Se esplode: riportare il traceback TESTUALE — piano B nel runbook §8.' -ForegroundColor Yellow

if ($StartProcesses) {
    Step 'Avvio processi in finestre separate (-StartProcesses)'
    # cmd /k + call conda.bat activate: l'attivazione batch e' l'unica
    # affidabile senza `conda init powershell` (PATH a Library\bin per
    # le DLL GDAL incluso).
    $prefix = "call `"$condaBat`" activate $CondaEnv && cd /d `"$BackendDir`" &&"
    Start-Process cmd -ArgumentList '/k', "$prefix python -m uvicorn app.main:app --host 127.0.0.1 --port 5004"
    Start-Sleep -Seconds 15
    Start-Process cmd -ArgumentList '/k', "$prefix python -m dramatiq workers.dramatiq_app --processes 1 --threads 4"
    Start-Process cmd -ArgumentList '/k', "$prefix python -m workers.beat"
    Ok 'Tre finestre avviate (backend, worker, beat). Eseguire poi smoke_native.py in una quarta.'
}
