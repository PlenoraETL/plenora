# Install Plenora — Windows nativo (single-box)

Runbook ufficiale dell'installazione nativa su Windows (Server 2022/2025
o Windows 11 Pro). Niente Docker, niente WSL: PostgreSQL, RabbitMQ,
backend, worker Dramatiq, beat, MinIO opzionale e frontend girano come
**servizi Windows** con avvio automatico al boot.

Collaudato end-to-end su Windows Server 2025 (2026-07-04): vedi la
sessione di scoping in [scoping-windows-native.md](scoping-windows-native.md)
con la tabella esiti e le lezioni incapsulate nello script.

## Prerequisiti

- Windows con `winget` disponibile (Server 2025 e Windows 11 lo includono)
- PowerShell **da Amministratore**
- Rete verso: winget sources, GitHub releases (RabbitMQ/WinSW),
  dl.min.io (se MinIO), caddyserver.com (se frontend), conda-forge, PyPI, npm

## Installazione completa (one-shot)

```powershell
git clone https://github.com/PlenoraETL/plenora.git C:\plenora
cd C:\plenora

# 1) Pacchetti di piattaforma (PostgreSQL 17, Erlang 28, Miniforge, Node, Git)
powershell -ExecutionPolicy Bypass -File scripts\install.ps1

# 2) NUOVA shell admin (PATH aggiornati), poi tutto il resto:
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 `
    -SkipWinget -InstallServices -InstallMinio -InstallFrontend -Production `
    -WorkerProcesses 1 -WorkerThreads 4
```

Il flusso chiede: password del superuser `postgres` (con winget
unattended di solito e' sconosciuta: lo script sa resettarla — vedi
runbook scoping §2) e username/password del **superadmin** (minimo 12
caratteri). A fine corsa: 5-6 servizi `plenora-*` attivi, login dal
browser su `http://127.0.0.1/`.

## Parametri principali

| Parametro | Default | Effetto |
|---|---|---|
| `-InstallServices` | off | Servizi WinSW: `plenora-backend`, `plenora-worker`, `plenora-beat` |
| `-WorkerProcesses` / `-WorkerThreads` | 1 / 4 | Parallelismo del worker Dramatiq |
| `-AdminUsername` / `-AdminPassword` | prompt | Superadmin (o env `PLENORA_BOOTSTRAP_ADMIN_*`); `-SkipAdminBootstrap` per saltare |
| `-InstallMinio` | off | MinIO come servizio + bucket `plenora-runtime`/`plenora-backups`, provider `minio` |
| `-StoragePolicy` | `hybrid` | `db` \| `hybrid` \| `object_storage` (con `-InstallMinio`) |
| `-InstallFrontend` | off | Build Vite + Caddy come servizio (statici + proxy `/api`) |
| `-FrontendPort` | 80 | Porta di Caddy; la build FE viene cablata su quell'origin |
| `-Production` | off | `ENVIRONMENT=production` coi gates attivi (richiede object storage) |

Non-interattivo: `PLENORA_BOOTSTRAP_ADMIN_USERNAME/_PASSWORD/_GROUP_ID`,
`PLENORA_INSTALL_WORKER_PROCESSES/_THREADS`, `-PgSuperPassword`.

## Il modello single-box (production)

`-Production` scrive `PLENORA_SINGLE_NODE=true`: e' la dichiarazione
esplicita che broker e servizi convivono su una macchina — sopprime
SOLO il gate anti-loopback sul broker. Tutti gli altri production
gates restano attivi (object storage obbligatorio, rate-limiter
distribuiti su PostgreSQL fail-closed, beat leader fail-closed).

NB secrets: in questo modello i secrets vivono in `backend/.env` con
ACL ristretta (Administrators+SYSTEM). Il loader dotenv li accetta
perche' `ENVIRONMENT` non e' nel process env dei servizi; hardening
futuro: env di sistema per-servizio.

## Operativita'

```powershell
Get-Service plenora-*                 # stato
Restart-Service plenora-backend       # riavvii mirati
# log (rotazione automatica):
#   C:\plenora-services\<servizio>\<servizio>.out.log / .err.log
curl.exe -s http://127.0.0.1/ready    # readiness attraverso Caddy
```

- **Stop pulito**: `Stop-Service plenora-beat` rilascia il leader lock
  su PG (Ctrl+C di WinSW → SIGINT del runner). Collaudato.
- **Riavvio macchina**: tutti i servizi ripartono da soli. Collaudato.
- **Aggiornamento codice**: `git pull` + rilancio dello script con gli
  stessi switch (idempotente: rigenera i servizi e riavvia).

## Trappole note (gia' gestite dallo script)

- RabbitMQ non e' piu' su winget → release GitHub, NSIS `/S`
- `ERLANG_HOME` va impostata (Machine) PRIMA di installare RabbitMQ
- Erlang/OTP va pinnato alla serie supportata da RabbitMQ (oggi 28.x)
- `rabbitmqctl` richiede il cookie Erlang di LocalSystem (sync automatico)
- password `postgres` di winget unattended non prevedibile → reset via
  trust temporaneo su `pg_hba.conf`
- il browser deve usare LO STESSO hostname della build FE
  (`http://127.0.0.1`); l'installer scrive comunque CORS per
  `127.0.0.1` e `localhost`
