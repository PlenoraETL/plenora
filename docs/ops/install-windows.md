# Manuale di installazione — Plenora su Windows nativo (single-box)

Manuale completo dell'installazione nativa su Windows (Server 2022/2025
o Windows 11 Pro): **niente Docker, niente WSL**. PostgreSQL, RabbitMQ,
backend FastAPI, worker Dramatiq, beat APScheduler, SeaweedFS S3 e
frontend (Caddy) girano come **servizi Windows** con avvio automatico.

Collaudato end-to-end su Windows Server 2025 (2026-07-04). Validazione
post-install automatica con `scripts\validate-windows-install.ps1
-RunE2E -RequireFrontend -RequireObjectStorage` verde su VM Windows Server 2025
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
12. [Vista dashboard: Grafana opzionale](#12-vista-dashboard-grafana-opzionale)

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
| Object storage SeaweedFS | servizio `plenora-object-storage` | 4.29 | 8333 (S3, loopback) |
| Frontend (Caddy) | servizio `plenora-caddy` | latest | **80** (configurabile) |
| Grafana OSS (opzionale, `-InstallGrafana`) | servizio `plenora-grafana` + plugin Infinity | 13.3.0 / 4.1.6 (pinnati) | 3300 (loopback), pubblicato su `/grafana` |
| Python + GDAL | env conda `plenora` (Miniforge) | 3.14 / GDAL 3.13 | — |
| Microsoft ODBC Driver | connettore SQL Server/MSSQL | 18.x x64 | — |
| XLSX exporter nativo | EXE statico opzionale (fallback Python automatico) | release Plenora | — |

Stato, lock, blacklist JWT, rate-limit e metadati dei task vivono su
PostgreSQL; upload, risultati, preview e intermedi persistenti vivono su
object storage S3-compatible. RabbitMQ e' solo il trasporto dei task.
File di configurazione: `C:\plenora\backend\.env`
(ACL ristretta ad Administrators+SYSTEM). Dati: `C:\plenora-data`.
Servizi e log: `C:\plenora-services\<servizio>\`.

## 2. Prerequisiti

- Windows Server 2022/2025 o Windows 11 Pro, 64 bit
- 8 GB RAM consigliati, ~15 GB disco (pacchetti + env conda)
- `winget` disponibile (incluso in Server 2025 / Windows 11)
- PowerShell **da Amministratore** per l'installazione
- Accesso di rete verso: sorgenti winget, GitHub releases (RabbitMQ,
  WinSW e SeaweedFS), `caddyserver.com` (se frontend),
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
    -SkipWinget -InstallServices -InstallFrontend -Production `
    -SplitWorkerServices -DataWorkerServices 1 `
    -DataWorkerThreads 1 -ControlWorkerThreads 2
```

Ti verranno chiesti:

1. **Password corrente del superuser `postgres`** — con l'install winget
   unattended EDB il valore iniziale e' `postgres`. L'installer verifica la
   password senza prompt secondari e obbliga a sostituire subito il default.
2. **Username e password del superadmin** (minimo 12 caratteri, doppia
   conferma). E' l'utente con cui farai login nell'applicazione.

Il flusso esegue nell'ordine: utente+DB PostgreSQL → verifica RabbitMQ
→ env conda (python 3.14 + GDAL 3.13 da conda-forge) → dipendenze pip →
acceleratore XLSX (se ZIP e `.sha256` sono presenti in `C:\deploy`) →
`.env` con secrets random → **hardening broker** (utente RabbitMQ
dedicato `plenora`, rimozione di `guest`) → ACL sul `.env` → **SeaweedFS**
(servizio S3 + bucket `plenora-runtime`) → **frontend**
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

Aprire nel browser **l'indirizzo del server**, per esempio
`http://192.168.1.20/` oppure il relativo nome DNS, e accedere con il
superadmin. Il frontend usa `/api` sullo stesso origin e Caddy inoltra
le richieste al backend locale: non e' legato a `127.0.0.1` e funziona
anche dai client remoti.

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
| `-InstallObjectStorage` | on | SeaweedFS locale; usare `$false` per un endpoint S3 esterno gia' configurato |
| `-InstallMinio` | off | Alias deprecato di `-InstallObjectStorage` |
| `-InstallFrontend` | off | Build FE + servizio Caddy |
| `-FrontendPort` | 80 | Porta pubblica di Caddy; frontend e API condividono lo stesso origin |
| `-FrontendApiUrl` | vuoto | Override avanzato dell'API; lasciare vuoto nel deploy single-box per usare `/api` same-origin |
| `-XlsxExporterArchive` / `-XlsxExporterChecksum` | auto | Pacchetto e checksum dell'acceleratore; senza path cerca il pacchetto piu' recente in `C:\deploy` e nella root del repo |
| `-SkipXlsxExporter` | off | Salta installazione/verifica dell'acceleratore e conserva il fallback Python |
| `-XlsxExporterOnly` | off | Deploy rapido del solo acceleratore, con stop/restart dei servizi gia' attivi |
| `-Production` | off | `ENVIRONMENT=production` coi gates (richiede object storage) |
| `-InstallGrafana` | off | Grafana OSS per la vista dashboard degli Eseguibili (richiede `-InstallFrontend`; vedi §12) |
| `-GrafanaArchive` / `-GrafanaInfinityArchive` | download | Zip di Grafana e del plugin Infinity gia' scaricati (installazione offline; stessi SHA-256 fissi) |
| `-GrafanaRootUrl` | `http://localhost[:porta]/grafana/` | URL pubblico di Grafana (`root_url`); con `https://` i cookie di Grafana diventano `Secure` |
| `-PgSuperPassword` | prompt | Password del superuser postgres |
| `-StartProcesses` | off | Modalita' dev: 3 finestre cmd al posto dei servizi |

Non-interattivo (env): `PLENORA_BOOTSTRAP_ADMIN_USERNAME`,
`PLENORA_BOOTSTRAP_ADMIN_PASSWORD`, `PLENORA_BOOTSTRAP_ADMIN_GROUP_ID`,
`PLENORA_INSTALL_WORKER_PROCESSES`, `PLENORA_INSTALL_WORKER_THREADS`,
`PLENORA_INSTALL_SPLIT_WORKERS`, `PLENORA_INSTALL_DATA_WORKER_SERVICES`,
`PLENORA_INSTALL_DATA_WORKER_THREADS`,
`PLENORA_INSTALL_CONTROL_WORKER_THREADS`,
`PLENORA_INSTALL_WORKER_QUEUE_PREFETCH`,
`PLENORA_INSTALL_WORKER_DELAY_QUEUE_PREFETCH`,
`PLENORA_INSTALL_XLSX_EXPORTER_ARCHIVE`,
`PLENORA_INSTALL_XLSX_EXPORTER_CHECKSUM`.

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

**Intestazioni di sicurezza del frontend (P31)**: con `-InstallFrontend`
l'installer genera `C:\plenora-services\plenora-caddy\security-headers.caddy`
da `backend/app/core/security/frontend_headers.py` (l'unica definizione della
CSP) e il Caddyfile lo importa in testa al ramo di Plenora: ogni risposta
(tranne `/grafana`, che con `-InstallGrafana` ha intestazioni proprie dallo
stesso modulo, vedi §12) porta
`Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`,
`X-Frame-Options`, `Permissions-Policy`. Le uniche origini esterne vengono
dal `backend\.env`: `FRONTEND_MAP_TILE_ORIGINS` (tile della mappa; vuoto =
nessuna tile esterna), `FRONTEND_SENTRY_DSN` (anche compilato nel bundle come
`VITE_SENTRY_DSN`), piu' `-FrontendApiUrl` se assoluto. Valvola d'emergenza:
`FRONTEND_CSP_REPORT_ONLY=true` e rieseguire `-InstallFrontend` (la CSP
segnala in console senza bloccare). I font sono inclusi nel bundle: nessuna
richiesta a Google Fonts.

## 5. Collaudo dell'installazione

Batteria minima automatica (5 minuti) — e' la stessa usata nel collaudo
ufficiale, impacchettata in uno script non distruttivo:

```powershell
cd C:\plenora
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RequireFrontend -RequireObjectStorage
```

Lo script verifica servizi Windows, `/ready` attraverso Caddy, frontend
statico con le sue intestazioni di sicurezza (CSP senza `unsafe-*`, `nosniff`),
e smoke round-trip `dispatch -> RabbitMQ -> worker -> PG`.
Non riavvia servizi, non modifica `.env`, non cancella dati.

Con Grafana installato (`-InstallGrafana`) lo script controlla anche il servizio
`plenora-grafana` (obbligatorio con `-RequireGrafana`), che ascolti solo su
`127.0.0.1:3300`, che `/grafana` senza sessione Plenora risponda **401** con le
intestazioni proprie (anche se il client manda un `X-WEBAUTH-USER` finto) e che
`/grafana-confine` (fuori dal blocco) sia ancora Plenora con le sue intestazioni.

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
#    atteso: {"status":"ready", ...} con 10/10 check ok

# 3. Smoke round-trip: dispatch -> RabbitMQ -> worker -> esito su PG
& 'C:\ProgramData\miniforge3\condabin\conda.bat' run -n plenora --cwd C:\plenora\backend python smoke_native.py
#    atteso: SMOKE: SUCCESS

# 4. Login dal browser: http://127.0.0.1/ con il superadmin
```

Con la suite functional isolata:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RunE2E -RequireFrontend -RequireObjectStorage
```

Con functional + integration:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RunE2E -Integration -RequireFrontend -RequireObjectStorage
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
- **Endpoint S3 SeaweedFS**: `http://127.0.0.1:8333` (credenziali
  `S3_ACCESS_KEY`/`S3_SECRET_KEY` dal `.env`)
- **Backup DB**: `pg_dump` dello schema `plenora` (vedi
  [backup-restore.md](backup-restore.md) per il modello completo)

## 8. Aggiornamento del software

```powershell
cd C:\plenora
git fetch origin
git pull --ff-only origin main
git log -1 --oneline
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 `
    -SkipWinget -InstallServices -InstallFrontend `
    -SkipAdminBootstrap -Production
```

Idempotente: rigenera i servizi (stop/refresh config/start), riesegue
le migrations al boot del backend, ricostruisce il FE. I dati (PG,
SeaweedFS) non vengono toccati.

Per aggiornare soltanto l'acceleratore XLSX, copiare nella directory
`C:\deploy` lo ZIP e il relativo `.sha256`, quindi eseguire:

```powershell
cd C:\plenora
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -XlsxExporterOnly
```

Il pacchetto piu' recente viene rilevato automaticamente. L'installer verifica
nome e SHA-256, esegue `--version` e un self-test XLSX prima della copia,
mantiene una copia di ripristino durante la sostituzione e riavvia soltanto i
servizi Plenora che erano gia' attivi. Per selezionare file diversi usare
`-XlsxExporterArchive` e `-XlsxExporterChecksum`.

Se l'installazione usa una porta frontend diversa da 80, ripassare la
stessa porta usata in installazione:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 `
    -SkipWinget -InstallServices -InstallFrontend `
    -SkipAdminBootstrap -Production -FrontendPort 8080
```

Validare sempre dopo l'update:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RequireFrontend -RequireObjectStorage
powershell -ExecutionPolicy Bypass -File scripts\validate-windows-install.ps1 `
    -RunE2E -RequireFrontend -RequireObjectStorage
```

### 8.1 Collaudo eseguibili versionati

Dopo un aggiornamento che modifica gli eseguibili, completare anche questo
collaudo applicativo con un amministratore e un utente esecutore:

1. pubblicare un progetto per ciascun tipo: Estrai, Manipola e Arricchisci;
2. verificare che un input scalare collegato visualmente a un filtro venga
   richiesto nel form e applicato all'esecuzione;
3. verificare un input file singolo e un input multi-file con due file aventi
   le stesse colonne; il risultato multi-file deve contenere le righe di
   entrambi;
4. modificare il progetto sorgente e usare **Versioni > Ripubblica dal
   progetto**; una run nuova deve indicare la nuova versione;
5. usare **Ripristina** sulla versione precedente e verificare che le run gia'
   avviate non cambino versione;
6. ripetere almeno una run come utente non amministratore e verificare il
   download dell'output.

Il gate tecnico locale del modulo puo' essere ripetuto dall'ambiente installato:

```powershell
& 'C:\ProgramData\miniforge3\condabin\conda.bat' run -n plenora `
    --cwd C:\plenora python -m pytest backend\tests2\eseguibili\unit -q
```

Questo test tecnico non sostituisce il collaudo applicativo sopra: la prova su
Windows valida anche Caddy, autenticazione, broker, worker e object storage.

## 9. Checklist operative

Installazione nuova:

- [ ] PowerShell Amministratore aperta.
- [ ] Repository clonato in `C:\plenora`.
- [ ] `git log -1 --oneline` mostra il commit atteso.
- [ ] Prima fase installer completata.
- [ ] PowerShell riaperta dopo winget.
- [ ] Seconda fase installer completata con `-Production`.
- [ ] Login superadmin riuscito.
- [ ] `validate-windows-install.ps1 -RequireFrontend -RequireObjectStorage` verde.
- [ ] `validate-windows-install.ps1 -RunE2E -RequireFrontend -RequireObjectStorage` verde.

Update:

- [ ] PowerShell Amministratore aperta.
- [ ] `git pull --ff-only origin main` completato.
- [ ] Commit verificato con `git log -1 --oneline`.
- [ ] Installer rilanciato con `-SkipAdminBootstrap`.
- [ ] Servizi `plenora-*` Running.
- [ ] `/ready` verde.
- [ ] Validazione base verde.
- [ ] Validazione e2e-v2 verde.
- [ ] Collaudo eseguibili versionati completato per Estrai, Manipola e Arricchisci.

## 10. Troubleshooting

| Sintomo | Causa e rimedio |
|---|---|
| Un servizio `plenora-*` non parte | `C:\plenora-services\<id>\<id>.err.log` (wrapper) e `.out.log` (app). In production il backend e' fail-fast sui gates: il log dice quale gate ha bloccato |
| Servizio RabbitMQ `Stopped`, zero log | `ERLANG_HOME` mancante a livello macchina (l'installer la imposta; verificare dopo update manuali di Erlang) |
| RabbitMQ `BOOT FAILED ... incompatible_feature_flags {horus, unknown_instruction}` | Erlang/OTP troppo nuovo per la versione RabbitMQ: usare la serie OTP supportata (oggi 28.x — l'installer la pinna) |
| `rabbitmqctl` → `incompatible cookie` | Cookie Erlang dell'utente ≠ LocalSystem: copiare `C:\Windows\System32\config\systemprofile\.erlang.cookie` in `%USERPROFILE%` (l'installer lo fa da solo) |
| Password `postgres` rifiutata (install winget) | Il default EDB unattended e' `postgres`; se e' stato gia' cambiato, usare la password corrente oppure il reset locale sotto. |
| Login dal browser: "Impossibile contattare il server" | Verificare che il bundle sia stato ricostruito con `-InstallFrontend` e senza `-FrontendApiUrl`; nella scheda Network del browser il login deve puntare a `/api/auth/login` sull'indirizzo del server, mai a `127.0.0.1:5004` |
| Porta 80 occupata (IIS?) | Reinstallare il FE con `-FrontendPort 8080` (ribuilda anche il bundle sull'origin nuovo) |
| `/ready` not_ready su `object_storage` | Verificare `plenora-object-storage`, endpoint `127.0.0.1:8333` e credenziali `S3_*`; l'object storage e' obbligatorio |
| Task in coda ma mai eseguiti | `plenora-worker*` giu' o `.env` broker cambiato senza restart dei servizi worker |
| SQL Server: `certificate chain ... not trusted` | Soluzione preferita: installare la CA del server in `LocalMachine\\Root`. Per server interni legacy, un superadmin puo' modificare la connessione MSSQL e scegliere **Accetta certificato non attendibile**: `Encrypt=Mandatory` resta attivo, viene disabilitata solo la verifica dell'identita' del server. Non usare l'eccezione su reti non fidate. |
| Un processo Erlang blocca update/cleanup | `epmd.exe` resta vivo dopo stop/uninstall: `Stop-Process -Name epmd -Force` |

Reset locale della password `postgres`:

```powershell
$pgData = 'C:\Program Files\PostgreSQL\17\data'
$pgBin = 'C:\Program Files\PostgreSQL\17\bin'
Copy-Item "$pgData\pg_hba.conf" "$pgData\pg_hba.conf.bak"
(Get-Content "$pgData\pg_hba.conf") `
    -replace 'scram-sha-256','trust' `
    | Set-Content "$pgData\pg_hba.conf" -Encoding ascii
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
foreach ($svc in Get-Service plenora-caddy,plenora-grafana,plenora-beat,plenora-worker*,plenora-object-storage,plenora-backend -ErrorAction SilentlyContinue) {
    $id = $svc.Name
    Stop-Service $id -Force
    if (Test-Path "C:\plenora-services\$id\$id.exe") {
        & "C:\plenora-services\$id\$id.exe" uninstall
    }
}
Remove-Item -Recurse -Force C:\plenora-services

# Dati e repo (IRREVERSIBILE: contiene DB, object storage e DATA_DIR)
Remove-Item -Recurse -Force C:\plenora-data, C:\plenora

# Piattaforma (se non serve ad altro)
winget uninstall PostgreSQL.PostgreSQL.17     # i dati DB sono in Program Files\PostgreSQL\17\data
winget uninstall Erlang.ErlangOTP             # prima: disinstallare il servizio RabbitMQ
winget uninstall CondaForge.Miniforge3
```

## 12. Vista dashboard: Grafana opzionale

La «vista dashboard» degli Eseguibili usa **Grafana OSS** incorporato nelle
pagine Plenora. E' facoltativo: senza `-InstallGrafana` l'installazione e il
Caddyfile restano quelli di sempre (nessun `/grafana`).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 `
    -SkipWinget -InstallServices -InstallFrontend -InstallGrafana -Production
```

`-InstallGrafana` richiede `-InstallFrontend` nello stesso lancio (il blocco
`/grafana` vive nel Caddyfile, che solo `-InstallFrontend` riscrive). **Agli
aggiornamenti ripassare sempre `-InstallGrafana`**: un lancio con
`-InstallFrontend` senza lo switch riscrive il Caddyfile senza `/grafana`
(l'installer lo segnala con un avviso se `plenora-grafana` esiste).

**Cosa installa.**

| Elemento | Dove |
|---|---|
| Grafana OSS 13.3.0 (zip ufficiale windows-amd64, SHA-256 fisso) | `C:\plenora-services\plenora-grafana\grafana-13.3.0\` |
| Plugin Infinity 4.1.6 (windows-amd64, SHA-256 fisso) | `C:\plenora-services\plenora-grafana\plugins\` |
| Configurazione generata (`custom.ini`, ACL Administrators+SYSTEM) | `C:\plenora-services\plenora-grafana\custom.ini` |
| Database SQLite di Grafana (dashboard, utenti, datasource) | `C:\plenora-data\grafana\` (da includere nei backup) |
| Log | `C:\plenora-services\plenora-grafana\log\` |
| Servizio Windows | `plenora-grafana` (WinSW, avvio automatico), solo `127.0.0.1:3300` |

**Spazio disco: servono 1,5 GB liberi** sul disco di `C:\plenora-services`
(zip ~500 MB, scompattato ~900 MB, plugin ~60 MB); l'installer lo controlla
prima di scaricare e si ferma se non ci sono. Una versione successiva si
installa accanto (cartella `grafana-<versione>`): la precedente si puo'
cancellare a mano dopo il collaudo.

**Installazione offline.** Il server puo' non avere internet: scaricare
altrove i due zip dagli URL fissi e passarli all'installer. Vengono
verificati con gli stessi SHA-256 del download (un file diverso ferma
l'installazione).

| Artefatto | URL | SHA-256 |
|---|---|---|
| Grafana OSS 13.3.0 | `https://dl.grafana.com/oss/release/grafana-13.3.0.windows-amd64.zip` | `133f0a0c7b905a6c7e4f3df596cf4df535ca78f1e3ef7e5be5f3f2178a51f75d` |
| Infinity 4.1.6 | `https://grafana.com/api/plugins/yesoreyeram-infinity-datasource/versions/4.1.6/download?os=windows&arch=amd64` | `a8c76c7e54ddd26adbdbc9e4a97568ebbf94316236acdba475a93c3a7a44ca18` |

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 `
    -SkipWinget -InstallFrontend -InstallGrafana `
    -GrafanaArchive C:\deploy\grafana-13.3.0.windows-amd64.zip `
    -GrafanaInfinityArchive C:\deploy\yesoreyeram-infinity-datasource-4.1.6.windows-amd64.zip
```

Grafana stesso non va mai su internet: `[plugins] preinstall_disabled = true`
(altrimenti Grafana 13 scarica in background ~550 MB di plugin
«preinstallati»), controllo aggiornamenti, telemetria e news spenti.

**Come entra un utente.** Solo utenti Plenora, nessun accesso anonimo, nessun
form di login, basic auth spenta. Caddy, per ogni richiesta a `/grafana`:
toglie dalla richiesta ogni intestazione `X-WEBAUTH-*`, chiede al backend
(`forward_auth` verso `/api/grafana/auth`) chi e' l'utente e, se il backend
risponde 2xx, passa a Grafana `X-WEBAUTH-USER` (login legato all'ID
dell'utente Plenora, `plenora-u-<id>`), `X-WEBAUTH-NAME` (nome visualizzato) e
`X-WEBAUTH-ROLE` (sempre: Admin al superadmin, Viewer agli altri; Grafana lo
riapplica a ogni accesso), organizzazione «Plenora Dashboard». Prima di
rispondere il backend allinea i team dell'utente in Grafana (`utenti-<gruppo>`,
`admin-<gruppo>`); ogni dashboard sta in una cartella propria visibile solo ai
gruppi che vedono l'Eseguibile in Plenora. Altrimenti la risposta del backend
(401, o 503 se Grafana non risponde) torna al browser. Le risposte di
`/grafana` hanno intestazioni proprie (CSP che tiene ogni caricamento sullo
stesso origin e `frame-ancestors 'self'`, niente `X-Frame-Options`,
`Referrer-Policy: no-referrer`); tutto il resto ha quelle di Plenora, la cui CSP
ammette iframe solo dallo stesso origin (`frame-src 'self'`).

Da sapere: Grafana tiene in cache le autorizzazioni per circa 30 secondi (una
condivisione tolta o un admin spostato perdono l'accesso entro 30 s; con
`[rbac] permission_cache = false` nel `custom.ini` l'effetto e' immediato).
Gli utenti `plenora-u-*` in Grafana non si cancellano a mano: li governa
Plenora (per togliere un accesso si agisce sull'utente in Plenora).

**Bootstrap** (idempotente, `scripts\grafana_bootstrap.py`, a ogni lancio):
rinomina l'organizzazione 1 in «Plenora Dashboard», crea il service account
`plenora` (Admin) e scrive il suo token in `backend\.env` come
`GRAFANA_SERVICE_TOKEN` (riusato se ancora valido; se perso se ne crea uno e i
vecchi vengono revocati), genera se assenti `GRAFANA_DATA_SECRET` e
`DASHBOARD_PASS_SECRET`, crea o aggiorna il datasource Infinity (uid fisso
`plenora-infinity`) che puo' chiamare solo `http://127.0.0.1:5004/api/dash/` e
manda `X-Grafana-Secret` (campo cifrato). L'installer scrive nel `.env` solo
questi tre segreti: ogni impostazione (es. `GRAFANA_ENABLED`) resta nei default
di `config.py`. Il bootstrap si autentica con lo stesso auth proxy, da
localhost, col login amministratore casuale scritto nel `custom.ini`.

**Rischi da conoscere.**

- **Impersonazione da processi locali.** Grafana si fida di `X-WEBAUTH-USER`
  da `127.0.0.1` (la whitelist dell'auth proxy): qualunque processo sul server
  che apre `http://127.0.0.1:3300` puo' presentarsi come un utente qualsiasi,
  compreso l'amministratore. La porta non e' raggiungibile dalla rete (solo
  loopback, verificato dal validatore), ma il server va trattato come fidato:
  niente utenti interattivi o servizi di terzi non necessari.
- **Token del service account potente.** `GRAFANA_SERVICE_TOKEN` e' Admin
  dell'organizzazione: crea e modifica dashboard, datasource, utenti. Vive solo
  nel `.env` (ACL Administrators+SYSTEM). Se esce: revocarlo da Grafana o
  cancellarlo dal `.env` e rilanciare l'installer (ne crea uno nuovo e revoca
  gli altri).
- **Segreti nel `custom.ini`.** `secret_key` (cifra i campi segreti dei
  datasource: non cambiarla, o diventano illeggibili) e login/password
  dell'amministratore iniziale; ACL Administrators+SYSTEM, riletti a ogni
  lancio. Se il `custom.ini` va perso ma `C:\plenora-data\grafana` resta, il
  bootstrap si ferma («non e' amministratore»): ripristinare il file dal
  backup oppure, se le dashboard sono rigenerabili, cancellare la cartella dati.
- **Log di Caddy.** Oggi Caddy non scrive log di accesso. Se si abilitano,
  filtrare la **query string** delle richieste `/grafana` (contiene il
  lasciapassare della dashboard), per esempio con un `log` e il filtro
  `query` di Caddy che cancella il parametro, prima di conservarli.
