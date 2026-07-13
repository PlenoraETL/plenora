# Clean Install, Non-Docker

This is the canonical single-machine install path for Plenora.

Target: fresh Ubuntu 24.04 host, no containers, local PostgreSQL and RabbitMQ
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
- Dramatiq worker receives and completes `cleanroom.ping`;
- APScheduler beat starts and emits heartbeat/readiness signal;
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
  rabbitmq-server \
  libgdal-dev libmagic1 \
  curl ca-certificates git \
  nodejs npm
```

Verify:

```bash
python3.11 --version
psql --version
rabbitmqctl version
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
\c plenora
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS plenora AUTHORIZATION plenora;
GRANT USAGE, CREATE ON SCHEMA plenora TO plenora;
ALTER ROLE plenora SET search_path TO plenora, public;
\q
```

Probe:

```bash
psql "postgresql://plenora:plenora@localhost:5432/plenora" -c "SELECT 1"
```

The application pool also sets `search_path=<DB_SCHEMA>,public` on every
connection. The `public` schema is kept read-only for the application role:
do not grant `CREATE` on `public` to `plenora`.

## 4. RabbitMQ

```bash
sudo systemctl enable --now rabbitmq-server
sudo rabbitmq-diagnostics -q ping
```

Expected: `Ping succeeded`. The default vhost `/` and the `guest` user
(localhost-only) are sufficient for a single-machine install.

### Object storage S3-compatible

Scarica il binario SeaweedFS e avvialo in modalita' single-node. In produzione
usa la unit [`plenora-object-storage.service`](../../backend/deploy/systemd/plenora-object-storage.service);
per una prova manuale:

```bash
curl -fsSL https://github.com/seaweedfs/seaweedfs/releases/download/4.29/linux_amd64.tar.gz \
  | sudo tar -xz -C /usr/local/bin weed
mkdir -p ~/plenora-object-storage
AWS_ACCESS_KEY_ID=plenora-storage AWS_SECRET_ACCESS_KEY=<generate> \
  S3_BUCKET=plenora-runtime weed mini -dir="$HOME/plenora-object-storage" -ip=127.0.0.1
```

Il valore `AWS_SECRET_ACCESS_KEY` deve coincidere con `S3_SECRET_KEY` nel
file applicativo seguente.

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
DRAMATIQ_BROKER_URL=amqp://guest:guest@localhost:5672
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BACKEND=s3_compatible
OBJECT_STORAGE_BUCKET=plenora-runtime
PLENORA_ARTIFACT_BUCKET=plenora-runtime
S3_ENDPOINT_URL=http://127.0.0.1:8333
S3_ACCESS_KEY=plenora-storage
S3_SECRET_KEY=<generate>
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

## 6. Start Backend, Data Worker, Control Worker, Beat

Use four terminals from `~/plenora/backend`.

Terminal 1:

```bash
source venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 5004
```

Terminal 2:

```bash
source venv/bin/activate
dramatiq workers.dramatiq_app --queues data-plane --processes 1 --threads 1
```

Terminal 3:

```bash
source venv/bin/activate
dramatiq workers.dramatiq_app --queues control-plane scheduler default --processes 1 --threads 2
```

Terminal 4:

```bash
source venv/bin/activate
python -m workers.beat
```

> Plenora 2.0 (Fase 3): il beat gira su APScheduler (`python -m workers.beat`),
> non piu' su `celery beat`. Leader-lock e schedule su PostgreSQL.

## 7. Backend Smoke

```bash
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
```

Task round-trip (Dramatiq dispatch + PostgreSQL result-store poll):

```bash
cd ~/plenora/backend
source venv/bin/activate
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
print(state)
PY
```

Expected: `SUCCESS` (the control worker from Terminal 3 must be running).

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
sudo systemctl stop rabbitmq-server
```

Do not delete the database unless this is an intentional reset.

## 10. Validation Report

Paste:

- commit hash;
- package versions;
- `/health` output;
- `/ready` output;
- `cleanroom.ping` round-trip state (`SUCCESS`);
- frontend dev URL and any browser/backend errors;
- whether the smoke pipeline completed.

## Stop Conditions

Stop and report output instead of retrying blindly when:

- host package install fails;
- PostgreSQL probe fails;
- RabbitMQ probe fails;
- backend startup fails before `/health`;
- `/ready` returns 503;
- `cleanroom.ping` times out;
- frontend cannot reach the backend.
