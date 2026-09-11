# =============================================================================
# Plenora-ETL — Installer nativo Windows (single-box)
# =============================================================================
#
# Collaudato end-to-end su Windows Server 2025 (2026-07-04): stack
# completo PostgreSQL + Erlang/RabbitMQ + Miniforge/conda (GDAL nativo)
# + worker Dramatiq + beat APScheduler + SeaweedFS S3 + frontend via
# Caddy, tutto come SERVIZI Windows. Runbook: docs/ops/install-windows.md;
# sessione di scoping e lezioni: docs/ops/scoping-windows-native.md.
# `scripts/install.ps1` inoltra qui.
#
# Uso tipico (PowerShell da AMMINISTRATORE, dalla root del repo):
#   1) powershell -ExecutionPolicy Bypass -File scripts\install-native.ps1
#      (pacchetti piattaforma via winget; poi NUOVA shell admin)
#   2) powershell -ExecutionPolicy Bypass -File scripts\install-native.ps1 `
#          -SkipWinget -InstallServices -InstallFrontend -Production
#
# Parametri principali: -WorkerProcesses/-WorkerThreads,
# -SplitWorkerServices/-DataWorkerServices/-DataWorkerThreads,
# -AdminUsername/-AdminPassword (-SkipAdminBootstrap),
# -FrontendPort, -PgSuperPassword. Non-interattivo via env
# PLENORA_BOOTSTRAP_ADMIN_* / PLENORA_INSTALL_WORKER_*.
#
# Ogni step stampa [STEP]/[OK]/[FAIL] e si ferma al primo errore.
# NB encoding: file UTF-8 CON BOM (PS 5.1 senza BOM legge ANSI e le
# em-dash nelle stringhe diventano virgolette smart che le CHIUDONO).
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
    [int]$WorkerThreads = 1,
    [switch]$SplitWorkerServices = $true,
    [int]$DataWorkerServices = 1,
    [int]$DataWorkerThreads = 1,
    [int]$ControlWorkerThreads = 2,
    [int]$WorkerQueuePrefetch = 1,
    [int]$WorkerDelayQueuePrefetch = 1,
    # W2 — bootstrap superadmin (parita' con install-docker.sh).
    # Fallback env: PLENORA_BOOTSTRAP_ADMIN_USERNAME / _PASSWORD / _GROUP_ID.
    [string]$AdminUsername = '',
    [string]$AdminPassword = '',
    [string]$AdminGroupId = '',
    [switch]$SkipAdminBootstrap,
    # W3 — object storage locale S3-compatible (SeaweedFS/WinSW).
    [switch]$InstallObjectStorage = $true,
    # Alias deprecato: mantenuto per non rompere vecchi comandi operativi.
    [switch]$InstallMinio,
    # W4 — frontend: build Vite + Caddy come servizio (statici + reverse
    # proxy /api verso il backend).
    [switch]$InstallFrontend,
    [int]$FrontendPort = 80,
    [string]$FrontendApiUrl = '',
    # Acceleratore XLSX nativo. Se il path non e' specificato, l'installer
    # cerca il pacchetto piu' recente in C:\deploy e nella root del repo.
    [string]$XlsxExporterArchive = '',
    [string]$XlsxExporterChecksum = '',
    [switch]$SkipXlsxExporter,
    # Deploy rapido: installa/aggiorna solo l'exporter e riavvia gli eventuali
    # servizi applicativi, senza rieseguire PostgreSQL/conda/frontend.
    [switch]$XlsxExporterOnly,
    # W5 — switch a ENVIRONMENT=production coi gates attivi (richiede
    # object storage configurato localmente o tramite endpoint S3 nel .env).
    [switch]$Production
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$envFile = Join-Path $BackendDir '.env'
$DataDir = 'C:\plenora-data'
$CondaEnv = 'plenora'

# Fallback non-interattivi (stesse env dell'installer docker/linux).
if (-not $AdminUsername) { $AdminUsername = $env:PLENORA_BOOTSTRAP_ADMIN_USERNAME }
if (-not $AdminPassword) { $AdminPassword = $env:PLENORA_BOOTSTRAP_ADMIN_PASSWORD }
if (-not $AdminGroupId) {
    $AdminGroupId = if ($env:PLENORA_BOOTSTRAP_ADMIN_GROUP_ID) { $env:PLENORA_BOOTSTRAP_ADMIN_GROUP_ID } else { 'root' }
}
if (-not $XlsxExporterArchive -and $env:PLENORA_INSTALL_XLSX_EXPORTER_ARCHIVE) {
    $XlsxExporterArchive = $env:PLENORA_INSTALL_XLSX_EXPORTER_ARCHIVE
}
if (-not $XlsxExporterChecksum -and $env:PLENORA_INSTALL_XLSX_EXPORTER_CHECKSUM) {
    $XlsxExporterChecksum = $env:PLENORA_INSTALL_XLSX_EXPORTER_CHECKSUM
}
if ($InstallMinio) {
    Write-Warning '-InstallMinio e'' deprecato: viene installato SeaweedFS tramite -InstallObjectStorage.'
    $InstallObjectStorage = $true
}
if (-not $PSBoundParameters.ContainsKey('WorkerProcesses') -and $env:PLENORA_INSTALL_WORKER_PROCESSES) {
    $WorkerProcesses = [int]$env:PLENORA_INSTALL_WORKER_PROCESSES
}
if (-not $PSBoundParameters.ContainsKey('WorkerThreads') -and $env:PLENORA_INSTALL_WORKER_THREADS) {
    $WorkerThreads = [int]$env:PLENORA_INSTALL_WORKER_THREADS
}
if (-not $PSBoundParameters.ContainsKey('SplitWorkerServices') -and $env:PLENORA_INSTALL_SPLIT_WORKERS) {
    $SplitWorkerServices = $env:PLENORA_INSTALL_SPLIT_WORKERS -match '^(1|true|yes)$'
}
if (-not $PSBoundParameters.ContainsKey('DataWorkerServices') -and $env:PLENORA_INSTALL_DATA_WORKER_SERVICES) {
    $DataWorkerServices = [int]$env:PLENORA_INSTALL_DATA_WORKER_SERVICES
}
if (-not $PSBoundParameters.ContainsKey('DataWorkerThreads') -and $env:PLENORA_INSTALL_DATA_WORKER_THREADS) {
    $DataWorkerThreads = [int]$env:PLENORA_INSTALL_DATA_WORKER_THREADS
}
if (-not $PSBoundParameters.ContainsKey('ControlWorkerThreads') -and $env:PLENORA_INSTALL_CONTROL_WORKER_THREADS) {
    $ControlWorkerThreads = [int]$env:PLENORA_INSTALL_CONTROL_WORKER_THREADS
}
if (-not $PSBoundParameters.ContainsKey('WorkerQueuePrefetch') -and $env:PLENORA_INSTALL_WORKER_QUEUE_PREFETCH) {
    $WorkerQueuePrefetch = [int]$env:PLENORA_INSTALL_WORKER_QUEUE_PREFETCH
}
if (-not $PSBoundParameters.ContainsKey('WorkerDelayQueuePrefetch') -and $env:PLENORA_INSTALL_WORKER_DELAY_QUEUE_PREFETCH) {
    $WorkerDelayQueuePrefetch = [int]$env:PLENORA_INSTALL_WORKER_DELAY_QUEUE_PREFETCH
}

function Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

function New-RandomSecret {
    # 48 caratteri alfanumerici via RNG crittografico .NET. Il rejection
    # sampling evita il bias del semplice byte modulo 62.
    $alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    $buffer = New-Object byte[] 64
    $result = New-Object System.Text.StringBuilder
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        while ($result.Length -lt 48) {
            $rng.GetBytes($buffer)
            foreach ($value in $buffer) {
                if ($value -lt 248 -and $result.Length -lt 48) {
                    [void]$result.Append($alphabet[$value % 62])
                }
            }
        }
    } finally {
        $rng.Dispose()
    }
    $result.ToString()
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

function Stop-PlenoraAppServicesForDependencyUpdate {
    # I moduli nativi (pyarrow/pyogrio) restano lockati finche' backend e
    # worker sono in esecuzione. Arrestiamo ogni servizio UNA sola volta,
    # in ordine applicativo, prima di modificare l'env conda.
    $installed = @(Get-Service -Name 'plenora-backend','plenora-worker*','plenora-beat' -ErrorAction SilentlyContinue)
    if ($installed.Count -eq 0) { return @() }

    $installedByName = @{}
    foreach ($service in $installed) { $installedByName[$service.Name] = $service }
    $orderedIds = @('plenora-beat') + @(
        $installedByName.Keys |
            Where-Object { $_ -like 'plenora-worker*' } |
            Sort-Object
    ) + @('plenora-backend')

    $stopped = @()
    foreach ($id in ($orderedIds | Select-Object -Unique)) {
        $service = $installedByName[$id]
        if (-not $service -or $service.Status -ne 'Running') { continue }
        Write-Host "  Arresto $id per aggiornamento dipendenze..."
        Stop-Service -Name $id -Force -ErrorAction Stop
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(45))
        $stopped += $id
    }
    return @($stopped)
}

function Start-PlenoraAppServicesAfterDependencyFailure {
    param([string[]]$ServiceIds)
    # Recovery best-effort: se pip/import falliscono, non lasciare una
    # installazione precedentemente funzionante completamente spenta.
    $ordered = @('plenora-backend') + @(
        $ServiceIds | Where-Object { $_ -like 'plenora-worker*' } | Sort-Object
    ) + @('plenora-beat')
    foreach ($id in ($ordered | Select-Object -Unique)) {
        if ($ServiceIds -notcontains $id) { continue }
        try { Start-Service -Name $id -ErrorAction Stop }
        catch { Write-Warning "Ripristino servizio $id fallito: $($_.Exception.Message)" }
    }
}

function Remove-StalePipDirectories {
    param([string]$CondaEnvRoot)
    $sitePackages = Join-Path $CondaEnvRoot 'Lib\site-packages'
    if (-not (Test-Path -LiteralPath $sitePackages)) { return }
    $resolvedSitePackages = (Resolve-Path -LiteralPath $sitePackages).Path
    foreach ($directory in @(Get-ChildItem -LiteralPath $resolvedSitePackages -Directory -Filter '~*' -ErrorAction SilentlyContinue)) {
        if ($directory.Parent.FullName -ne $resolvedSitePackages) {
            Write-Warning "Directory pip temporanea fuori scope ignorata: $($directory.FullName)"
            continue
        }
        try {
            Remove-Item -LiteralPath $directory.FullName -Recurse -Force -ErrorAction Stop
            Write-Host "  Rimossa directory pip temporanea: $($directory.Name)"
        } catch {
            Write-Warning "Impossibile rimuovere $($directory.FullName): $($_.Exception.Message)"
        }
    }
}

function Resolve-XlsxExporterArchive {
    param([string]$ExplicitArchive)

    if ($ExplicitArchive) {
        if (-not (Test-Path -LiteralPath $ExplicitArchive -PathType Leaf)) {
            throw "Archivio XLSX exporter non trovato: $ExplicitArchive"
        }
        return (Resolve-Path -LiteralPath $ExplicitArchive).Path
    }

    $candidates = @()
    foreach ($directory in @('C:\deploy', $RepoRoot)) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) { continue }
        $candidates += @(Get-ChildItem -LiteralPath $directory -File `
            -Filter 'plenora-xlsx-exporter-*-windows-x64.zip' `
            -ErrorAction SilentlyContinue)
    }
    $selected = $candidates | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if ($selected) { return $selected.FullName }
    return $null
}

function Install-XlsxExporter {
    param(
        [Parameter(Mandatory=$true)][string]$ArchivePath,
        [string]$ChecksumPath
    )

    $archive = (Resolve-Path -LiteralPath $ArchivePath).Path
    if (-not $ChecksumPath) {
        $ChecksumPath = [IO.Path]::ChangeExtension($archive, '.sha256')
    }
    if (-not (Test-Path -LiteralPath $ChecksumPath -PathType Leaf)) {
        throw "Checksum XLSX exporter non trovato: $ChecksumPath"
    }
    $checksum = (Resolve-Path -LiteralPath $ChecksumPath).Path
    $checksumText = (Get-Content -LiteralPath $checksum -Raw).Trim()
    $checksumMatch = [regex]::Match(
        $checksumText,
        '^(?i)([0-9a-f]{64})(?:\s+\*?(.+?))?$'
    )
    if (-not $checksumMatch.Success) {
        throw "Formato checksum XLSX exporter non valido: $checksum"
    }
    $listedName = $checksumMatch.Groups[2].Value.Trim()
    if ($listedName -and ([IO.Path]::GetFileName($listedName) -ne [IO.Path]::GetFileName($archive))) {
        throw "Il checksum dichiara '$listedName', non '$([IO.Path]::GetFileName($archive))'"
    }
    $expectedHash = $checksumMatch.Groups[1].Value.ToLowerInvariant()
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "Checksum XLSX exporter non valido: atteso $expectedHash, ottenuto $actualHash"
    }

    $extractDir = Join-Path $env:TEMP ("plenora-xlsx-exporter-" + [guid]::NewGuid().ToString('N'))
    $selfTestFile = Join-Path $extractDir 'self-test.xlsx'
    try {
        New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
        Expand-Archive -LiteralPath $archive -DestinationPath $extractDir -Force
        $executables = @(Get-ChildItem -LiteralPath $extractDir -Recurse -File `
            -Filter 'plenora-xlsx-exporter.exe')
        if ($executables.Count -ne 1) {
            throw "Archivio XLSX exporter non valido: atteso un EXE, trovati $($executables.Count)"
        }
        $candidate = $executables[0].FullName
        $version = (& $candidate --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $version -notmatch '^plenora-xlsx-exporter\s+\S+$') {
            throw "Version check XLSX exporter fallito: $version"
        }
        $selfTestOutput = & $candidate self-test --output $selfTestFile 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Self-test XLSX exporter fallito: $($selfTestOutput -join ' ')"
        }
        if (-not (Test-Path -LiteralPath $selfTestFile -PathType Leaf) -or
            (Get-Item -LiteralPath $selfTestFile).Length -lt 22) {
            throw 'Self-test XLSX exporter non ha prodotto un file XLSX valido'
        }
        $signature = Get-Content -LiteralPath $selfTestFile -Encoding Byte -TotalCount 2
        if ($signature.Count -ne 2 -or $signature[0] -ne 0x50 -or $signature[1] -ne 0x4b) {
            throw 'Self-test XLSX exporter ha prodotto un contenitore non valido'
        }

        $binDir = Join-Path $BackendDir 'bin'
        $target = Join-Path $binDir 'plenora-xlsx-exporter.exe'
        $backup = Join-Path $binDir 'plenora-xlsx-exporter.previous.exe'
        New-Item -ItemType Directory -Path $binDir -Force | Out-Null
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            Copy-Item -LiteralPath $target -Destination $backup -Force
        }
        try {
            Copy-Item -LiteralPath $candidate -Destination $target -Force
            $installedVersion = (& $target --version 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0 -or $installedVersion -ne $version) {
                throw "Verifica exporter installato fallita: $installedVersion"
            }
        } catch {
            if (Test-Path -LiteralPath $backup -PathType Leaf) {
                Copy-Item -LiteralPath $backup -Destination $target -Force
            } else {
                Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
            }
            throw
        } finally {
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        }
        Ok "XLSX exporter installato: $installedVersion (SHA-256 $actualHash)"
    } finally {
        if (Test-Path -LiteralPath $extractDir) {
            Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-WinSWExe {
    # WinSW.exe condiviso (W1 servizi app + W3 object storage): scarica
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

function Remove-WinSWService {
    param([string]$Id)
    $service = Get-Service -Name $Id -ErrorAction SilentlyContinue
    if (-not $service) { return }
    Write-Host "  ${Id}: servizio worker non piu' desiderato - stop + uninstall"
    Stop-Service $Id -Force -ErrorAction SilentlyContinue
    $exe = Join-Path (Join-Path $ServicesRoot $Id) "$Id.exe"
    if (Test-Path $exe) {
        & $exe uninstall 2>$null | Out-Null
    } else {
        & sc.exe delete $Id | Out-Null
    }
}

function ConvertTo-ServiceEnvXml {
    param([hashtable]$Env)
    if (-not $Env) { return '' }
    return (($Env.GetEnumerator() | Sort-Object Name | ForEach-Object {
        $name = [System.Security.SecurityElement]::Escape([string]$_.Name)
        $value = [System.Security.SecurityElement]::Escape([string]$_.Value)
        "  <env name=""$name"" value=""$value""/>"
    }) -join "`r`n")
}

if ($WorkerProcesses -lt 1) { Fail '-WorkerProcesses deve essere >= 1' }
if ($WorkerThreads -lt 1) { Fail '-WorkerThreads deve essere >= 1' }
if ($DataWorkerServices -lt 1) { Fail '-DataWorkerServices deve essere >= 1' }
if ($DataWorkerThreads -lt 1) { Fail '-DataWorkerThreads deve essere >= 1' }
if ($ControlWorkerThreads -lt 1) { Fail '-ControlWorkerThreads deve essere >= 1' }
if ($WorkerQueuePrefetch -lt 1) { Fail '-WorkerQueuePrefetch deve essere >= 1' }
if ($WorkerDelayQueuePrefetch -lt 1) { Fail '-WorkerDelayQueuePrefetch deve essere >= 1' }

if ($XlsxExporterOnly) {
    Step 'Deploy rapido acceleratore XLSX nativo'
    if ($SkipXlsxExporter) { Fail '-XlsxExporterOnly non e'' compatibile con -SkipXlsxExporter' }
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
               ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) { Fail 'Servono privilegi di Amministratore per riavviare i servizi Plenora.' }
    try {
        $resolvedXlsxArchive = Resolve-XlsxExporterArchive -ExplicitArchive $XlsxExporterArchive
        if (-not $resolvedXlsxArchive) {
            throw 'Pacchetto non trovato: mettere ZIP e .sha256 in C:\deploy oppure specificare -XlsxExporterArchive.'
        }
        $xlsxUpdateStoppedIds = @(Stop-PlenoraAppServicesForDependencyUpdate)
        try {
            Install-XlsxExporter -ArchivePath $resolvedXlsxArchive `
                -ChecksumPath $XlsxExporterChecksum
        } finally {
            Start-PlenoraAppServicesAfterDependencyFailure -ServiceIds $xlsxUpdateStoppedIds
        }
    } catch {
        Fail $_.Exception.Message
    }
    Ok 'Deploy rapido XLSX completato; servizi precedentemente attivi riavviati'
    exit 0
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
Step 'Microsoft ODBC Driver 18 per SQL Server'
# -----------------------------------------------------------------------------
$odbc18 = @(Get-OdbcDriver -Platform '64-bit' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'ODBC Driver 18 for SQL Server' })
if ($odbc18.Count -eq 0) {
    Write-Host '  Scarico Microsoft ODBC Driver 18 x64 (release ufficiale)...'
    $odbcMsi = Join-Path $env:TEMP 'msodbcsql18-x64.msi'
    Invoke-WebRequest 'https://go.microsoft.com/fwlink/?linkid=2358430' -OutFile $odbcMsi
    $odbcInstall = Start-Process msiexec.exe -Wait -PassThru -ArgumentList @(
        '/i', "`"$odbcMsi`"", '/quiet', '/norestart', 'IACCEPTMSODBCSQLLICENSETERMS=YES'
    )
    if ($odbcInstall.ExitCode -notin 0, 3010) {
        Fail "Microsoft ODBC Driver 18 install exit=$($odbcInstall.ExitCode)"
    }
    $odbc18 = @(Get-OdbcDriver -Platform '64-bit' -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq 'ODBC Driver 18 for SQL Server' })
}
if ($odbc18.Count -eq 0) { Fail 'ODBC Driver 18 for SQL Server non risulta installato' }
Ok 'Microsoft ODBC Driver 18 for SQL Server disponibile'

# -----------------------------------------------------------------------------
Step 'PostgreSQL: utente e database plenora'
# -----------------------------------------------------------------------------
$psql = 'C:\Program Files\PostgreSQL\17\bin\psql.exe'
if (-not (Test-Path $psql)) { Fail "psql non trovato in $psql (install PostgreSQL fallita o path diverso)" }
if (-not $PgSuperPassword) {
    Write-Host "  Con installazione winget unattended la password iniziale EDB e' normalmente: postgres" -ForegroundColor Yellow
    $sec = Read-Host 'Password corrente del superuser postgres' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try {
        $PgSuperPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
$env:PGPASSWORD = $PgSuperPassword
$authResult = (& $psql -w -U postgres -h 127.0.0.1 -d postgres -tAc 'SELECT 1' 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $authResult -ne '1') {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    Fail "Autenticazione postgres fallita. Con winget unattended provare 'postgres'; se non funziona seguire il reset in docs/ops/install-windows.md"
}

# Password applicativa random. Se esiste gia' un .env valido la riusiamo;
# il vecchio default debole "plenora" viene ruotato automaticamente.
$dbPassword = ''
if (Test-Path $envFile) {
    $existingEnv = Get-Content $envFile -Raw
    if ($existingEnv -match '(?m)^DATABASE_URL=postgresql://plenora:([^@\r\n]+)@127\.0\.0\.1:5432/plenora\s*$') {
        $dbPassword = $Matches[1]
    }
}
if (-not $dbPassword -or $dbPassword -eq 'plenora') {
    $dbPassword = New-RandomSecret
}

$roleExists = (& $psql -w -U postgres -h 127.0.0.1 -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='plenora'" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail "Verifica ruolo plenora fallita: $roleExists" }
if ($roleExists -eq '1') {
    $roleOutput = (& $psql -w -U postgres -h 127.0.0.1 -d postgres -c "ALTER ROLE plenora WITH PASSWORD '$dbPassword' CREATEDB;" 2>&1 | Out-String).Trim()
} else {
    $roleOutput = (& $psql -w -U postgres -h 127.0.0.1 -d postgres -c "CREATE USER plenora WITH PASSWORD '$dbPassword' CREATEDB;" 2>&1 | Out-String).Trim()
}
if ($LASTEXITCODE -ne 0) { Fail "Creazione/aggiornamento ruolo plenora fallita: $roleOutput" }

$dbExists = (& $psql -w -U postgres -h 127.0.0.1 -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='plenora'" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail "Verifica database plenora fallita: $dbExists" }
if ($dbExists -ne '1') {
    $dbOutput = (& $psql -w -U postgres -h 127.0.0.1 -d postgres -c 'CREATE DATABASE plenora OWNER plenora;' 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { Fail "Creazione database plenora fallita: $dbOutput" }
}
$env:PGPASSWORD = $dbPassword
$one = (& $psql -w -U plenora -h 127.0.0.1 -d plenora -tAc 'SELECT 1' 2>&1 | Out-String).Trim()
Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0 -or $one -ne '1') { Fail "SELECT 1 come utente plenora fallita: $one" }

# L'installer EDB unattended usa "postgres" come password iniziale. Non
# deve sopravvivere a un'installazione Plenora: la ruotiamo subito e non la
# salviamo nel .env, perche' l'app usa esclusivamente il ruolo limitato.
if ($PgSuperPassword -eq 'postgres') {
    Write-Host '  La password PostgreSQL predefinita deve essere sostituita.' -ForegroundColor Yellow
    $newSec = Read-Host 'Nuova password robusta del superuser postgres (minimo 12 caratteri)' -AsSecureString
    $confirmSec = Read-Host 'Conferma nuova password del superuser postgres' -AsSecureString
    $newBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($newSec)
    $confirmBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($confirmSec)
    try {
        $newPgPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($newBstr)
        $confirmPgPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($confirmBstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($newBstr)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($confirmBstr)
    }
    if ($newPgPassword.Length -lt 12) { Fail 'La nuova password postgres deve avere almeno 12 caratteri' }
    if ($newPgPassword -ne $confirmPgPassword) { Fail 'Le password postgres non coincidono' }
    $escapedPgPassword = $newPgPassword.Replace("'", "''")
    $env:PGPASSWORD = $PgSuperPassword
    $rotateOutput = (& $psql -w -U postgres -h 127.0.0.1 -d postgres -c "ALTER ROLE postgres WITH PASSWORD '$escapedPgPassword';" 2>&1 | Out-String).Trim()
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { Fail "Rotazione password postgres fallita: $rotateOutput" }
    $PgSuperPassword = $newPgPassword
    Ok 'Password predefinita del superuser postgres sostituita'
}
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
    # Non usare -Wait: alcune release NSIS lasciano vivo il bootstrapper
    # anche quando servizio e broker sono gia' operativi. Aspettiamo invece
    # l'outcome reale con un timeout bounded.
    $rabbitInstaller = Start-Process $exe -ArgumentList '/S' -PassThru
    $rabbitDeadline = (Get-Date).AddMinutes(5)
    do {
        Start-Sleep -Seconds 2
        $svc = Get-Service -Name 'RabbitMQ' -ErrorAction SilentlyContinue
        if ($svc) { break }
        if ($rabbitInstaller.HasExited -and $rabbitInstaller.ExitCode -ne 0) {
            Fail "Installer RabbitMQ exit=$($rabbitInstaller.ExitCode)"
        }
    } while ((Get-Date) -lt $rabbitDeadline)
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
        python=3.12 gdal=3.13.0 proj-data pyogrio=0.13.0 geopandas-base=1.1.4 `
        pyproj=3.7.2 shapely=2.1.2 pandas=3.0.3 numpy=2.5.1 `
        pyarrow=25.0.0 openpyxl=3.1.5 pip
    if ($LASTEXITCODE -ne 0) { Fail 'conda create fallita (annotare output resolver)' }
}
$gdalVer = & $condaBat run -n $CondaEnv gdalinfo --version
if ($LASTEXITCODE -ne 0) { Fail "gdalinfo non parte nell'env (DLL? provare pin libjxl)" }
Ok "Env conda pronto: $gdalVer"

# -----------------------------------------------------------------------------
Step 'Dipendenze pip dal lock verificato'
# -----------------------------------------------------------------------------
$requirementsLock = Join-Path $BackendDir 'requirements.lock'
if (-not (Test-Path $requirementsLock)) { Fail 'requirements.lock mancante' }
$stoppedAppServiceIds = @()
if ($InstallServices) {
    $stoppedAppServiceIds = @(Stop-PlenoraAppServicesForDependencyUpdate)
    if ($stoppedAppServiceIds.Count -gt 0) {
        Ok "Servizi applicativi arrestati una sola volta: $($stoppedAppServiceIds -join ', ')"
    }
}
& $condaBat run -n $CondaEnv python -m pip install --require-hashes -r $requirementsLock
if ($LASTEXITCODE -ne 0) {
    Start-PlenoraAppServicesAfterDependencyFailure -ServiceIds $stoppedAppServiceIds
    Fail 'pip install fallita (annotare il pacchetto incriminato)'
}
& $condaBat run -n $CondaEnv python -c "import fastapi, dramatiq, psycopg, pika, apscheduler, croniter; print('deps ok')"
if ($LASTEXITCODE -ne 0) {
    Start-PlenoraAppServicesAfterDependencyFailure -ServiceIds $stoppedAppServiceIds
    Fail 'import check fallito'
}
$forgeRootForCleanup = Split-Path -Parent (Split-Path -Parent $condaBat)
Remove-StalePipDirectories -CondaEnvRoot (Join-Path $forgeRootForCleanup "envs\$CondaEnv")
Ok 'Dipendenze python complete'

# -----------------------------------------------------------------------------
Step 'Acceleratore XLSX nativo (opzionale, con verifica SHA-256)'
# -----------------------------------------------------------------------------
if ($SkipXlsxExporter) {
    Write-Host '  Skip richiesto: resta disponibile il writer XLSX Python.' -ForegroundColor Yellow
} else {
    try {
        $resolvedXlsxArchive = Resolve-XlsxExporterArchive -ExplicitArchive $XlsxExporterArchive
    } catch {
        Start-PlenoraAppServicesAfterDependencyFailure -ServiceIds $stoppedAppServiceIds
        Fail $_.Exception.Message
    }
    if ($resolvedXlsxArchive) {
        $xlsxUpdateStoppedIds = @()
        if (-not $InstallServices) {
            $xlsxUpdateStoppedIds = @(Stop-PlenoraAppServicesForDependencyUpdate)
        }
        try {
            Install-XlsxExporter -ArchivePath $resolvedXlsxArchive `
                -ChecksumPath $XlsxExporterChecksum
        } catch {
            Start-PlenoraAppServicesAfterDependencyFailure -ServiceIds $xlsxUpdateStoppedIds
            Start-PlenoraAppServicesAfterDependencyFailure -ServiceIds $stoppedAppServiceIds
            Fail $_.Exception.Message
        }
        if (-not $InstallServices) {
            Start-PlenoraAppServicesAfterDependencyFailure -ServiceIds $xlsxUpdateStoppedIds
        }
    } else {
        $installedXlsxExporter = Join-Path $BackendDir 'bin\plenora-xlsx-exporter.exe'
        if (Test-Path -LiteralPath $installedXlsxExporter -PathType Leaf) {
            $installedXlsxVersion = (& $installedXlsxExporter --version 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                Start-PlenoraAppServicesAfterDependencyFailure -ServiceIds $stoppedAppServiceIds
                Fail "XLSX exporter esistente non eseguibile: $installedXlsxVersion"
            }
            Ok "XLSX exporter gia' installato: $installedXlsxVersion"
        } else {
            Write-Host '  Pacchetto non trovato: writer Python attivo. Mettere ZIP e .sha256 in C:\deploy e rilanciare.' -ForegroundColor Yellow
        }
    }
}

# -----------------------------------------------------------------------------
Step "Configurazione .env + DATA_DIR ($DataDir)"
# -----------------------------------------------------------------------------
if (Test-Path $envFile) {
    Set-EnvValue -File $envFile -Key 'DATABASE_URL' -Value "postgresql://plenora:$dbPassword@127.0.0.1:5432/plenora"
    Ok '.env gia'' presente: credenziali PostgreSQL sincronizzate; resto invariato'
} else {
    $jwt = New-RandomSecret
    $enc = New-RandomSecret
    @"
# Generato da install-native.ps1 (scoping) — $(Get-Date -Format 'yyyy-MM-dd HH:mm')
ENVIRONMENT=development
DATABASE_URL=postgresql://plenora:$dbPassword@127.0.0.1:5432/plenora
DB_SCHEMA=plenora
DRAMATIQ_BROKER_URL=amqp://guest:guest@127.0.0.1:5672
JWT_SECRET_KEY=$jwt
PLENORA_ENCRYPTION_KEY=$enc
DATA_DIR=$DataDir
# PostgreSQL e' solo control-plane; ogni payload persistente usa S3.
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BACKEND=s3_compatible
OBJECT_STORAGE_BUCKET=plenora-runtime
PLENORA_ARTIFACT_BUCKET=plenora-runtime
BACKUP_BUCKET=plenora-backups
CLOUD_PROVIDER=none
"@ | Set-Content -Encoding ascii $envFile
    Ok '.env generato (secrets random; ENVIRONMENT=development per lo scoping)'
}
# Readiness/lease operative anche sul percorso nativo. Set-EnvValue rende
# l'upgrade idempotente per installazioni create da versioni precedenti.
Set-EnvValue -File $envFile -Key 'READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS' -Value '180'
Set-EnvValue -File $envFile -Key 'READINESS_WORKER_HEARTBEAT_MAX_AGE_SECONDS' -Value '45'
Set-EnvValue -File $envFile -Key 'WORKER_HEARTBEAT_INTERVAL_SECONDS' -Value '10'
Set-EnvValue -File $envFile -Key 'TASK_EXECUTION_LEASE_SECONDS' -Value '45'
Set-EnvValue -File $envFile -Key 'READINESS_TASK_DLQ_MAX_MESSAGES' -Value '0'
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
# W3 — SeaweedFS (object storage locale S3-compatible come servizio Windows)
# =============================================================================
if ($InstallObjectStorage) {
    Step 'W3: SeaweedFS come servizio Windows (S3-compatible data-plane)'

    $storageSvcDir = Join-Path $ServicesRoot 'plenora-object-storage'
    New-Item -ItemType Directory -Force $storageSvcDir | Out-Null
    $weedExe = Join-Path $storageSvcDir 'weed.exe'
    if (-not (Test-Path $weedExe)) {
        Write-Host '  Scarico SeaweedFS 4.29 (release ufficiale windows-amd64)...'
        $archive = Join-Path $env:TEMP 'seaweedfs-windows-amd64.zip'
        $extractDir = Join-Path $env:TEMP 'seaweedfs-windows-amd64'
        Invoke-WebRequest 'https://github.com/seaweedfs/seaweedfs/releases/download/4.29/windows_amd64.zip' -OutFile $archive
        $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        $expectedHash = 'a5a343f2e2249b4e709842b846e596330a316e064b15f9d77899581ea545cb9b'
        if ($actualHash -ne $expectedHash) {
            Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
            Fail "Checksum SeaweedFS non valido: $actualHash"
        }
        if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
        Expand-Archive -LiteralPath $archive -DestinationPath $extractDir -Force
        $downloadedWeed = Get-ChildItem $extractDir -Recurse -Filter weed.exe | Select-Object -First 1
        if (-not $downloadedWeed) { Fail 'weed.exe non trovato nell''archivio SeaweedFS' }
        Copy-Item $downloadedWeed.FullName $weedExe -Force
        Remove-Item $archive -Force -ErrorAction SilentlyContinue
        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    $storageData = Join-Path $DataDir 'object-storage'
    New-Item -ItemType Directory -Force $storageData | Out-Null

    # Credenziali: riusa il .env per rendere i rerun idempotenti.
    $storageUser = 'plenora-storage'
    $storageSecret = ''
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^S3_ACCESS_KEY=(.+)$') { $storageUser = $Matches[1] }
        if ($line -match '^S3_SECRET_KEY=(.+)$') { $storageSecret = $Matches[1] }
    }
    if (-not $storageSecret) { $storageSecret = New-RandomSecret }

    $storageXml = @"
<service>
  <id>plenora-object-storage</id>
  <name>Plenora Object Storage</name>
  <description>Object storage locale S3-compatible per Plenora-ETL</description>
  <executable>$weedExe</executable>
  <arguments>mini -dir=$storageData -ip=127.0.0.1</arguments>
  <workingdirectory>$storageSvcDir</workingdirectory>
  <env name="AWS_ACCESS_KEY_ID" value="$storageUser"/>
  <env name="AWS_SECRET_ACCESS_KEY" value="$storageSecret"/>
  <env name="S3_BUCKET" value="plenora-runtime"/>
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="5 sec"/>
  <stoptimeout>30 sec</stoptimeout>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>4</keepFiles>
  </log>
</service>
"@
    Install-WinSWService -Id 'plenora-object-storage' -Xml $storageXml
    Start-Service plenora-object-storage

    $up = $false
    foreach ($i in 1..20) {
        $tnc = Test-NetConnection 127.0.0.1 -Port 8333 -WarningAction SilentlyContinue
        if ($tnc.TcpTestSucceeded) { $up = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $up) { Fail 'SeaweedFS S3 non in ascolto su 8333 entro 40s (vedi C:\plenora-services\plenora-object-storage\*.log)' }
    Ok 'SeaweedFS S3 in ascolto su 127.0.0.1:8333'

    # .env: applicazione disaccoppiata dal vendor, solo protocollo S3.
    Set-EnvValue -File $envFile -Key 'OBJECT_STORAGE_ENABLED' -Value 'true'
    Set-EnvValue -File $envFile -Key 'OBJECT_STORAGE_BACKEND' -Value 's3_compatible'
    Set-EnvValue -File $envFile -Key 'CLOUD_PROVIDER' -Value 'none'
    Set-EnvValue -File $envFile -Key 'S3_ENDPOINT_URL' -Value 'http://127.0.0.1:8333'
    Set-EnvValue -File $envFile -Key 'S3_ACCESS_KEY' -Value $storageUser
    Set-EnvValue -File $envFile -Key 'S3_SECRET_KEY' -Value $storageSecret
    Set-EnvValue -File $envFile -Key 'AWS_ACCESS_KEY_ID' -Value $storageUser
    Set-EnvValue -File $envFile -Key 'AWS_SECRET_ACCESS_KEY' -Value $storageSecret
    Set-EnvValue -File $envFile -Key 'S3_BUCKET' -Value 'plenora-runtime'
    Set-EnvValue -File $envFile -Key 'OBJECT_STORAGE_BUCKET' -Value 'plenora-runtime'
    Set-EnvValue -File $envFile -Key 'PLENORA_ARTIFACT_BUCKET' -Value 'plenora-runtime'
    Set-EnvValue -File $envFile -Key 'BACKUP_BUCKET' -Value 'plenora-backups'

    # S3_BUCKET inizializza soltanto il bucket runtime di SeaweedFS. Il bucket
    # backup e' intenzionalmente separato e deve essere creato esplicitamente.
    # Lo script usa HeadBucket + CreateBucket ed e' quindi sicuro sui rerun.
    & $condaBat run -n $CondaEnv --cwd $BackendDir `
        python scripts/provision_local_s3_buckets.py
    if ($LASTEXITCODE -ne 0) {
        Fail "Provisioning bucket SeaweedFS fallito (exit=$LASTEXITCODE)"
    }
    Ok 'Bucket SeaweedFS pronti: plenora-runtime, plenora-backups'

    # I servizi app leggono il .env al boot: restart per vedere lo storage.
    $running = Get-Service plenora-backend, plenora-worker*, plenora-beat -ErrorAction SilentlyContinue |
               Where-Object Status -eq 'Running'
    foreach ($s in $running) { Restart-Service $s.Name }
    Ok 'Object storage configurato: runtime=plenora-runtime, backup=plenora-backups, backend=s3_compatible, policy=object_storage'
}

# =============================================================================
# W4 — Frontend: build Vite + Caddy come servizio Windows
# =============================================================================
if ($InstallFrontend) {
    Step "W4: frontend (build Vite, API same-origin /api) + Caddy su porta $FrontendPort"

    $FrontendDir = Join-Path $RepoRoot 'frontend'
    if (-not (Test-Path (Join-Path $FrontendDir 'package.json'))) { Fail "frontend non trovato in $FrontendDir" }

    # La shell che ha eseguito winget puo' non vedere ancora il PATH macchina
    # aggiornato. Preferiamo il path stabile dell'installer Node ufficiale.
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    $npm = if ($npmCommand) { $npmCommand.Source } else { Join-Path $env:ProgramFiles 'nodejs\npm.cmd' }
    if (-not (Test-Path $npm)) {
        Fail "npm.cmd non trovato in '$npm' (Node.js LTS installato correttamente?)"
    }

    # Vuoto = same-origin. Un valore assoluto serve solo a deployment
    # intenzionalmente multi-host.
    Push-Location $FrontendDir
    $originalPath = $env:PATH
    try {
        # npm.cmd puo' essere invocato per path assoluto, ma lifecycle script
        # come esbuild eseguono `node` tramite PATH: propaghiamo la directory
        # Node anche ai processi figli della shell aperta prima di winget.
        $nodeDir = Split-Path -Parent $npm
        $env:PATH = "$nodeDir;$originalPath"
        $env:VITE_API_URL = $FrontendApiUrl
        Write-Host '  npm ci...'
        & $npm ci
        if ($LASTEXITCODE -ne 0) { Fail "npm ci exit=$LASTEXITCODE" }
        Write-Host '  npm run build...'
        & $npm run build
        if ($LASTEXITCODE -ne 0) { Fail "npm run build exit=$LASTEXITCODE" }
    } finally {
        Pop-Location
        $env:PATH = $originalPath
        Remove-Item Env:\VITE_API_URL -ErrorAction SilentlyContinue
    }
    $distDir = Join-Path $FrontendDir 'dist'
    if (-not (Test-Path (Join-Path $distDir 'index.html'))) { Fail 'build FE senza dist\index.html' }
    Ok 'Frontend buildato (dist/)'

    # Manteniamo le origin locali per compatibilita' con tooling diretto; il
    # browser in produzione usa /api same-origin e non dipende dal CORS.
    $portSuffix = if ($FrontendPort -eq 80) { '' } else { ":$FrontendPort" }
    Set-EnvValue -File $envFile -Key 'CORS_ORIGINS' -Value "http://127.0.0.1$portSuffix,http://localhost$portSuffix"
    $backendSvc = Get-Service plenora-backend -ErrorAction SilentlyContinue
    if ($backendSvc -and $backendSvc.Status -eq 'Running') { Restart-Service plenora-backend }

    # Caddy: singolo exe dal download service ufficiale.
    $caddySvcDir = Join-Path $ServicesRoot 'plenora-caddy'
    New-Item -ItemType Directory -Force $caddySvcDir | Out-Null
    $caddyExe = Join-Path $caddySvcDir 'caddy.exe'
    if (-not (Test-Path $caddyExe)) {
        Write-Host '  Scarico caddy.exe (download service ufficiale)...'
        Invoke-WebRequest 'https://caddyserver.com/api/download?os=windows&arch=amd64' -OutFile $caddyExe
    }

    # Caddyfile: statici SPA (fallback index.html) + reverse proxy delle
    # rotte backend. /health e /ready proxati per comodita' operatore.
    $caddyfile = Join-Path $caddySvcDir 'Caddyfile'
    @"
:$FrontendPort {
    encode gzip
    @backend path /api/* /health /ready
    handle @backend {
        reverse_proxy 127.0.0.1:5004
    }
    handle {
        root * $distDir
        try_files {path} /index.html
        file_server
    }
}
"@ | Set-Content -Encoding ascii $caddyfile

    $caddyXml = @"
<service>
  <id>plenora-caddy</id>
  <name>Plenora Frontend (Caddy)</name>
  <description>Statici frontend Plenora + reverse proxy API (porta $FrontendPort)</description>
  <executable>$caddyExe</executable>
  <arguments>run --config $caddyfile --adapter caddyfile</arguments>
  <workingdirectory>$caddySvcDir</workingdirectory>
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="5 sec"/>
  <stoptimeout>15 sec</stoptimeout>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>4</keepFiles>
  </log>
</service>
"@
    Install-WinSWService -Id 'plenora-caddy' -Xml $caddyXml
    Start-Service plenora-caddy

    $up = $false
    foreach ($i in 1..10) {
        $tnc = Test-NetConnection 127.0.0.1 -Port $FrontendPort -WarningAction SilentlyContinue
        if ($tnc.TcpTestSucceeded) { $up = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $up) { Fail "Caddy non in ascolto su $FrontendPort (vedi C:\plenora-services\plenora-caddy\*.log)" }
    Ok "W4 completata: frontend sulla porta $FrontendPort, API same-origin /api"
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
Write-Host ' Setup base completato.' -ForegroundColor Green
Write-Host '=============================================================' -ForegroundColor Green
Write-Host '  Servizi:    -InstallServices (backend/worker/beat come servizi Windows)'
Write-Host '  Storage:    SeaweedFS S3 locale installato per default (-InstallObjectStorage:$false per endpoint esterno)'
Write-Host '  Frontend:   -InstallFrontend (build Vite + Caddy su porta 80)'
Write-Host '  XLSX:       ZIP + .sha256 in C:\deploy (rilevamento e verifica automatici)'
Write-Host '  Production: -Production (gates attivi, richiede object storage)'
Write-Host ''
Write-Host '  Checkpoint: curl.exe -s http://127.0.0.1:5004/ready' -ForegroundColor Cyan
Write-Host "  Smoke:      conda run -n $CondaEnv --cwd $BackendDir python smoke_native.py" -ForegroundColor Cyan
Write-Host '  Dev mode:   -StartProcesses apre backend/worker/beat in 3 finestre cmd' -ForegroundColor Cyan

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

    $workerServices = @()
    if ($SplitWorkerServices) {
        $workerProcessEstimate = $DataWorkerServices + 1
        for ($i = 1; $i -le $DataWorkerServices; $i++) {
            $workerId = if ($i -eq 1) { 'plenora-worker' } else { "plenora-worker-data-$i" }
            $workerServices += @{
                id = $workerId
                name = "Plenora Worker Data $i"
                desc = "Plenora-ETL data-plane worker Dramatiq (1 processo x $DataWorkerThreads thread)"
                args = "-m dramatiq workers.dramatiq_app --queues data-plane --processes 1 --threads $DataWorkerThreads"
                depends = @($pgSvc, 'RabbitMQ')
                extraEnv = @{
                    DRAMATIQ_PROCESSES = $workerProcessEstimate
                    DRAMATIQ_THREADS = $DataWorkerThreads
                    dramatiq_queue_prefetch = $WorkerQueuePrefetch
                    dramatiq_delay_queue_prefetch = $WorkerDelayQueuePrefetch
                }
            }
        }
        $workerServices += @{
            id = 'plenora-worker-control'
            name = 'Plenora Worker Control'
            desc = "Plenora-ETL control/scheduler/default worker Dramatiq (1 processo x $ControlWorkerThreads thread)"
            args = "-m dramatiq workers.dramatiq_app --queues control-plane scheduler default --processes 1 --threads $ControlWorkerThreads"
            depends = @($pgSvc, 'RabbitMQ')
            extraEnv = @{
                DRAMATIQ_PROCESSES = $workerProcessEstimate
                DRAMATIQ_THREADS = $ControlWorkerThreads
                dramatiq_queue_prefetch = $WorkerQueuePrefetch
                dramatiq_delay_queue_prefetch = $WorkerDelayQueuePrefetch
            }
        }
    } else {
        $workerServices += @{
            id = 'plenora-worker'
            name = 'Plenora Worker'
            desc = "Plenora-ETL worker Dramatiq ($WorkerProcesses processi x $WorkerThreads thread)"
            args = "-m dramatiq workers.dramatiq_app --processes $WorkerProcesses --threads $WorkerThreads"
            depends = @($pgSvc, 'RabbitMQ')
            extraEnv = @{
                DRAMATIQ_PROCESSES = $WorkerProcesses
                DRAMATIQ_THREADS = $WorkerThreads
                dramatiq_queue_prefetch = $WorkerQueuePrefetch
                dramatiq_delay_queue_prefetch = $WorkerDelayQueuePrefetch
            }
        }
    }

    $workerServiceIds = @($workerServices | ForEach-Object { $_.id })
    $existingWorkerServices = @(Get-Service -Name 'plenora-worker*' -ErrorAction SilentlyContinue)
    foreach ($existing in $existingWorkerServices) {
        if ($workerServiceIds -notcontains $existing.Name) {
            Remove-WinSWService -Id $existing.Name
        }
    }

    $services = @(
        @{
            id = 'plenora-backend'
            name = 'Plenora Backend'
            desc = 'Plenora-ETL API FastAPI (uvicorn)'
            args = '-m uvicorn app.main:app --host 127.0.0.1 --port 5004'
            depends = @($pgSvc)
        }
    ) + $workerServices + @(
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
        $extraEnvXml = ConvertTo-ServiceEnvXml -Env $svc.extraEnv
        if ($extraEnvXml) { $extraEnvXml = "$extraEnvXml`r`n" }
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
$extraEnvXml$dependXml
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
    foreach ($id in @('plenora-backend') + $workerServiceIds + @('plenora-beat')) {
        Start-Service $id
        Start-Sleep -Seconds 5
    }
    Start-Sleep -Seconds 10
    Get-Service plenora-* | Format-Table Status, Name, DisplayName -AutoSize
    Write-Host '  Log dei servizi: C:\plenora-services\<id>\<id>.out.log / .err.log' -ForegroundColor Cyan
    if ($SplitWorkerServices) {
        Ok "W1 completata: servizi Windows attivi (backend, $($workerServiceIds.Count) worker, beat; avvio automatico al boot)"
    } else {
        Ok 'W1 completata: 3 servizi Windows attivi (avvio automatico al boot)'
    }
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

# =============================================================================
# W5 — Production: gates attivi su deployment single-box dichiarato
# =============================================================================
if ($Production) {
    Step 'W5: switch a ENVIRONMENT=production (single-node gates)'

    # I production gates esigono object storage condiviso: locale oppure
    # endpoint S3-compatible gia' configurato nel .env.
    $envC = Get-Content $envFile -Raw
    if ($envC -notmatch 'OBJECT_STORAGE_ENABLED=true') {
        Fail 'production richiede object storage: usa -InstallObjectStorage o configura un endpoint S3 nel .env prima di -Production'
    }

    # NB dotenv in production: config_env_loader ignora il .env quando il
    # PROCESS env dichiara production — i servizi WinSW non hanno
    # ENVIRONMENT nel process env, quindi il .env viene letto e a quel
    # punto ENVIRONMENT=production attiva i gates. Hardening futuro:
    # secrets via env di sistema del servizio.
    Set-EnvValue -File $envFile -Key 'ENVIRONMENT' -Value 'production'
    # Single-box dichiarato: sopprime SOLO il gate anti-loopback sul
    # broker (RabbitMQ e' su questa macchina per design).
    Set-EnvValue -File $envFile -Key 'PLENORA_SINGLE_NODE' -Value 'true'
    # Rate limiting distribuito (su PostgreSQL) fail-closed, come i
    # gates richiedono.
    Set-EnvValue -File $envFile -Key 'LOGIN_RL_DISTRIBUTED_REQUIRED' -Value 'true'
    Set-EnvValue -File $envFile -Key 'ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_ENABLED' -Value 'true'
    Set-EnvValue -File $envFile -Key 'ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_REQUIRED' -Value 'true'

    # Restart di tutti i servizi app per il nuovo ambiente.
    $appServices = Get-Service plenora-backend, plenora-worker*, plenora-beat -ErrorAction SilentlyContinue
    foreach ($s in $appServices) {
        if ($s.Status -eq 'Running') { Restart-Service $s.Name }
    }

    # Il boot in production e' fail-fast sui gates: se /ready non torna,
    # un gate ha bloccato — vedere plenora-backend.err.log.
    $ready = $false
    foreach ($i in 1..30) {
        try {
            $resp = Invoke-RestMethod 'http://127.0.0.1:5004/ready' -TimeoutSec 3
            if ($resp.status -eq 'ready') { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 3
    }
    if (-not $ready) {
        Fail 'backend non ready in production entro 90s: controllare C:\plenora-services\plenora-backend\plenora-backend.err.log (probabile gate fallito)'
    }
    Ok 'W5 completata: ENVIRONMENT=production, gates attivi, /ready verde'
}
