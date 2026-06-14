# Multi-Server Docker Install

Canonical procedure for running Plenora across multiple Docker hosts or
separated Docker roles.

This is not the root `docker-compose.yml` path. The compose file is scoped to a
single Docker network on one host. Multi-server installs must use shared
network dependencies and per-role containers.

## Target Topology

| Role | Required | Scaling Rule |
|---|---:|---|
| PostgreSQL | Yes | One primary database endpoint. Use managed Postgres or an HA Postgres setup outside this procedure. Also holds the task result/state store, the beat leader-lock, the JWT blacklist, and the rate-limit backend. |
| RabbitMQ | Yes | One shared AMQP broker endpoint for the Dramatiq task queue. |
| Object storage | Yes | Required for multi-host runtime blobs/artifacts. Use S3 or S3-compatible MinIO. |
| Backend API | Yes | One or more replicas behind a load balancer/reverse proxy. |
| Dramatiq worker | Yes | One or more replicas. Scale by workload. |
| Beat (APScheduler) | Yes | Run one active beat. Extra standby beat containers are only acceptable after validating the PostgreSQL leader-lock behavior. |
| Frontend nginx | Yes | One or more static frontend containers, or serve the built assets from the edge tier. |
| Reverse proxy/TLS | Production | Terminates TLS and routes browser traffic to frontend and backend API. |

## Hard Rules

- All backend, worker, and beat containers must run the same image tag.
- All backend, worker, and beat containers must share the same
  `PLENORA_ENCRYPTION_KEY` and `JWT_SECRET_KEY`.
- `DATABASE_URL` and `DRAMATIQ_BROKER_URL` must point to shared network
  services, not compose-local names like `postgres` or `rabbitmq`.
- Do not rely on local `DATA_DIR` for cross-host data sharing.
- Use `STORAGE_POLICY=object_storage` for multi-host installs unless a specific
  fallback design has been validated.
- Build the frontend with a browser-reachable API URL, not an internal Docker
  hostname.
- Start exactly one backend first and wait for `/ready` before scaling backend
  replicas or starting workers.

## 1. Host Prerequisites

On every Docker host:

```bash
docker --version
docker compose version
docker network ls
```

For Linux hosts, the single-server installer can be used as a host bootstrap
and preflight helper:

```bash
bash scripts/install-docker.sh --install-deps --check
```

For Windows Docker Desktop hosts:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-docker.ps1 -InstallDeps -Check
```

Do not run the full single-server installer on every multi-server host: it
starts the compose-local Postgres and RabbitMQ services. In a multi-server
topology, shared PostgreSQL, RabbitMQ, and object storage are provisioned once
and then referenced by backend, worker, beat, and frontend hosts through
explicit network URLs.

On the operator workstation:

```bash
git clone https://github.com/PlenoraETL/plenora.git ~/plenora
cd ~/plenora
git rev-parse HEAD
```

Record the commit hash.

## 2. Shared PostgreSQL

Create the database and schema on the shared Postgres endpoint:

```sql
CREATE USER plenora WITH PASSWORD '<strong-password>';
CREATE DATABASE plenora OWNER plenora;
\c plenora
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS plenora AUTHORIZATION plenora;
GRANT USAGE, CREATE ON SCHEMA plenora TO plenora;
ALTER ROLE plenora SET search_path TO plenora, public;
```

Probe from every app/worker host:

```bash
psql "postgresql://plenora:<password>@<postgres-host>:5432/plenora" -c "SELECT 1"
```

## 3. Shared RabbitMQ

RabbitMQ must be reachable from backend, worker, and beat hosts.

Minimum probe (from a host with the RabbitMQ CLI tools, or via the broker
container):

```bash
rabbitmq-diagnostics -q ping
```

Expected: `Ping succeeded`.

Use one canonical AMQP broker URL for the Dramatiq task queue:

```text
DRAMATIQ_BROKER_URL=amqp://guest:guest@<rabbitmq-host>:5672
```

Task state and results are not stored on the broker: they live in PostgreSQL
(`task_results`). There is no separate result-backend URL anymore.

## 4. Shared Object Storage

Use S3 or S3-compatible MinIO. Runtime object storage must be available before
starting the backend when `STORAGE_POLICY=object_storage`.

Canonical runtime env:

```bash
STORAGE_POLICY=object_storage
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BACKEND=s3
OBJECT_STORAGE_BUCKET=<plenora-runtime-bucket>
PLENORA_ARTIFACT_BUCKET=<plenora-runtime-bucket>
INTERMEDIATE_PERSISTENCE_MODE=metadata_only
```

For MinIO:

```bash
OBJECT_STORAGE_BACKEND=minio
MINIO_ENDPOINT_URL=https://<minio-host-or-dns>
MINIO_ACCESS_KEY=<access-key>
MINIO_SECRET_KEY=<secret-key>
OBJECT_STORAGE_BUCKET=<plenora-runtime-bucket>
```

Keep backup storage separate from runtime storage:

```bash
BACKUP_BUCKET=<plenora-backup-bucket>
BACKUP_BUCKET=<plenora-backup-bucket>
BACKUP_S3_REGION=<region>
```

## 5. Database Provider Libraries

The backend Python providers are installed from `backend/requirements.txt`:

| Provider | Python package | Native/system dependency | Multi-server action |
|---|---|---|---|
| PostgreSQL | `psycopg[binary]` | `libpq`, `postgresql-client-17` | Included in the standard backend image. |
| MySQL/MariaDB | `pymysql` | None for the pure-Python driver | Included in the standard backend image. |
| Oracle | `oracledb` | None in thin mode | Included in the standard backend image. |
| SQL Server | `pyodbc` | Microsoft ODBC Driver 18 or 17 plus unixODBC | Install native ODBC driver in every backend and worker image that must connect to SQL Server. |

After a backend image is running, verify providers from each backend/worker
image family:

```bash
docker exec -it plenora-backend python -c "import psycopg, pymysql, oracledb, pyodbc; print('python db providers ok'); print('odbc_drivers=', pyodbc.drivers())"
```

For SQL Server, `odbc_drivers` must include `ODBC Driver 18 for SQL Server`
or `ODBC Driver 17 for SQL Server`. If it is empty, SQL Server connections
will fail even though `pyodbc` imports correctly.

Use this Debian 12/bookworm package layer for SQL Server-capable images:

```Dockerfile
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
```

Oracle note: `oracledb` defaults to thin mode. Oracle Instant Client is not
required unless the deployment explicitly needs thick-mode-only behavior.

## 6. Build Images

Use one immutable tag for the release:

```bash
export PLENORA_TAG=$(git rev-parse --short HEAD)
export REGISTRY=<registry.example.com/plenora>
export PUBLIC_API_URL=https://api.example.com

docker build -f backend/Dockerfile -t $REGISTRY/backend:$PLENORA_TAG .
docker build \
  --build-arg VITE_API_URL=$PUBLIC_API_URL \
  -f frontend/Dockerfile \
  -t $REGISTRY/frontend:$PLENORA_TAG .

docker push $REGISTRY/backend:$PLENORA_TAG
docker push $REGISTRY/frontend:$PLENORA_TAG
```

The `PUBLIC_API_URL` value is compiled into the frontend bundle. It must be
reachable from the operator's browser.

## 7. Backend Environment File

Create `/etc/plenora/backend.env` on every backend, worker, and beat host:

```bash
ENVIRONMENT=production
LOG_LEVEL=INFO
DB_SCHEMA=plenora
DATABASE_URL=postgresql://plenora:<password>@<postgres-host>:5432/plenora
DRAMATIQ_BROKER_URL=amqp://guest:guest@<rabbitmq-host>:5672
PLENORA_ENCRYPTION_KEY=<same-fernet-key-on-all-app-containers>
JWT_SECRET_KEY=<same-jwt-key-on-all-app-containers>
CORS_ORIGINS=https://app.example.com,https://api.example.com
TRUSTED_PROXIES=<reverse-proxy-ip-or-cidr>
STORAGE_POLICY=object_storage
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BACKEND=s3
OBJECT_STORAGE_BUCKET=<plenora-runtime-bucket>
PLENORA_ARTIFACT_BUCKET=<plenora-runtime-bucket>
BACKUP_BUCKET=<plenora-backup-bucket>
BACKUP_BUCKET=<plenora-backup-bucket>
BACKUP_S3_REGION=<region>
INTERMEDIATE_PERSISTENCE_MODE=metadata_only
```

Protect it:

```bash
sudo chown root:root /etc/plenora/backend.env
sudo chmod 600 /etc/plenora/backend.env
```

## 8. Start One Backend

On the first backend host:

```bash
docker pull $REGISTRY/backend:$PLENORA_TAG
docker network create plenora-app || true
docker run -d \
  --name plenora-backend \
  --restart unless-stopped \
  --env-file /etc/plenora/backend.env \
  --network plenora-app \
  -p 5004:5004 \
  -v plenora-data:/data \
  $REGISTRY/backend:$PLENORA_TAG
```

Validate before starting any other app container:

```bash
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
```

Then verify migrations:

```bash
psql "$DATABASE_URL" -tAc "SELECT to_regclass('plenora.schema_migrations') IS NOT NULL;"
psql "$DATABASE_URL" -tAc "SELECT count(*) FROM plenora.schema_migrations;"
```

## 9. Start Additional Backend Replicas

On each additional backend host:

```bash
docker pull $REGISTRY/backend:$PLENORA_TAG
docker network create plenora-app || true
docker run -d \
  --name plenora-backend \
  --restart unless-stopped \
  --env-file /etc/plenora/backend.env \
  --network plenora-app \
  -p 5004:5004 \
  -v plenora-data:/data \
  $REGISTRY/backend:$PLENORA_TAG
curl -fsS http://127.0.0.1:5004/ready
```

Add these backends to the reverse proxy or load balancer only after `/ready`
is green.

## 10. Start Workers

On each worker host:

```bash
docker pull $REGISTRY/backend:$PLENORA_TAG
docker network create plenora-app || true
docker run -d \
  --name plenora-worker \
  --restart unless-stopped \
  --env-file /etc/plenora/backend.env \
  --network plenora-app \
  -v plenora-data:/data \
  --entrypoint python \
  $REGISTRY/backend:$PLENORA_TAG \
  -m dramatiq workers.dramatiq_app --processes 2 --threads 4
```

> Plenora 2.0 (Fase 1): il worker esegue gli actor Dramatiq da RabbitMQ
> (`python -m dramatiq workers.dramatiq_app`), non piu' `celery worker`.

Worker round-trip:

```bash
docker exec -it plenora-worker python -c "
import time
from app.core.task_runtime.dispatch import dispatch_task_by_name
from app.core.orchestrator.task_result_store import default_task_result_store
handle = dispatch_task_by_name('cleanroom.ping')
store = default_task_result_store()
deadline = time.monotonic() + 30
state = 'PENDING'
while time.monotonic() < deadline:
    record = store.get(handle.id)
    if record is not None and record.state in ('SUCCESS', 'FAILURE'):
        state = record.state
        break
    time.sleep(0.5)
print(state)
"
```

Expected: `cleanroom.ping` reaches `SUCCESS` in PostgreSQL (`task_results`). The
Dramatiq worker pulls the task from RabbitMQ and the result-store records the
terminal state.

## 11. Start Beat

On one scheduler host:

```bash
docker pull $REGISTRY/backend:$PLENORA_TAG
docker network create plenora-app || true
docker run -d \
  --name plenora-beat \
  --restart unless-stopped \
  --env-file /etc/plenora/backend.env \
  --network plenora-app \
  -v plenora-data:/data \
  --entrypoint python \
  $REGISTRY/backend:$PLENORA_TAG \
  -m workers.beat
```

> Plenora 2.0 (Fase 3): il beat gira su APScheduler (`python -m workers.beat`),
> non piu' su `celery beat`. Leader-lock e schedule su PostgreSQL.

Do not scale beat until standby behavior has been validated against the
PostgreSQL leader-lock (`beat_leader_locks`).

## 12. Start Frontend

On the frontend/edge host:

```bash
docker pull $REGISTRY/frontend:$PLENORA_TAG
docker run -d \
  --name plenora-frontend \
  --restart unless-stopped \
  -p 3000:80 \
  $REGISTRY/frontend:$PLENORA_TAG
```

Open:

```text
https://app.example.com
```

Confirm the browser calls `https://api.example.com`, not an internal host.

## 13. Reverse Proxy

Minimum routing:

| Public URL | Upstream |
|---|---|
| `https://app.example.com/` | frontend nginx container(s), port 80 |
| `https://api.example.com/` | backend container(s), port 5004 |

Backend env must match the proxy:

```bash
CORS_ORIGINS=https://app.example.com,https://api.example.com
TRUSTED_PROXIES=<proxy-ip-or-cidr>
```

## 14. Validation

From an operator machine:

```bash
curl -fsS https://api.example.com/health
curl -fsS https://api.example.com/ready
curl -fsS https://app.example.com/
```

From an app/worker host:

```bash
psql "$DATABASE_URL" -c "SELECT 1"
rabbitmq-diagnostics -q ping
docker logs --tail 200 plenora-backend
docker logs --tail 200 plenora-worker
docker logs --tail 200 plenora-beat
```

In the UI:

- login;
- create/list/delete a project;
- run one smoke pipeline;
- verify logs and result download;
- verify no browser CORS/network errors.

## 15. Rolling Update

1. Build and push a new immutable image tag.
2. Start one new backend with the new tag.
3. Wait for `/ready`.
4. Move load balancer traffic to the new backend.
5. Replace workers.
6. Replace beat last.
7. Replace frontend after confirming the API URL is correct.

Rollback is the same sequence using the previous image tag. Do not rotate
`PLENORA_ENCRYPTION_KEY` or `JWT_SECRET_KEY` during rollback.

## Stop Conditions

Stop and report output when:

- any host cannot reach Postgres or RabbitMQ;
- object storage readiness fails;
- first backend `/ready` fails;
- migration table is missing or empty after backend boot;
- worker ping times out;
- more than one beat appears active;
- frontend bundle calls the wrong API origin;
- CORS blocks browser requests;
- runtime blobs are written to local-only storage in a multi-host path.
