# Clean Install, Non-Docker

This is the canonical single-machine install path for Plenora.

Target: fresh Ubuntu 24.04 host, no containers, local PostgreSQL and Redis
unless stated otherwise.

## Status

This is the primary go-ready installation path. It still requires a real
fresh-machine validation report before the project can claim 1.0 install
readiness.

Docker is documented separately in [install-docker.md](install-docker.md).

## Acceptance Gate

The install is green only when all of these are true:

- backend starts on `127.0.0.1:5004`;
- `GET /health` returns HTTP 200;
- `GET /ready` returns HTTP 200;
- Celery worker receives and completes `cleanroom.ping`;
- Celery beat starts and emits heartbeat/readiness signal;
- frontend starts and can reach the backend;
- one smoke pipeline can be created, executed, previewed, and downloaded;
- the exact commit hash and command outputs are captured in the validation
  report.

## 1. Host Packages

```bash
sudo apt update
sudo apt install -y \
  python3.11 python3.11-venv python3.11-dev \
  build-essential libpq-dev \
  postgresql postgresql-client \
  redis-server \
  libgdal-dev libmagic1 \
  curl ca-certificates git \
  nodejs npm
```

Verify:

```bash
python3.11 --version
psql --version
redis-server --version
gdalinfo --version
node --version
npm --version
git --version
```

## 2. Clone

```bash
git clone https://github.com/PlenoraETL/plenora.git ~/plenora
cd ~/plenora
git rev-parse HEAD
```

Record the full commit hash in the validation report.

## 3. PostgreSQL

```bash
sudo -u postgres psql
CREATE USER plenora WITH PASSWORD 'plenora';
CREATE DATABASE plenora OWNER plenora;
GRANT ALL PRIVILEGES ON DATABASE plenora TO plenora;
\q
```

Probe:

```bash
psql "postgresql://plenora:plenora@localhost:5432/plenora" -c "SELECT 1"
```

## 4. Redis

```bash
sudo systemctl enable --now redis-server
redis-cli ping
```

Expected: `PONG`.

## 5. Backend Environment

```bash
cd ~/plenora/backend
python3.11 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

Create `backend/.env`:

```bash
cat > .env <<'EOF'
ENVIRONMENT=development
LOG_LEVEL=INFO
PLENORA_ENCRYPTION_KEY=<generate>
JWT_SECRET_KEY=<generate>
DATABASE_URL=postgresql://plenora:plenora@localhost:5432/plenora
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
STORAGE_POLICY=db
BACKUP_BUCKET=plenora-local-no-real-s3
INTERMEDIATE_PERSISTENCE_MODE=metadata_only
EOF
chmod 600 .env
```

Generate secrets:

```bash
./venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
./venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Replace `<generate>` values before starting the backend.

## 6. Start Backend, Worker, Beat

Use three terminals from `~/plenora/backend`.

Terminal 1:

```bash
source venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 5004
```

Terminal 2:

```bash
source venv/bin/activate
celery -A workers.celery_app worker --loglevel=info --pool=solo
```

Terminal 3:

```bash
source venv/bin/activate
celery -A workers.celery_app beat --loglevel=info
```

## 7. Backend Smoke

```bash
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
```

Celery round-trip:

```bash
cd ~/plenora/backend
source venv/bin/activate
python -c "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))"
```

Expected: `pong`.

## 8. Frontend

```bash
cd ~/plenora/frontend
npm install
npm run dev
```

Open `http://localhost:5173` and confirm the UI can reach the backend.

## 9. Stop

Stop foreground processes with `Ctrl-C`.

For local services:

```bash
sudo systemctl stop redis-server
```

Do not delete the database unless this is an intentional reset.

## 10. Report

Use [first-pc-validation-report-template.md](first-pc-validation-report-template.md).

Paste:

- commit hash;
- package versions;
- `/health` output;
- `/ready` output;
- Celery `cleanroom.ping` output;
- frontend dev URL and any browser/backend errors;
- whether the smoke pipeline completed.

## Stop Conditions

Stop and report output instead of retrying blindly when:

- host package install fails;
- PostgreSQL probe fails;
- Redis probe fails;
- backend startup fails before `/health`;
- `/ready` returns 503;
- `cleanroom.ping` times out;
- frontend cannot reach the backend.
