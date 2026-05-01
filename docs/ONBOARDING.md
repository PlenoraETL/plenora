# Onboarding — URBY-ETL

Setup completo per nuovo dev. Backend + Frontend + dipendenze locali.
Versione estesa del Quickstart in [README root](../README.md#quickstart-dev).

---

## Prerequisiti

| Tool | Versione | Note |
|---|---|---|
| Python | 3.12 | venv consigliato |
| Node.js | 20+ | con npm 10+ |
| PostgreSQL | 14+ | locale o Docker |
| Redis | 7+ | locale o Docker |
| Git | 2.40+ | |
| OS | Linux / macOS / Windows | su Windows: WSL2 raccomandato per Celery |

Optional (per integrazioni opzionali):
- **Oracle Instant Client** se devi testare connessioni Oracle (`oracledb` Python driver).
- **GDAL/Fiona** per export gpkg/shp (`pip install fiona shapely`).
- **MinIO** locale come alternativa a S3 per object storage runtime
  (vedi [ADR-0015](../backend/docs/architecture/decisions/0015-2026-04-22-minio-object-storage-provider.md)).

---

## 1. Clone e setup base

```bash
git clone https://github.com/PlenoraETL/plenora.git URBY-ETL
cd URBY-ETL
```

Apri 2 shell (backend + frontend) o 4 (backend + worker + beat + frontend).

---

## 2. PostgreSQL

### Locale (Linux/macOS)

```bash
# Crea utente + database
sudo -u postgres psql <<'SQL'
CREATE USER dataflow WITH PASSWORD 'dataflow';
CREATE DATABASE dataflow_test OWNER dataflow;
GRANT ALL PRIVILEGES ON DATABASE dataflow_test TO dataflow;
SQL
```

### Docker

```bash
docker run -d --name urby-pg \
  -e POSTGRES_USER=dataflow -e POSTGRES_PASSWORD=dataflow \
  -e POSTGRES_DB=dataflow_test \
  -p 5432:5432 postgres:14-alpine
```

### Schema

Le migration vengono applicate automaticamente al primo startup backend
(`app.main:apply_pending_migrations` chiamato in `lifespan`). Niente
script manuale richiesto.

---

## 3. Redis

```bash
# Locale
redis-server --port 6379 --daemonize yes

# Docker
docker run -d --name urby-redis -p 6379:6379 redis:7-alpine
```

Test connessione:

```bash
redis-cli ping     # -> PONG
```

---

## 4. Env vars

Crea `backend/.env` (gitignored). Lista completa in
[`backend/docs/ops/runtime-prereqs.md`](../backend/docs/ops/runtime-prereqs.md).

```bash
# Encryption + JWT (genera con i comandi sotto)
PLENORA_ENCRYPTION_KEY=...
JWT_SECRET_KEY=...

# DB
DATABASE_URL=postgresql://dataflow:dataflow@localhost:5432/dataflow_test

# Redis (Celery + JWT blacklist)
REDIS_URL=redis://localhost:6379/0

# Optional
ENVIRONMENT=development
LOG_LEVEL=INFO
INTERMEDIATE_PERSISTENCE_MODE=metadata_only   # default produzione
```

### Generare le chiavi

```bash
# PLENORA_ENCRYPTION_KEY (Fernet)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT_SECRET_KEY (urlsafe 48-byte)
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

> ⚠️ **NON committare `.env`**. Per produzione, usa il secret manager
> (vedi [`backend/docs/compliance/secrets-management.md`](../backend/docs/compliance/secrets-management.md)).

---

## 5. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# Server (dev mode)
uvicorn app.main:app --reload --port 5004

# In altre 2 shell (con venv attivato):
celery -A workers.celery_app worker --loglevel=info
celery -A workers.celery_app beat --loglevel=info
```

Verifica:

```bash
curl http://localhost:5004/health
curl http://localhost:5004/ready
```

OpenAPI docs: `http://localhost:5004/docs`.

---

## 6. Frontend

```bash
cd frontend
npm install
npm run dev
```

Default: `http://localhost:5173`. Configurato per proxy verso
backend `:5004`. Vedi [`frontend/README.md`](../frontend/README.md).

---

## 7. Primo login

Default admin user va creato manualmente dal DB al primo setup.

```bash
cd backend
python -c "
from app.db.config_db_service import create_user
create_user(
    username='admin', password='admin-dev-only-change-me',
    role='superadmin', group_id='default',
)
print('admin created')
"
```

> Cambia subito la password al primo login. Vedi
> [`backend/docs/ops/runbook.md`](../backend/docs/ops/runbook.md) per
> recovery admin lockout (sezione 2.1).

---

## 8. Test policy locale

**Niente full suite locale per default.** Vedi
[CONTRIBUTING.md](../CONTRIBUTING.md) per la policy completa.

Scenari ricorrenti:

```bash
# Backend mirato (file specifico)
cd backend
pytest tests2/<dominio>/<file>.py -v

# Backend wider (1 dominio)
pytest tests2/security/ tests2/orchestrator/

# Frontend mirato
cd frontend
npx vitest run tests/<area>/<file>.test.{js,jsx}

# Frontend full (CI già lo fa, ma se serve in locale)
npm run test
```

---

## 9. Troubleshooting

### `ModuleNotFoundError: No module named 'app'` su pytest

Backend tests vanno eseguiti con `cwd = backend/`:

```bash
cd backend && pytest tests2/...
```

`tests2/pytest.ini` ha `pythonpath = ..` che sistema l'import path dal
parent.

### `psycopg2.OperationalError: could not connect to server`

- Verifica PG running: `pg_isready -h localhost -p 5432`.
- Controlla `DATABASE_URL` nel `.env`.
- Su Linux/macOS, l'utente PG default può richiedere `sudo -u postgres`.

### `redis.exceptions.ConnectionError`

- Verifica Redis running: `redis-cli ping`.
- Controlla `REDIS_URL` nel `.env`.

### `JWT_SECRET_KEY non configurata`

In `ENVIRONMENT=development` viene generato un secret random per la
sessione (warning emesso). I token diventano invalidi al riavvio.
Imposta `JWT_SECRET_KEY` nel `.env` per persistenza.

### Vite dev server non si connette al backend

Verifica che il backend sia su `:5004` e che il proxy in
`frontend/vite.config.js` punti a quello. Default funziona out-of-box
con `uvicorn ... --port 5004`.

### Celery worker non vede task

- Verifica che broker (Redis) sia raggiungibile.
- Verifica che il worker sia stato lanciato dal `cwd = backend/` con
  venv attivato.
- Tasks registrati: `celery -A workers.celery_app inspect registered`.

### Test scheduler `time.sleep` lenti / flake

I test scheduler usano `time.sleep` reale per testare thread heartbeat.
Default totale ~3-5 sec. Su CI loaded può salire a 10 sec. Pattern noto
documentato in audit Phase 1 (R-05). Non blocker.

---

## 10. Riferimenti

- [README root](../README.md) — overview Plenora.
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — boundaries + execution flow.
- [docs/ROADMAP.md](ROADMAP.md) — residui audit + Phase 2+.
- [backend/README.md](../backend/README.md) — setup BE dettagliato + ADR refs.
- [frontend/README.md](../frontend/README.md) — setup FE dettagliato.
- [backend/docs/ops/runbook.md](../backend/docs/ops/runbook.md) — recovery procedures.
- [backend/docs/ops/incident_response.md](../backend/docs/ops/incident_response.md) — incident framework.
