# Scoping: Plenora nativo su Windows Server 2025 (VM VMware)

> Documento di LAVORO per la sessione di scoping del packaging nativo
> Windows (2026-07-04). Obiettivo: bring-up manuale dello stack completo
> (PostgreSQL + RabbitMQ + backend + worker Dramatiq + beat) su una VM
> pulita, annotando ogni comando e ogni rottura. L'esito fonda il vero
> `install.ps1` (oggi stub deliberato).
>
> RISCHIO n.1 da validare: il worker `python -m dramatiq` su Windows
> (multiprocessing spawn, niente segnali POSIX). Se il CLI non regge,
> piano B al §7.

Tutti i comandi in **PowerShell da Amministratore**, salvo dove detto.

---

## 0. Prerequisiti di piattaforma

```powershell
# Verifica winget (Windows Server 2025 lo include)
winget --version

# Git + strumenti
winget install --accept-package-agreements --accept-source-agreements Git.Git
winget install OpenJS.NodeJS.LTS
```

Se `winget` manca: installare App Installer dallo Store o usare i
download manuali indicati nei paragrafi.

## 1. Repo sulla VM

Opzione A (rete + GitHub):
```powershell
git clone https://github.com/PlenoraETL/plenora.git C:\plenora
# repo privato: servira' un PAT o `gh auth login`
```
Opzione B (niente creds sulla VM): zip del repo dall'host → cartella
condivisa VMware / drag-and-drop → estrarre in `C:\plenora`.

## 2. PostgreSQL 17

```powershell
winget install PostgreSQL.PostgreSQL.17
# (fallback manuale: installer EDB da postgresql.org)
```
Poi (la password del superuser la chiede l'installer; annotarla):
```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -c "CREATE USER plenora WITH PASSWORD 'plenora' CREATEDB;"
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -c "CREATE DATABASE plenora OWNER plenora;"
```
CHECKPOINT: `psql -U plenora -d plenora -c "SELECT 1;"` → `1`.

## 3. Erlang + RabbitMQ

RabbitMQ su Windows richiede Erlang/OTP PRIMA:
```powershell
winget install Erlang.ErlangOTP
winget install VMware.RabbitMQ   # se l'id non risolve: cerca `winget search rabbitmq`
```
L'installer registra il servizio Windows `RabbitMQ` e lo avvia
(listener amqp su 5672, guest/guest valido solo da localhost — per lo
scoping va bene, tutto e' sulla VM).

CHECKPOINT:
```powershell
Get-Service RabbitMQ
Test-NetConnection 127.0.0.1 -Port 5672   # TcpTestSucceeded: True
```

## 4. Python + stack geospaziale (Miniforge / conda-forge)

Il GDAL nativo arriva da conda-forge, come nel Dockerfile Linux:
```powershell
winget install CondaForge.Miniforge3
# nuova shell PowerShell dopo l'install, poi:
conda create -y -n plenora -c conda-forge python=3.12 gdal=3.13.0 proj-data pyogrio geopandas-base pyproj shapely pandas numpy pyarrow openpyxl pip
conda activate plenora
```
NB: su linux-64 e' servito il pin `libjxl=0.11.*` (gdal 3.13 linkava
libjxl.so.0.11). Su win-64 il resolver puo' comportarsi diversamente:
se `gdalinfo --version` fallisce per DLL mancanti, provare ad
aggiungere lo stesso pin al create.

CHECKPOINT:
```powershell
python --version          # 3.12.x
gdalinfo --version        # GDAL 3.13.0
python -c "import pyogrio, geopandas, pyproj, shapely; print('geo ok')"
```

## 5. Dipendenze pip (senza doppiare lo stack conda)

Replica del filtro del Dockerfile (i pacchetti geo restano di conda):
```powershell
cd C:\plenora\backend
$conda = 'geopandas','numpy','openpyxl','pandas','pyarrow','pyogrio','pyproj','shapely'
Get-Content requirements.txt | Where-Object {
    $line = $_.Trim(); $pkg = ($line -split '[<>=!~\[]')[0].Trim()
    -not ($conda -contains $pkg)
} | Set-Content -Encoding ascii requirements-pip.txt
pip install -r requirements-pip.txt
```
CHECKPOINT: `python -c "import fastapi, dramatiq, psycopg, pika, apscheduler, croniter; print('deps ok')"`.

Attesi possibili intoppi Windows: `pyodbc` (wheel ok, ma il driver
`msodbcsql18` va installato a parte SOLO se serve SQL Server — per lo
scoping si puo' saltare), `oracledb`/`pymysql` (wheel puri, ok).

## 6. Configurazione `.env`

```powershell
@'
ENVIRONMENT=development
DATABASE_URL=postgresql://plenora:plenora@127.0.0.1:5432/plenora
DB_SCHEMA=plenora
DRAMATIQ_BROKER_URL=amqp://guest:guest@127.0.0.1:5672
JWT_SECRET_KEY=scoping-vm-jwt-secret-0001
PLENORA_ENCRYPTION_KEY=scoping-vm-encryption-key-0001
DATA_DIR=C:\plenora-data
'@ | Set-Content -Encoding ascii C:\plenora\backend\.env
New-Item -ItemType Directory -Force C:\plenora-data | Out-Null
```
`ENVIRONMENT=development` DI PROPOSITO: niente production gates
(object storage, rate limiter distribuiti) durante lo scoping.

## 7. Backend (finestra PowerShell n.1)

```powershell
cd C:\plenora\backend
conda activate plenora
python -m uvicorn app.main:app --host 127.0.0.1 --port 5004
```
Al boot: bootstrap 4-step (validate → storage config → PG pool +
migrations → broker ping). CHECKPOINT (altra finestra):
```powershell
curl.exe -s http://127.0.0.1:5004/health
curl.exe -s http://127.0.0.1:5004/ready
```
`/ready` deve dare ok su postgres+broker+migrations.

## 8. Worker Dramatiq (finestra n.2) — IL RISCHIO N.1

```powershell
cd C:\plenora\backend
conda activate plenora
python -m dramatiq workers.dramatiq_app --processes 1 --threads 4
```
Cosa osservare e riportare TESTUALMENTE:
- boot: righe `before_worker_boot` / bootstrap (pool PG, tabelle, ready);
- eventuali traceback su multiprocessing/spawn/segnali;
- se il processo resta su e stabile ≥60s.

**Piano B se il CLI esplode** (worker in-process, niente
multiprocessing del CLI):
```powershell
python -c "import workers.dramatiq_app as m; import dramatiq; w = dramatiq.Worker(dramatiq.get_broker(), worker_threads=4); w.start(); import time; time.sleep(3600)"
```
Se serve il piano B, e' un dato FONDANTE per il packaging (il servizio
Windows wrappera' la Worker API, non il CLI).

## 9. Smoke round-trip (finestra n.3)

```powershell
cd C:\plenora\backend
conda activate plenora
python - <<'PY'
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
PY
```
CHECKPOINT: `SMOKE: SUCCESS`. (NB: heredoc `<<'PY'` e' sintassi bash —
in PowerShell salvare il blocco in `smoke.py` e lanciare `python smoke.py`.)

## 10. Beat (finestra n.4)

```powershell
cd C:\plenora\backend
conda activate plenora
python -m workers.beat
```
CHECKPOINT: log di acquisizione leader; su PG:
```powershell
psql -U plenora -d plenora -c "SELECT * FROM plenora.beat_leader_locks;"
```
1 riga. Poi Ctrl+C → la riga sparisce (release del lock). NB: su
Windows non c'e' SIGTERM — annotare COME muore il processo con Ctrl+C
(il runner gestisce SIGINT).

## 11. Frontend (opzionale per lo scoping v1)

```powershell
cd C:\plenora\frontend
npm ci
npm run build
# servire dist/ con qualunque static server; login e giro veloce UI
```

---

## Esiti da annotare (compilare durante la sessione)

| # | Step | Esito | Note |
|---|---|---|---|
| 2 | PostgreSQL 17 | | |
| 3 | Erlang+RabbitMQ | | |
| 4 | conda env + GDAL | | |
| 5 | pip deps | | |
| 7 | backend boot + /ready | | |
| 8 | worker CLI dramatiq | | CLI o piano B? |
| 9 | smoke ping | | |
| 10 | beat + leader lock | | |
| 11 | FE build | | |

Decisioni che discendono dagli esiti: forma del servizio (NSSM vs
wrapper pywin32 vs Task Scheduler), CLI vs Worker API per il worker,
packaging dell'env python (conda-pack? installer Miniforge scriptato?),
bootstrap DB/broker nell'installer.
