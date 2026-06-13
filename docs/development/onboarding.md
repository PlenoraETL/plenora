# Onboarding

Setup guide for a new Plenora development environment. Use this together
with the root [README](../../README.md) and the operations docs.

## Prerequisites

- Python 3.12
- Node.js 20+ and npm 10+
- PostgreSQL 14+
- Redis 7+
- Git 2.40+
- Optional native libraries for geospatial formats, depending on the work:
  GDAL, Pyogrio, Shapely, libspatialindex, libpq

Docker is supported as a cleanroom target through the compose path documented
in the ops install guide.

## Clone

```bash
git clone https://github.com/PlenoraETL/plenora.git plenora
cd plenora
```

Use separate shells for backend API, Celery worker, Celery beat, and
frontend.

## PostgreSQL

Local example:

```bash
sudo -u postgres psql
CREATE USER plenora WITH PASSWORD 'plenora';
CREATE DATABASE plenora OWNER plenora;
GRANT ALL PRIVILEGES ON DATABASE plenora TO plenora;
```

Docker example:

```bash
docker run -d --name plenora-pg \
  -e POSTGRES_USER=plenora \
  -e POSTGRES_PASSWORD=plenora \
  -e POSTGRES_DB=plenora \
  -p 5432:5432 postgres:14-alpine
```

Migrations are applied by the backend startup path. Do not edit production
schemas manually.

## Redis

```bash
redis-server --port 6379
```

or:

```bash
docker run -d --name plenora-redis -p 6379:6379 redis:7-alpine
```

Verify:

```bash
redis-cli ping
```

## Backend Environment

Create `backend/.env` locally. Do not commit it.

Minimum development variables:

```bash
PLENORA_ENCRYPTION_KEY=...
JWT_SECRET_KEY=...
DATABASE_URL=postgresql://plenora:plenora@localhost:5432/plenora
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
ENVIRONMENT=development
LOG_LEVEL=INFO
INTERMEDIATE_PERSISTENCE_MODE=metadata_only
```

Generate local secrets:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload --port 5004
```

Windows PowerShell activation:

```powershell
.\venv\Scripts\Activate.ps1
```

Worker and beat:

```bash
cd backend
celery -A workers.celery_app worker --loglevel=info
python -m workers.beat
```

Smoke checks:

```bash
curl http://localhost:5004/health
curl http://localhost:5004/ready
```

OpenAPI: `http://localhost:5004/docs`.

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Default UI: `http://localhost:5173`.

## Docker Cleanroom

Docker scaffolding is available through the root Docker files and the install
docs. Treat Docker as release-ready only after the acceptance gate in
`install-docker.md` has passed on a clean Linux host.

See:

- [Docker Install](../ops/install-docker.md)
- [Runtime Prerequisites](../ops/runtime-prereqs.md)

## Test Policy

Use targeted tests during normal development:

```bash
cd backend
pytest tests2/<domain>/<file>.py -v
python -m ruff check backend/app backend/workers backend/scripts backend/tests2 scripts --select F401,F403,F405,F811,F821,F841
python -m pyflakes backend/app backend/workers backend/scripts backend/tests2 scripts

cd frontend
npx vitest run tests/<area>/<file>.test.jsx
npm run build
```

Run wider suites when touching shared runtime, auth, installation, or
release-candidate paths.

## First Admin User

The first admin creation path is deployment-specific. For local work, use
the current backend admin/user provisioning helper or an approved migration
script. Do not hard-code bootstrap credentials in committed files.
