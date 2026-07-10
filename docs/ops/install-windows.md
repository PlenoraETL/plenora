# Manuale di installazione — Plenora su Windows nativo (single-box)

Manuale completo dell'installazione nativa su Windows (Server 2022/2025
o Windows 11 Pro): **niente Docker, niente WSL**. PostgreSQL, RabbitMQ,
backend FastAPI, worker Dramatiq, beat APScheduler, MinIO opzionale e
frontend (Caddy) girano come **servizi Windows** con avvio automatico.

Collaudato end-to-end su Windows Server 2025 (2026-07-04). Validazione
post-install automatica con `scripts\validate-windows-install.ps1
-RunE2E -RequireFrontend -RequireMinio` verde su VM Windows Server 2025
(2026-07-05): servizi, `/ready`, frontend, smoke nativo, `tests2/functional`
23 passed, e ripartenza post-reboot confermata. Questo documento e' il
sorgente canonico anche per il manuale PDF operativo.

## Indice

1. [Architettura installata](#1-architettura-installata)
2. [Prerequisiti](#2-prerequisiti)
3. [Installazione passo-passo](#3-installazione-passo-passo)
4. [Parametri dell'installer](#4-parametri-dellinstaller)
5. [Collaudo dell'installazione](#5-collaudo-dellinstallazione)
6. [Test e2e-v2 sull'ambiente installato](#6-test-e2e-v2-sullambiente-installato)
7. [Operativita' quotidiana](#7-operativita-quotidiana)
8. [Aggiornamento del software](#8-aggiornamento-del-software)
9. [Checklist operative](#9-checklist-operative)
10. [Troubleshooting](#10-troubleshooting)
11. [Disinstallazione](#11-disinstallazione)

---

## 1. Architettura installata

| Componente | Come gira | Versione | Porta |
|---|---|---|---|
| PostgreSQL 17 | servizio EDB (`postgresql-x64-17`) | 17.x | 5432 |
| Erlang/OTP | runtime di RabbitMQ | **28.x (pinnato)** | — |
| RabbitMQ | servizio `RabbitMQ` | 4.3.x | 5672 |
| Backend FastAPI | servizio `plenora-backend` (WinSW → uvicorn) | repo | 5004 (loopback) |
| Worker Dramatiq | servizio `plenora-worker` (e, in modalita' split, `plenora-worker-control` + eventuali `plenora-worker-data-N`) | repo | — |
| Beat APScheduler | servizio `plenora-beat` | repo | — |
| MinIO (opzionale) | servizio `plenora-minio` | latest | 9000 (API), 9001 (console) |
| Frontend (Caddy) | servizio `plenora-caddy` | latest | **80** (configurabile) |
| Python + GDAL | env conda `plenora` (Miniforge) | 3.12 / GDAL 3.13 | — |

Stato, lock, blacklist JWT, rate-limit e risultati dei task vivono su
PostgreSQL; RabbitMQ e' solo il trasporto dei task (architettura
Plenora 2.0). File di configurazione: `C:\plenora\backend\.env`
(ACL ristretta ad Administrators+SYSTEM). Dati: `C:\plenora-data`.
Servizi e log: `C:\plenora-services\<servizio>\`.

## 2. Prerequisiti

- Windows Server 2022/2025 o Windows 11 Pro, 64 bit
- 8 GB RAM consigliati, ~15 GB disco (pacchetti + env conda)
- `winget` disponibile (incluso in Server 2025 / Windows 11)
- PowerShell **da Amministratore** per l'installazione
- Accesso di rete verso: sorgenti winget, GitHub releases (RabbitMQ,
  WinSW), `dl.min.io` (se MinIO), `caddyserver.com` (se frontend),
  conda-forge, PyPI, registry npm

## 3. Installazione passo-passo

### 3.1 Clona il repository

```powershell
git clone https://github.com/PlenoraETL/plenora.git C:\plenora
cd C:\plenora
```
(se Git manca: `winget install Git.Git` e riapri la shell)

### 3.2 Fase 1 — pacchetti di piattaforma

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Installa via winget: Git, Node LTS, **PostgreSQL 17**, **Erlang/OTP 28**
(pinnato — vedi §9), **Miniforge**; poi scarica e installa **RabbitMQ**
dalla release ufficiale GitHub (non e' piu' su winget) impostando
`ERLANG_HOME` a livello macchina.

> Al termine del blocco winget: **chiudi la shell e riaprine una nuova
> da Amministratore** (i PATH nuovi non sono visibili nella shell
> corrente).

### 3.3 Fase 2 — stack applicativo completo

```powershell
cd C:\plenora
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 `
    -SkipWinget -InstallServices -InstallMinio -InstallFrontend -Production `
    -SplitWorkerServices -DataWorkerServices 1 `
    -DataWorkerThreads 1 -ControlWorkerThreads 2
```

Ti verranno chiesti:

1. **Password del superuser `postgres`** — con l'install winget
   unattended di solito NON e' nota: vedi §9 per il reset in locale.
2. **Username e password del superadmin** (minimo 12 caratteri, doppia
   conferma). E' l'utente con cui farai login nell'applicazione.

Il flusso esegue nell'ordine: utente+DB PostgreSQL → verifica RabbitMQ
→ env conda (python 3.12 + GDAL 3.13 da conda-forge) → dipendenze pip →
`.env` con secrets random → **hardening broker** (utente RabbitMQ
dedicato `plenora`, rimozione di `guest`) → ACL sul `.env` → **MinIO**
(servizio + bucket `plenora-runtime`/`plenora-backups`) → **frontend**
(build Vite + servizio Caddy) → **servizi applicativi** (backend con
migrations al primo boot, worker, beat) → **seed superadmin** con
verifica via login API reale → **switch a production** (gates attivi,
attesa `/ready`).

Output finale atteso:

```
[OK]   Superadmin '<nome>' verificato: login API ok
[OK]   W5 completata: ENVIRONMENT=production, gates attivi, /ready verde
```

### 3.4 Primo accesso

Browser (sulla macchina): **`http://127.0.0.1/`** → login con il
superadmin. Usare esattamente quell'hostname (la build FE e' cablata
su quell'origin; l'installer configura comunque CORS anche per
`localhost`).

## 4. Parametri dell'installer

| Parametro | Default | Effetto |
|---|---|---|
| `-SkipWinget` | off | Salta la fase pacchetti (gia' installati) |
| `-InstallServices` | off | Servizi `plenora-backend/worker/beat` |
| `-WorkerProcesses` / `-WorkerThreads` | 1 / 1 | Parallelismo del worker Dramatiq legacy, usato quando `-SplitWorkerServices:$false` |
| `-SplitWorkerServices` | on | Crea worker separati per code: data-plane e control/scheduler/default; disabilitare esplicitamente con `-SplitWorkerServices:$false` |
| `-DataWorkerServices` / `-DataWorkerThreads` | 1 / 1 | Numero di servizi data-plane e thread per ciascun servizio split |
| `-ControlWorkerThreads` | 2 | Thread del worker control/scheduler/default in modalita' split |
| `-WorkerQueuePrefetch` / `-WorkerDelayQueuePrefetch` | 1 / 1 | Prefetch Dramatiq dei worker Windows; default basso per contenere RSS |
| `-AdminUsername` / `-AdminPassword` / `-AdminGroupId` | prompt / `root` | Superadmin; `-SkipAdminBootstrap` per saltare |
| `-InstallMinio` | off | MinIO come servizio + bucket + provider `minio` |
| `-StoragePolicy` | `hybrid` | `db` \| `hybrid` \| `object_storage` |
| `-InstallFrontend` | off | Build FE + servizio Caddy |
| `-FrontendPort` | 80 | Porta di Caddy (la build FE viene cablata su quell'origin) |
| `-Production` | off | `ENVIRONMENT=production` coi gates (richiede object storage) |
| `-PgSuperPassword` | prompt | Password del superuser postgres |
| `-StartProcesses` | off | Modalita' dev: 3 finestre cmd al posto dei servizi |

Non-interattivo (env): `PLENORA_BOOTSTRAP_ADMIN_USERNAME`,
`PLENORA_BOOTSTRAP_ADMIN_PASSWORD`, `PLENORA_BOOTSTRAP_ADMIN_GROUP_ID`,
`PLENORA_INSTALL_WORKER_PROCESSES`, `PLENORA_INSTALL_WORKER_THREADS`,
`PLENORA_INSTALL_SPLIT_WORKERS`, `PLENORA_INSTALL_DATA_WORKER_SERVICES`,
`PLENORA_INSTALL_DATA_WORKER_THREADS`,
`PLENORA_INSTALL_CONTROL_WORKER_THREADS`,
`PLENORA_INSTALL_WORKER_QUEUE_PREFETCH`,
`PLENORA_INSTALL_WORKER_DELAY_QUEUE_PREFETCH`.

La modalita' split con data-plane a thread singolo e' il default per evitare
pipeline pandas concorrenti nello stesso processo. RabbitMQ bilancia
automaticamente i task tra piu' servizi
che ascoltano la stessa coda; per aumentare throughput data-plane usare
`-DataWorkerServices 2` prima di aumentare i thread.

**Il modello single-box (`-Production`)**: scrive
`PLENORA_SINGLE_NODE=true` — dichiarazione esplicita che broker e
servizi convivono sulla macchina; sopprime SOLO il gate anti-loopback
sul broker. Restano attivi: object storage obbligatorio, rate-limiter
distribuiti (su PostgreSQL) fail-closed, beat leader fail-closed.

## 5. Collaudo dell'installazione

Batteria minima automatica (5 minuti) — e' la stessa usata nel collaudo
ufficiale, impacchettata in uno script non distruttivo:

```powershell
cd C:\plenora
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RequireFrontend -RequireMinio
```

Lo script verifica servizi Windows, `/ready` attraverso Caddy, frontend
statico, e smoke round-trip `dispatch -> RabbitMQ -> worker -> PG`.
Non riavvia servizi, non modifica `.env`, non cancella dati.

Se l'installazione non include Caddy/frontend:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -ReadyUrl http://127.0.0.1:5004/ready -SkipFrontendPage
```

Controlli equivalenti manuali:

```powershell
# 1. Tutti i servizi su
Get-Service plenora-*        # attesi Running: backend, worker*, beat (+minio, +caddy)

# 2. Readiness completa (attraverso Caddy)
curl.exe -s http://127.0.0.1/ready
#    atteso: {"status":"ready", ...} con 8/8 check ok

# 3. Smoke round-trip: dispatch -> RabbitMQ -> worker -> esito su PG
& 'C:\ProgramData\miniforge3\condabin\conda.bat' run -n plenora --cwd C:\plenora\backend python smoke_native.py
#    atteso: SMOKE: SUCCESS

# 4. Login dal browser: http://127.0.0.1/ con il superadmin
```

Con la suite functional isolata:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RunE2E -RequireFrontend -RequireMinio
```

Con functional + integration:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RunE2E -Integration -RequireFrontend -RequireMinio
```

Batteria estesa (collaudo da accettazione):

```powershell
# 5. Sopravvivenza al riavvio: tutto riparte da solo
Restart-Computer
#    ...dopo il boot: ripetere 1-4 senza toccare nulla

# 6. Stop pulito del beat: il leader lock su PG viene rilasciato
Stop-Service plenora-beat
$env:PGPASSWORD='plenora'
& 'C:\Program Files\PostgreSQL\17\bin\psql.exe' -U plenora -h 127.0.0.1 -d plenora -c "SELECT * FROM plenora.beat_leader_locks;"
Remove-Item Env:\PGPASSWORD
#    atteso: 0 righe
Start-Service plenora-beat

# 7. Solo l'utente broker dedicato esiste (guest rimosso)
& 'C:\Program Files\RabbitMQ Server\rabbitmq_server-4.3.2\sbin\rabbitmqctl.bat' list_users
#    atteso: solo `plenora`
```

## 6. Test e2e-v2 sull'ambiente installato

Le suite e2e-v2 del repo (`tests2/functional`, `tests2/integration`)
girano sull'installazione nativa **senza toccare i servizi di
produzione**: usano un DB dedicato `plenora_test` e code RabbitMQ
suffissate per-run (il worker di produzione non vede i task di test).

```powershell
cd C:\plenora
# suite functional (veloce)
powershell -ExecutionPolicy Bypass -File scripts\run-e2e-windows.ps1

# functional + integration (i test dei provider esterni Oracle/MSSQL/
# MySQL si auto-skippano se quei DB non ci sono)
powershell -ExecutionPolicy Bypass -File scripts\run-e2e-windows.ps1 -Integration

# iterazioni mirate
powershell -ExecutionPolicy Bypass -File scripts\run-e2e-windows.ps1 -SkipDeps -PytestArgs '-k api_enrich'
```

Il runner: installa le dev-deps nell'env conda, crea+migra
`plenora_test`, legge le credenziali broker dal `.env` (il fallback
`guest` non esiste piu'), imposta `URBY_RUN_INTEGRATION=1` e lancia
pytest. Exit code 0 = verdi.

Lo stesso runner puo' essere invocato dal validatore post-install con
`scripts\validate-windows-install.ps1 -RunE2E` (e `-Integration` se
serve la suite estesa).

Nota: la suite canonica completa (~10.500 unit/contract) resta la lane
containerizzata `tests2` su Linux/Docker — le e2e-v2 su Windows
validano il COMPORTAMENTO dell'installazione reale.

## 7. Operativita' quotidiana

```powershell
Get-Service plenora-*                    # stato
Restart-Service plenora-backend          # riavvio mirato
Stop-Service plenora-*                   # stop ordinato di tutto
```

- **Log** (rotazione automatica 10MB x4):
  `C:\plenora-services\<servizio>\<servizio>.out.log` / `.err.log`
- **Config**: `C:\plenora\backend\.env` — dopo una modifica,
  riavviare i servizi interessati
- **Console MinIO**: `http://127.0.0.1:9001` (credenziali
  `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` dal `.env`)
- **Backup DB**: `pg_dump` dello schema `plenora` (vedi
  [backup-restore.md](backup-restore.md) per il modello completo)

## 8. Aggiornamento del software

```powershell
cd C:\plenora
git fetch origin
git pull --ff-only origin main
git log -1 --oneline
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 `
    -SkipWinget -InstallServices -InstallMinio -InstallFrontend `
    -SkipAdminBootstrap -Production
```

Idempotente: rigenera i servizi (stop/refresh config/start), riesegue
le migrations al boot del backend, ricostruisce il FE. I dati (PG,
MinIO) non vengono toccati.

Se l'installazione usa una porta frontend diversa da 80, ripassare la
stessa porta usata in installazione:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 `
    -SkipWinget -InstallServices -InstallMinio -InstallFrontend `
    -SkipAdminBootstrap -Production -FrontendPort 8080
```

Validare sempre dopo l'update:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RequireFrontend -RequireMinio
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RunE2E -RequireFrontend -RequireMinio
```

## 9. Checklist operative

Installazione nuova:

- [ ] PowerShell Amministratore aperta.
- [ ] Repository clonato in `C:\plenora`.
- [ ] `git log -1 --oneline` mostra il commit atteso.
- [ ] Prima fase installer completata.
- [ ] PowerShell riaperta dopo winget.
- [ ] Seconda fase installer completata con `-Production`.
- [ ] Login superadmin riuscito.
- [ ] `validate-windows-install.ps1 -RequireFrontend -RequireMinio` verde.
- [ ] `validate-windows-install.ps1 -RunE2E -RequireFrontend -RequireMinio` verde.

Update:

- [ ] PowerShell Amministratore aperta.
- [ ] `git pull --ff-only origin main` completato.
- [ ] Commit verificato con `git log -1 --oneline`.
- [ ] Installer rilanciato con `-SkipAdminBootstrap`.
- [ ] Servizi `plenora-*` Running.
- [ ] `/ready` verde.
- [ ] Validazione base verde.
- [ ] Validazione e2e-v2 verde.

## 10. Troubleshooting

| Sintomo | Causa e rimedio |
|---|---|
| Un servizio `plenora-*` non parte | `C:\plenora-services\<id>\<id>.err.log` (wrapper) e `.out.log` (app). In production il backend e' fail-fast sui gates: il log dice quale gate ha bloccato |
| Servizio RabbitMQ `Stopped`, zero log | `ERLANG_HOME` mancante a livello macchina (l'installer la imposta; verificare dopo update manuali di Erlang) |
| RabbitMQ `BOOT FAILED ... incompatible_feature_flags {horus, unknown_instruction}` | Erlang/OTP troppo nuovo per la versione RabbitMQ: usare la serie OTP supportata (oggi 28.x — l'installer la pinna) |
| `rabbitmqctl` → `incompatible cookie` | Cookie Erlang dell'utente ≠ LocalSystem: copiare `C:\Windows\System32\config\systemprofile\.erlang.cookie` in `%USERPROFILE%` (l'installer lo fa da solo) |
| Password `postgres` sconosciuta (install winget) | Reset locale: procedura sotto. |
| Login dal browser: "Impossibile contattare il server" | Hostname diverso dall'origin della build FE (es. `localhost` vs `127.0.0.1`): usare `http://127.0.0.1/`; l'installer scrive CORS per entrambe |
| Porta 80 occupata (IIS?) | Reinstallare il FE con `-FrontendPort 8080` (ribuilda anche il bundle sull'origin nuovo) |
| `/ready` not_ready su `object_storage` | Provider cloud configurato senza credenziali: con MinIO usare `-InstallMinio`; senza object storage (solo dev) `OBJECT_STORAGE_ENABLED=false` |
| Task in coda ma mai eseguiti | `plenora-worker*` giu' o `.env` broker cambiato senza restart dei servizi worker |
| Un processo Erlang blocca update/cleanup | `epmd.exe` resta vivo dopo stop/uninstall: `Stop-Process -Name epmd -Force` |

Reset locale della password `postgres`:

```powershell
$pgData = 'C:\Program Files\PostgreSQL\17\data'
$pgBin = 'C:\Program Files\PostgreSQL\17\bin'
Copy-Item "$pgData\pg_hba.conf" "$pgData\pg_hba.conf.bak"
(Get-Content "$pgData\pg_hba.conf") `
    -replace 'scram-sha-256','trust' `
    | Set-Content "$pgData\pg_hba.conf"
Restart-Service postgresql-x64-17
& "$pgBin\psql.exe" -U postgres -c "ALTER USER postgres WITH PASSWORD '<nuova-password-forte>';"
Copy-Item "$pgData\pg_hba.conf.bak" "$pgData\pg_hba.conf" -Force
Restart-Service postgresql-x64-17
```

Trappola per chi modifica gli script: i `.ps1` del repo sono **UTF-8
con BOM** — PowerShell 5.1 senza BOM li legge come ANSI e una em-dash
dentro una stringa double-quoted diventa una virgoletta smart che
CHIUDE la stringa (parse error fantasma piu' avanti nel file).

## 11. Disinstallazione

```powershell
# Servizi applicativi (worker split inclusi)
foreach ($svc in Get-Service plenora-caddy,plenora-beat,plenora-worker*,plenora-minio,plenora-backend -ErrorAction SilentlyContinue) {
    $id = $svc.Name
    Stop-Service $id -Force
    if (Test-Path "C:\plenora-services\$id\$id.exe") {
        & "C:\plenora-services\$id\$id.exe" uninstall
    }
}
Remove-Item -Recurse -Force C:\plenora-services

# Dati e repo (IRREVERSIBILE: contiene DB MinIO e DATA_DIR)
Remove-Item -Recurse -Force C:\plenora-data, C:\plenora

# Piattaforma (se non serve ad altro)
winget uninstall PostgreSQL.PostgreSQL.17     # i dati DB sono in Program Files\PostgreSQL\17\data
winget uninstall Erlang.ErlangOTP             # prima: disinstallare il servizio RabbitMQ
winget uninstall CondaForge.Miniforge3
```
