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
    [switch]$StartProcesses,
    # W1 — servizi Windows (WinSW): registra plenora-backend/worker/beat
    # come servizi con avvio automatico al boot.
    [switch]$InstallServices,
    [int]$WorkerProcesses = 1,
    [int]$WorkerThreads = 4,
    # W2 — bootstrap superadmin (parita' con install-docker.sh).
    # Fallback env: PLENORA_BOOTSTRAP_ADMIN_USERNAME / _PASSWORD / _GROUP_ID.
    [string]$AdminUsername = '',
    [string]$AdminPassword = '',
    [string]$AdminGroupId = '',
    [switch]$SkipAdminBootstrap,
    # W3 — MinIO opzionale come object storage locale (servizio WinSW):
    # crea i bucket runtime/backup e configura CLOUD_PROVIDER=minio.
    [switch]$InstallMinio,
    [ValidateSet('db', 'hybrid', 'object_storage')]
    [string]$StoragePolicy = 'hybrid'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$DataDir = 'C:\plenora-data'
$CondaEnv = 'plenora'

# Fallback non-interattivi (stesse env dell'installer docker/linux).
if (-not $AdminUsername) { $AdminUsername = $env:PLENORA_BOOTSTRAP_ADMIN_USERNAME }
if (-not $AdminPassword) { $AdminPassword = $env:PLENORA_BOOTSTRAP_ADMIN_PASSWORD }
if (-not $AdminGroupId) {
    $AdminGroupId = if ($env:PLENORA_BOOTSTRAP_ADMIN_GROUP_ID) { $env:PLENORA_BOOTSTRAP_ADMIN_GROUP_ID } else { 'root' }
}
if (-not $PSBoundParameters.ContainsKey('WorkerProcesses') -and $env:PLENORA_INSTALL_WORKER_PROCESSES) {
    $WorkerProcesses = [int]$env:PLENORA_INSTALL_WORKER_PROCESSES
}
if (-not $PSBoundParameters.ContainsKey('WorkerThreads') -and $env:PLENORA_INSTALL_WORKER_THREADS) {
    $WorkerThreads = [int]$env:PLENORA_INSTALL_WORKER_THREADS
}

function Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

function New-RandomSecret {
    # 48 char alfanumerici via RNG crittografico .NET
    $chars = ([char[]]([char]'a'..[char]'z') + [char[]]([char]'A'..[char]'Z') + [char[]]([char]'0'..[char]'9'))
    -join (1..48 | ForEach-Object { $chars | Get-Random })
}

function Set-EnvValue {
    # Aggiorna (o appende) KEY=VALUE nel .env, preservando il resto.
    param([string]$File, [string]$Key, [string]$Value)
    $lines = @(Get-Content $File)
    $found = $false
    $lines = $lines | ForEach-Object {
        if ($_ -match "^$([regex]::Escape($Key))=") { $found = $true; "$Key=$Value" } else { $_ }
    }
    if (-not $found) { $lines += "$Key=$Value" }
    Set-Content -Encoding ascii $File $lines
}

function Invoke-RabbitCtl {
    # rabbitmqctl con sync del cookie Erlang: il servizio gira come
    # LocalSystem (cookie in systemprofile), la shell admin ha il suo —
    # senza allineamento ogni comando fallisce con "incompatible cookie".
    param([string[]]$CtlArgs)
    $ctl = Get-ChildItem 'C:\Program Files\RabbitMQ Server\rabbitmq_server-*\sbin\rabbitmqctl.bat' |
           Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $ctl) { Fail 'rabbitmqctl.bat non trovato' }
    $sysCookie = 'C:\Windows\System32\config\systemprofile\.erlang.cookie'
    $usrCookie = Join-Path $env:USERPROFILE '.erlang.cookie'
    if ((Test-Path $sysCookie) -and (
            -not (Test-Path $usrCookie) -or
            ((Get-Content $sysCookie -Raw) -ne (Get-Content $usrCookie -Raw)))) {
        Copy-Item $sysCookie $usrCookie -Force
        # il cookie utente deve essere scrivibile solo dal proprietario? Su
        # Windows rabbitmqctl non enforc-a i permessi: basta il contenuto.
    }
    # CATTURARE lo stdout: senza, colerebbe nella pipeline e il valore di
    # ritorno diventerebbe un array (output + exit code) — il chiamante
    # confronterebbe mele con pere (visto sul campo alla prima run W2).
    $out = & $ctl.FullName @CtlArgs
    if ($out) { $out | ForEach-Object { Write-Host "    $_" } }
    return $LASTEXITCODE
}

$ServicesRoot = 'C:\plenora-services'

function Get-WinSWExe {
    # WinSW.exe condiviso (W1 servizi app + W3 servizio MinIO): scarica
    # una volta dalla release GitHub, fallback pinnato v2.12.0.
    New-Item -ItemType Directory -Force $ServicesRoot | Out-Null
    $winswExe = Join-Path $ServicesRoot 'WinSW-x64.exe'
    if (-not (Test-Path $winswExe)) {
        Write-Host '  Scarico WinSW (ultima release GitHub)...'
        try {
            $rel = Invoke-RestMethod 'https://api.github.com/repos/winsw/winsw/releases/latest'
            $asset = $rel.assets | Where-Object { $_.name -eq 'WinSW-x64.exe' } | Select-Object -First 1
            if (-not $asset) { throw 'asset WinSW-x64.exe assente nella latest' }
            Invoke-WebRequest $asset.browser_download_url -OutFile $winswExe
        } catch {
            Write-Host "  latest non utilizzabile ($($_.Exception.Message)): fallback WinSW v2.12.0"
            Invoke-WebRequest 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe' -OutFile $winswExe
        }
    }
    return $winswExe
}

function Install-WinSWService {
    # Registra (o refresh-a) un servizio WinSW: cartella dedicata,
    # copia del wrapper col retry (il wrapper molla il file in ritardo
    # dopo lo stop — visto sul campo), XML config, install.
    param([string]$Id, [string]$Xml)
    $dir = Join-Path $ServicesRoot $Id
    New-Item -ItemType Directory -Force $dir | Out-Null
    $exe = Join-Path $dir "$Id.exe"
    if (Get-Service -Name $Id -ErrorAction SilentlyContinue) {
        Write-Host "  ${Id}: gia' registrato — stop + uninstall per refresh config"
        Stop-Service $Id -Force -ErrorAction SilentlyContinue
        & $exe uninstall 2>$null | Out-Null
    }
    $winswExe = Get-WinSWExe
    $copied = $false
    foreach ($try in 1..30) {
        try { Copy-Item $winswExe $exe -Force; $copied = $true; break }
        catch { Start-Sleep -Seconds 1 }
    }
    if (-not $copied) { Fail "${Id}: impossibile aggiornare $exe (wrapper ancora in esecuzione dopo 30s)" }
    $Xml | Set-Content -Encoding ascii (Join-Path $dir "$Id.xml")
    & $exe install
    if ($LASTEXITCODE -ne 0) { Fail "${Id}: winsw install exit=$LASTEXITCODE" }
    Ok "$Id registrato"
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
    # NB scoping VM 2026-07-04: RabbitMQ server NON e' piu' su winget
    # (ne' VMware.RabbitMQ ne' Broadcom.*): si installa dal blocco
    # dedicato piu' sotto (release ufficiale GitHub, NSIS /S).
    # LEZIONE scoping VM 2026-07-04: OTP 29 e' troppo nuovo per RabbitMQ
    # 4.3.x — BOOT FAILED {incompatible_feature_flags,{horus,
    # extraction_denied,{unknown_instruction,...}}}. Pin alla serie 28.
    $pkgs = @(
        @{ id = 'Git.Git' },
        @{ id = 'OpenJS.NodeJS.LTS' },
        @{ id = 'PostgreSQL.PostgreSQL.17' },
        @{ id = 'Erlang.ErlangOTP'; version = '28.5.0.3' },   # PRIMA di RabbitMQ
        @{ id = 'CondaForge.Miniforge3' }
    )
    foreach ($p in $pkgs) {
        Write-Host "  winget install $($p.id)"
        $args = @('install', '--id', $p.id,
                  '--accept-package-agreements', '--accept-source-agreements', '--silent')
        if ($p.version) { $args += @('--version', $p.version) }
        & winget @args
        if ($LASTEXITCODE -notin 0, -1978335189) {   # -1978335189 = gia' installato
            Fail "winget install $($p.id) exit=$LASTEXITCODE (annotare e cercare l'ID con 'winget search')"
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
Step 'RabbitMQ: install (release GitHub) + servizio e porta 5672'
# -----------------------------------------------------------------------------
# LEZIONE scoping VM 2026-07-04: winget installa Erlang SENZA settare
# ERLANG_HOME; senza, il servizio RabbitMQ si registra ma non parte mai
# (e rabbitmq-server.bat fallisce esplicito). Va impostata PRIMA
# dell'install di RabbitMQ, a livello Machine per il servizio.
if (-not $env:ERLANG_HOME) {
    $erlHome = 'C:\Program Files\Erlang OTP'
    if (-not (Test-Path $erlHome)) { Fail "Erlang non trovato in $erlHome (install OTP fallita o path diverso)" }
    [Environment]::SetEnvironmentVariable('ERLANG_HOME', $erlHome, 'Machine')
    $env:ERLANG_HOME = $erlHome
    Ok "ERLANG_HOME impostata: $erlHome"
}

$svc = Get-Service -Name 'RabbitMQ' -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host '  RabbitMQ non presente: scarico l''ultima release ufficiale...'
    $rel = Invoke-RestMethod 'https://api.github.com/repos/rabbitmq/rabbitmq-server/releases/latest'
    $asset = $rel.assets | Where-Object { $_.name -like 'rabbitmq-server-*.exe' } | Select-Object -First 1
    if (-not $asset) { Fail 'Nessun installer .exe nella release RabbitMQ (annotare tag e asset)' }
    Write-Host "  $($rel.tag_name) -> $($asset.name)"
    $exe = Join-Path $env:TEMP $asset.name
    Invoke-WebRequest $asset.browser_download_url -OutFile $exe
    Start-Process $exe -ArgumentList '/S' -Wait     # NSIS silent; registra il servizio
    Start-Sleep -Seconds 10
    $svc = Get-Service -Name 'RabbitMQ' -ErrorAction SilentlyContinue
}
if (-not $svc) { Fail 'Servizio RabbitMQ non trovato dopo l''install (compat Erlang? vedi runbook)' }
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
# Storage: senza questi il bootstrap fallisce validate_storage_config
# (default STORAGE_POLICY=hybrid esige un bucket object storage) e
# SALTA anche le migrations -> errori 'relazione logs non esiste'.
# Lezione scoping VM 2026-07-04.
STORAGE_POLICY=db
BACKUP_BUCKET=plenora-scoping-backup
# Niente credenziali cloud sulla VM: senza questi due /ready resta
# not_ready su object_storage (probe S3 -> NoCredentialsError).
OBJECT_STORAGE_ENABLED=false
CLOUD_PROVIDER=none
"@ | Set-Content -Encoding ascii $envFile
    Ok '.env generato (secrets random; ENVIRONMENT=development per lo scoping)'
}
New-Item -ItemType Directory -Force $DataDir | Out-Null

# -----------------------------------------------------------------------------
Step 'W2: hardening broker — utente RabbitMQ dedicato (via guest)'
# -----------------------------------------------------------------------------
$envContent = Get-Content $envFile -Raw
if ($envContent -match 'DRAMATIQ_BROKER_URL=amqp://plenora:') {
    Ok 'Broker gia'' su utente dedicato plenora: skip'
} else {
    $rabbitPw = New-RandomSecret
    # add_user fallisce se esiste: in quel caso change_password (idempotenza
    # dopo un run interrotto in cui la password non era arrivata al .env).
    $rc = Invoke-RabbitCtl @('add_user', 'plenora', $rabbitPw)
    if ($rc -ne 0) {
        $rc = Invoke-RabbitCtl @('change_password', 'plenora', $rabbitPw)
        if ($rc -ne 0) { Fail "rabbitmqctl add_user/change_password exit=$rc" }
    }
    $rc = Invoke-RabbitCtl @('set_permissions', '-p', '/', 'plenora', '.*', '.*', '.*')
    if ($rc -ne 0) { Fail "rabbitmqctl set_permissions exit=$rc" }
    Set-EnvValue -File $envFile -Key 'DRAMATIQ_BROKER_URL' -Value "amqp://plenora:$rabbitPw@127.0.0.1:5672"
    # Via l'utente guest (best effort: su nodo fresco esiste, dopo no).
    Invoke-RabbitCtl @('delete_user', 'guest') | Out-Null
    # I servizi gia' registrati leggono il .env al boot: restart per
    # fargli vedere le credenziali nuove.
    $running = Get-Service plenora-* -ErrorAction SilentlyContinue | Where-Object Status -eq 'Running'
    foreach ($s in $running) { Restart-Service $s.Name }
    Ok 'Utente broker plenora creato (password random nel .env), guest rimosso'
}

# -----------------------------------------------------------------------------
Step 'W2: ACL sul .env (solo Administrators + SYSTEM)'
# -----------------------------------------------------------------------------
# SID e non nomi: su Windows localizzato "Administrators" si chiama
# "Amministratori" — icacls coi nomi inglesi fallirebbe.
# S-1-5-32-544 = builtin Administrators, S-1-5-18 = LocalSystem.
icacls $envFile /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "icacls sul .env exit=$LASTEXITCODE" }
Ok 'ACL ristretta sul .env (Administrators + SYSTEM)'

# =============================================================================
# W3 — MinIO opzionale (object storage locale come servizio Windows)
# =============================================================================
if ($InstallMinio) {
    Step "W3: MinIO come servizio Windows (STORAGE_POLICY=$StoragePolicy)"

    $minioSvcDir = Join-Path $ServicesRoot 'plenora-minio'
    New-Item -ItemType Directory -Force $minioSvcDir | Out-Null
    $minioExe = Join-Path $minioSvcDir 'minio.exe'
    if (-not (Test-Path $minioExe)) {
        Write-Host '  Scarico minio.exe (release ufficiale windows-amd64)...'
        Invoke-WebRequest 'https://dl.min.io/server/minio/release/windows-amd64/minio.exe' -OutFile $minioExe
    }
    $minioData = Join-Path $DataDir 'minio'
    New-Item -ItemType Directory -Force $minioData | Out-Null

    # Credenziali: riusa quelle gia' nel .env (idempotenza), altrimenti
    # genera. NB: il .env e' la sorgente di verita' — la password non e'
    # recuperabile da MinIO stesso.
    $minioUser = 'plenora-minio'
    $minioPw = ''
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^MINIO_ACCESS_KEY=(.+)$') { $minioUser = $Matches[1] }
        if ($line -match '^MINIO_SECRET_KEY=(.+)$') { $minioPw = $Matches[1] }
    }
    if (-not $minioPw) { $minioPw = New-RandomSecret }

    $minioXml = @"
<service>
  <id>plenora-minio</id>
  <name>Plenora MinIO</name>
  <description>Object storage locale S3-compatible per Plenora-ETL</description>
  <executable>$minioExe</executable>
  <arguments>server $minioData --address 127.0.0.1:9000 --console-address 127.0.0.1:9001</arguments>
  <workingdirectory>$minioSvcDir</workingdirectory>
  <env name="MINIO_ROOT_USER" value="$minioUser"/>
  <env name="MINIO_ROOT_PASSWORD" value="$minioPw"/>
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="5 sec"/>
  <stoptimeout>30 sec</stoptimeout>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>4</keepFiles>
  </log>
</service>
"@
    Install-WinSWService -Id 'plenora-minio' -Xml $minioXml
    Start-Service plenora-minio

    $up = $false
    foreach ($i in 1..20) {
        $tnc = Test-NetConnection 127.0.0.1 -Port 9000 -WarningAction SilentlyContinue
        if ($tnc.TcpTestSucceeded) { $up = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $up) { Fail 'MinIO non in ascolto su 9000 entro 40s (vedi C:\plenora-services\plenora-minio\*.log)' }
    Ok 'MinIO in ascolto su 127.0.0.1:9000 (console: 9001)'

    # Bucket runtime + backup via boto3 (gia' nelle dipendenze), idempotente.
    @'
"""Crea i bucket MinIO per Plenora (runtime + backup). Idempotente."""
import os
import boto3
from botocore.client import Config

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["PLENORA_MINIO_URL"],
    aws_access_key_id=os.environ["PLENORA_MINIO_USER"],
    aws_secret_access_key=os.environ["PLENORA_MINIO_PASSWORD"],
    config=Config(signature_version="s3v4"),
    region_name="us-east-1",
)
for bucket in ("plenora-runtime", "plenora-backups"):
    try:
        s3.create_bucket(Bucket=bucket)
        print("bucket creato:", bucket)
    except Exception as e:
        if "BucketAlreadyOwnedByYou" in str(e) or "BucketAlreadyExists" in str(e):
            print("bucket ok:", bucket)
        else:
            raise
'@ | Set-Content -Encoding ascii (Join-Path $BackendDir '_minio_buckets.py')
    $env:PLENORA_MINIO_URL = 'http://127.0.0.1:9000'
    $env:PLENORA_MINIO_USER = $minioUser
    $env:PLENORA_MINIO_PASSWORD = $minioPw
    try {
        & $condaBat run -n $CondaEnv --cwd $BackendDir python _minio_buckets.py
        if ($LASTEXITCODE -ne 0) { Fail "creazione bucket MinIO exit=$LASTEXITCODE" }
    } finally {
        Remove-Item (Join-Path $BackendDir '_minio_buckets.py') -ErrorAction SilentlyContinue
        Remove-Item Env:\PLENORA_MINIO_PASSWORD -ErrorAction SilentlyContinue
    }

    # .env: attiva l'object storage sul provider minio.
    Set-EnvValue -File $envFile -Key 'STORAGE_POLICY' -Value $StoragePolicy
    Set-EnvValue -File $envFile -Key 'OBJECT_STORAGE_ENABLED' -Value 'true'
    Set-EnvValue -File $envFile -Key 'CLOUD_PROVIDER' -Value 'minio'
    Set-EnvValue -File $envFile -Key 'MINIO_ENDPOINT_URL' -Value 'http://127.0.0.1:9000'
    Set-EnvValue -File $envFile -Key 'MINIO_ACCESS_KEY' -Value $minioUser
    Set-EnvValue -File $envFile -Key 'MINIO_SECRET_KEY' -Value $minioPw
    Set-EnvValue -File $envFile -Key 'OBJECT_STORAGE_BUCKET' -Value 'plenora-runtime'
    Set-EnvValue -File $envFile -Key 'BACKUP_BUCKET' -Value 'plenora-backups'

    # I servizi app leggono il .env al boot: restart per vedere MinIO.
    $running = Get-Service plenora-backend, plenora-worker, plenora-beat -ErrorAction SilentlyContinue |
               Where-Object Status -eq 'Running'
    foreach ($s in $running) { Restart-Service $s.Name }
    Ok "MinIO configurato: bucket plenora-runtime/plenora-backups, provider=minio, policy=$StoragePolicy"
}

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
    Start-Process cmd -ArgumentList '/k', "$prefix python -m dramatiq workers.dramatiq_app --processes $WorkerProcesses --threads $WorkerThreads"
    Start-Process cmd -ArgumentList '/k', "$prefix python -m workers.beat"
    Ok 'Tre finestre avviate (backend, worker, beat). Eseguire poi smoke_native.py in una quarta.'
}

# =============================================================================
# W1 — Servizi Windows via WinSW (piano installer produzione)
# =============================================================================
if ($InstallServices) {
    Step 'W1: servizi Windows (WinSW) — plenora-backend / plenora-worker / plenora-beat'

    # Env conda: un servizio non puo' fare `conda activate` — replichiamo
    # nel config del servizio il PATH (DLL GDAL in Library\bin) e le var
    # dei dati geospaziali che l'attivazione imposterebbe.
    $forgeRoot = Split-Path -Parent (Split-Path -Parent $condaBat)   # ...\miniforge3
    $envRoot = Join-Path $forgeRoot "envs\$CondaEnv"
    $pythonExe = Join-Path $envRoot 'python.exe'
    if (-not (Test-Path $pythonExe)) { Fail "python.exe non trovato in $envRoot" }
    $envPath = "$envRoot;$envRoot\Library\mingw-w64\bin;$envRoot\Library\usr\bin;$envRoot\Library\bin;$envRoot\Scripts;%PATH%"
    $gdalData = Join-Path $envRoot 'Library\share\gdal'
    $projData = Join-Path $envRoot 'Library\share\proj'

    # Nome reale del servizio PostgreSQL (EDB: postgresql-x64-17).
    $pgSvc = (Get-Service postgresql* | Select-Object -First 1).Name
    if (-not $pgSvc) { Fail 'Servizio PostgreSQL non trovato' }

    $services = @(
        @{
            id = 'plenora-backend'
            name = 'Plenora Backend'
            desc = 'Plenora-ETL API FastAPI (uvicorn)'
            args = '-m uvicorn app.main:app --host 127.0.0.1 --port 5004'
            depends = @($pgSvc)
        },
        @{
            id = 'plenora-worker'
            name = 'Plenora Worker'
            desc = "Plenora-ETL worker Dramatiq ($WorkerProcesses processi x $WorkerThreads thread)"
            args = "-m dramatiq workers.dramatiq_app --processes $WorkerProcesses --threads $WorkerThreads"
            depends = @($pgSvc, 'RabbitMQ')
        },
        @{
            id = 'plenora-beat'
            name = 'Plenora Beat'
            desc = 'Plenora-ETL beat scheduler (APScheduler, leader lock su PG)'
            args = '-m workers.beat'
            depends = @($pgSvc, 'RabbitMQ')
        }
    )

    foreach ($svc in $services) {
        $dependXml = ($svc.depends | ForEach-Object { "  <depend>$_</depend>" }) -join "`r`n"
        $xmlContent = @"
<service>
  <id>$($svc.id)</id>
  <name>$($svc.name)</name>
  <description>$($svc.desc)</description>
  <executable>$pythonExe</executable>
  <arguments>$($svc.args)</arguments>
  <workingdirectory>$BackendDir</workingdirectory>
  <env name="PATH" value="$envPath"/>
  <env name="GDAL_DATA" value="$gdalData"/>
  <env name="PROJ_DATA" value="$projData"/>
  <env name="PROJ_LIB" value="$projData"/>
$dependXml
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="5 sec"/>
  <!-- Stop: WinSW manda Ctrl+C al console app: uvicorn shutdown pulito,
       il beat rilascia il leader lock su PG (gestione SIGINT del runner). -->
  <stoptimeout>30 sec</stoptimeout>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>4</keepFiles>
  </log>
</service>
"@
        Install-WinSWService -Id $svc.id -Xml $xmlContent
    }

    # Avvio in ordine: backend (fa le migrations) -> worker -> beat.
    foreach ($id in 'plenora-backend', 'plenora-worker', 'plenora-beat') {
        Start-Service $id
        Start-Sleep -Seconds 5
    }
    Start-Sleep -Seconds 10
    Get-Service plenora-* | Format-Table Status, Name, DisplayName -AutoSize
    Write-Host '  Log dei servizi: C:\plenora-services\<id>\<id>.out.log / .err.log' -ForegroundColor Cyan
    Ok 'W1 completata: 3 servizi Windows attivi (avvio automatico al boot)'
}

# =============================================================================
# W2 — Bootstrap superadmin (parita' con install-docker.sh)
# =============================================================================
if (-not $SkipAdminBootstrap) {
    Step 'W2: bootstrap superadmin'

    # Prompt interattivo se le credenziali non arrivano da parametri/env.
    if (-not $AdminUsername) {
        $AdminUsername = Read-Host 'Superadmin username [admin]'
        if (-not $AdminUsername) { $AdminUsername = 'admin' }
    }
    while (-not $AdminPassword) {
        $p1 = Read-Host 'Superadmin password (min 12 caratteri)' -AsSecureString
        $p2 = Read-Host 'Conferma superadmin password' -AsSecureString
        $s1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p1))
        $s2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p2))
        if ($s1 -ne $s2) { Write-Host '  Le password non coincidono: riprova.' -ForegroundColor Yellow; continue }
        if ($s1.Length -lt 12) { Write-Host '  Minimo 12 caratteri: riprova.' -ForegroundColor Yellow; continue }
        $AdminPassword = $s1
    }
    if ($AdminPassword.Length -lt 12) { Fail 'superadmin password: minimo 12 caratteri' }

    # Il seed richiede il backend healthy (migrations applicate): poll
    # /ready fino a 90s. Con -InstallServices il backend e' un servizio
    # gia' avviato; senza, deve essere su per altra via.
    $ready = $false
    foreach ($i in 1..30) {
        try {
            $resp = Invoke-RestMethod 'http://127.0.0.1:5004/ready' -TimeoutSec 3
            if ($resp.status -eq 'ready') { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 3
    }
    if (-not $ready) {
        Fail 'backend non ready entro 90s: avviare i servizi (-InstallServices) e rilanciare il bootstrap admin'
    }

    # Stesso script python dell'installer docker (idempotente: crea o
    # aggiorna password/ruolo/gruppo), eseguito nell'env conda locale.
    $seedScript = @'
import os, uuid

# Bare-metal: DATABASE_URL vive nel .env — l'installer docker esegue lo
# stesso snippet DENTRO il container (env dal compose), qui va caricato
# il dotenv prima di toccare il DB (stessa lezione del worker dramatiq).
from app.config_env_loader import load_backend_dotenv
load_backend_dotenv()

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
    $seedFile = Join-Path $BackendDir '_bootstrap_admin.py'
    $seedScript | Set-Content -Encoding ascii $seedFile
    $env:PLENORA_BOOTSTRAP_ADMIN_USERNAME = $AdminUsername
    $env:PLENORA_BOOTSTRAP_ADMIN_PASSWORD = $AdminPassword
    $env:PLENORA_BOOTSTRAP_ADMIN_GROUP_ID = $AdminGroupId
    try {
        & $condaBat run -n $CondaEnv --cwd $BackendDir python _bootstrap_admin.py
        if ($LASTEXITCODE -ne 0) { Fail "seed superadmin exit=$LASTEXITCODE" }
    } finally {
        Remove-Item $seedFile -ErrorAction SilentlyContinue
        Remove-Item Env:\PLENORA_BOOTSTRAP_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    }

    # Verifica: login reale via API (fallisce loud se il seed non ha preso).
    try {
        $login = Invoke-RestMethod -Method Post 'http://127.0.0.1:5004/api/auth/login' `
            -ContentType 'application/json' `
            -Body (@{ username = $AdminUsername; password = $AdminPassword } | ConvertTo-Json)
        if ($login.access_token) {
            Ok "Superadmin '$AdminUsername' verificato: login API ok"
        } else {
            Write-Host '  Login ok ma senza access_token nella risposta: verificare a mano dal FE.' -ForegroundColor Yellow
        }
    } catch {
        Fail "login di verifica fallito: $($_.Exception.Message)"
    }
    $AdminPassword = ''
}
