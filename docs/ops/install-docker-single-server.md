# Single-Server Docker Install

Canonical path for one host running the full Plenora stack with Docker Compose.

This procedure uses the root `docker-compose.yml` and starts:

- PostgreSQL;
- Redis;
- backend API;
- Celery worker;
- Celery beat;
- frontend static bundle served by nginx.

## Acceptance Gate

The install is green only when:

- `docker compose config --quiet` succeeds;
- backend and frontend images build;
- Postgres and Redis become healthy;
- backend `/health` and `/ready` return HTTP 200;
- seeded API smoke succeeds: login, `/auth/me`, project create/list/delete,
  and logout;
- the direct Postgres postcondition confirms schema `plenora`, table
  `plenora.schema_migrations`, and at least one applied migration row;
- worker completes `cleanroom.ping`;
- beat starts and stays alive;
- frontend serves the SPA shell;
- logs show no startup error.

## 1. Prerequisites

Target: fresh Linux host with Docker Engine and Docker Compose v2.

```bash
uname -a
cat /etc/os-release
docker --version
docker compose version
git --version
```

## 2. Clone

```bash
git clone https://github.com/PlenoraETL/plenora.git ~/plenora
cd ~/plenora
git rev-parse HEAD
```

Record the full commit hash in the validation report.

## 3. Environment

Automated path:

```bash
bash scripts/install-docker.sh --check
bash scripts/install-docker.sh
```

Windows with Docker Desktop:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-docker.ps1 -Check
powershell -ExecutionPolicy Bypass -File scripts/install-docker.ps1
```

Fresh Linux Debian/Ubuntu host:

```bash
bash scripts/install-docker.sh --install-deps
```

Fresh Windows host with winget:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-docker.ps1 -InstallDeps
```

Both installers:

- create `.env.docker` from `.env.docker.example` if missing;
- preserve existing non-empty secrets;
- generate `PLENORA_ENCRYPTION_KEY` and `JWT_SECRET_KEY` if empty;
- create the external Docker bridge network `plenora` if missing;
- set `OBJECT_STORAGE_ENABLED=false` when `STORAGE_POLICY=db` and the key is
  missing, so the local DB-only scaffold does not require cloud credentials;
- prompt for the number of Celery worker containers, defaulting to one worker;
- prompt for a bootstrap superadmin username/password on interactive installs,
  then create or update that user after the backend is healthy;
- run `docker compose config --quiet`;
- build backend and frontend images;
- start Postgres, Redis, backend, worker, beat, and frontend;
- check Python DB providers: PostgreSQL, MySQL/MariaDB, Oracle, and SQL Server
  Python driver;
- run health, readiness, frontend, and worker smoke checks unless smoke is
  skipped.

Manual path:

```bash
cp .env.docker.example .env.docker
python3 -c "from cryptography.fernet import Fernet; print('PLENORA_ENCRYPTION_KEY=' + Fernet.generate_key().decode())" >> .env.docker
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env.docker
chmod 600 .env.docker
```

Review `.env.docker` before continuing. Do not commit it.

For a remote host, set the frontend build URL before building:

```bash
export FRONTEND_VITE_API_URL=http://<host-ip-or-dns>:5004
```

With the installer:

```bash
bash scripts/install-docker.sh --frontend-api-url http://<host-ip-or-dns>:5004
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-docker.ps1 -FrontendApiUrl http://<host-ip-or-dns>:5004
```

For local validation on the same machine, the compose default
`http://localhost:5004` is correct.

Interactive installs ask for the first superadmin credentials. The username
defaults to `admin`; the password is not written to `.env.docker`.

For non-interactive installs:

```bash
export PLENORA_BOOTSTRAP_ADMIN_USERNAME=admin
export PLENORA_BOOTSTRAP_ADMIN_PASSWORD='<strong-password>'
bash scripts/install-docker.sh
```

To skip user creation:

```bash
bash scripts/install-docker.sh --skip-admin-bootstrap
```

Interactive installs ask how many Celery worker containers to run. The default
is `1`. For non-interactive installs:

```bash
export PLENORA_WORKER_REPLICAS=2
bash scripts/install-docker.sh
```

The single-server stack attaches every service to the external Docker network
`plenora`. The Linux installer creates it with subnet `172.28.0.0/16` if it is
missing. Override these defaults before running the installer if that subnet
conflicts with the host:

```bash
export PLENORA_DOCKER_NETWORK=plenora
export PLENORA_DOCKER_SUBNET=172.28.0.0/16
bash scripts/install-docker.sh
```

The installer and validation script default to `COMPOSE_FILE=docker-compose.yml`
so local `docker-compose.override.yml` files do not affect a clean install.

## 4. Database Provider Libraries

The backend image installs the Python database providers from
`backend/requirements.txt`:

| Provider | Python package | Native/system dependency | Status in default backend image |
|---|---|---|---|
| PostgreSQL | `psycopg[binary]` | `libpq`, `postgresql-client-17` | Included. |
| MySQL/MariaDB | `pymysql` | None for the pure-Python driver | Included. |
| Oracle | `oracledb` | None in thin mode | Included. |
| SQL Server | `pyodbc` | Microsoft ODBC Driver 18 or 17 plus unixODBC | Python package included; native ODBC driver must be present for real SQL Server connections. |

Provider import check after the backend is running:

```bash
docker compose exec -T backend python -c "import psycopg, pymysql, oracledb, pyodbc; print('python db providers ok'); print('odbc_drivers=', pyodbc.drivers())"
```

Expected:

- imports succeed;
- `odbc_drivers` contains `ODBC Driver 18 for SQL Server` or
  `ODBC Driver 17 for SQL Server` if SQL Server connections are required.

If `odbc_drivers=[]`, PostgreSQL/MySQL/Oracle can still work, but SQL Server
connection tests will return a controlled failure telling the operator to
install the Microsoft ODBC driver.

To make the installer fail when SQL Server ODBC is missing:

```bash
bash scripts/install-docker.sh --require-sqlserver-odbc
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-docker.ps1 -RequireSqlServerOdbc
```

For a SQL Server-capable custom backend image on Debian 12/bookworm, use a
derived image or add the equivalent package install to `backend/Dockerfile`:

```Dockerfile
FROM plenora-backend:scaffold
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg unixodbc \
    && install -d /usr/share/keyrings \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*
USER plenora:plenora
```

Then rebuild and verify:

```bash
docker compose build backend
docker compose up -d --wait backend
docker compose exec -T backend python -c "import pyodbc; print(pyodbc.drivers())"
```

Oracle note: `oracledb` uses thin mode by default and does not require Oracle
Instant Client for normal TCP connections. Only install Instant Client if a
specific thick-mode feature or legacy network setup requires it.

## 5. Validate Compose

```bash
docker compose config --quiet
```

## 6. Build And Start

```bash
docker compose build backend frontend
docker compose up -d postgres redis
docker compose up -d --wait backend
docker compose up -d --scale worker=1 worker beat
docker compose up -d --wait frontend
```

## 7. Health And Readiness

```bash
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
docker compose exec -T postgres psql -U plenora -d plenora -tAc "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'plenora');"
docker compose exec -T postgres psql -U plenora -d plenora -tAc "SELECT to_regclass('plenora.schema_migrations') IS NOT NULL;"
docker compose exec -T postgres psql -U plenora -d plenora -tAc "SELECT count(*) FROM plenora.schema_migrations;"
curl -fsS http://127.0.0.1:3000/
docker compose ps
```

If `/ready` fails:

```bash
curl -sS http://127.0.0.1:5004/ready
docker compose ps
docker compose logs --tail 200 backend
docker compose logs --tail 200 postgres redis
docker compose logs --tail 200 worker
docker compose logs --tail 200 beat
docker compose exec -T backend python -c "from app.observability.health import gather_readiness; print(gather_readiness().body)"
```

Use the `/ready` response first: it names the failing readiness check.

## 8. Worker Round-Trip

```bash
docker compose exec -T worker python -c "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))"
```

Expected: `pong`.

## 9. API Smoke

Preferred runner:

```bash
bash scripts/docker-validate.sh --dry-run
bash scripts/docker-validate.sh
```

The script is non-destructive. It does not remove containers, named volumes,
or `.env.docker`.

## 10. Frontend

Open:

```text
http://<host-ip-or-dns>:3000/
```

Confirm:

- login screen loads;
- browser can reach the backend API origin baked into the frontend build;
- no CORS or network errors appear in browser dev tools.

If the frontend points to the wrong API URL, rebuild it:

```bash
FRONTEND_VITE_API_URL=http://<host-ip-or-dns>:5004 docker compose build frontend
docker compose up -d --wait frontend
```

## 11. Stop

Non-destructive stop:

```bash
docker compose stop
```

Disposable-host reset only:

```bash
docker compose down -v
rm -f .env.docker
```

## Stop Conditions

Stop and report output when:

- `docker compose config` fails;
- image build fails;
- Postgres or Redis stays unhealthy;
- `/ready` returns 503;
- worker ping times out;
- frontend serves but calls the wrong API origin;
- logs show repeated startup exceptions.
