# Clean Install, Docker

This is the canonical Docker validation path for Plenora.

## Status

Backend Docker scaffold exists. Full product Docker readiness is not closed
until frontend delivery is included and validated on a real Linux host.

Current compose scope:

- PostgreSQL;
- Redis;
- backend API;
- Celery worker;
- Celery beat.

Known gap:

- frontend Docker/static delivery is not complete yet.

## Files

| File | Purpose |
|---|---|
| `backend/Dockerfile` | Backend image used by API, worker, and beat. |
| `docker-compose.yml` | Local compose stack. |
| `.env.docker.example` | Template for `.env.docker`. |
| `.dockerignore` | Excludes local state and secrets from build context. |
| `scripts/docker-validate.sh` | Non-destructive validation runner. |

## Acceptance Gate

Docker validation is green only when:

- `docker compose config` succeeds;
- backend image builds;
- Postgres and Redis become healthy;
- backend `/health` and `/ready` are green;
- worker completes `cleanroom.ping`;
- beat starts and stays alive;
- logs show no startup error;
- frontend delivery is validated separately or added to compose.

## Validation pack for the Linux PC test

Stream: `BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK`

Status: **FIRST SCAFFOLD, NOT YET VALIDATED**.

Preferred runner:

```bash
bash scripts/docker-validate.sh --dry-run
bash scripts/docker-validate.sh
```

The script is non-destructive. It does not run `docker compose down`,
`docker compose down -v`, remove volumes, or overwrite `.env.docker`.

Paste back these host fingerprints with the validation output:

```bash
uname -a
cat /etc/os-release
docker --version
docker compose version
```

Expected output checklist:

1. **`docker compose config`**
   Expected: compose resolves without missing env/file errors.
2. **`docker compose build backend`**
   Expected: backend image builds successfully.
3. **`docker compose up -d --wait backend`**
   Expected: Postgres, Redis, and backend become healthy enough for API probes.
4. **`curl -fsS http://127.0.0.1:5004/health`**
   Expected: HTTP 200 from the backend liveness surface.
   Then run **`curl -fsS http://127.0.0.1:5004/ready`**.
   Expected: HTTP 200 from the readiness surface; dependency gates and
   migration readiness are green.
5. **`docker compose up -d worker beat`**
   Expected: worker and beat containers stay running.
6. **`cleanroom.ping`**
   Expected: Celery worker returns `pong`.
7. **memory caps visibility**
   Expected: runtime memory caps print from backend settings.
8. **`docker compose stop`**
   Expected: stack stops without deleting volumes or `.env.docker`.

## 1. Prerequisites

```bash
docker --version
docker compose version
```

## 2. Environment

```bash
cp .env.docker.example .env.docker
python3 -c "from cryptography.fernet import Fernet; print('PLENORA_ENCRYPTION_KEY=' + Fernet.generate_key().decode())" >> .env.docker
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env.docker
chmod 600 .env.docker
```

Review `.env.docker` before continuing. Do not commit it.

## 3. Validate Compose

```bash
docker compose config
```

## 4. Build And Start

```bash
docker compose build backend
docker compose up -d postgres redis
docker compose up -d --wait backend
docker compose up -d worker beat
```

## 5. Health

```bash
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
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

Use the `/ready` response body first: it names the failing readiness check.
Common blockers are Postgres/Redis connectivity, pending or failed migrations,
object storage policy/config, and incomplete backend bootstrap.

## 6. Worker Round-Trip

```bash
docker compose exec worker python -c "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))"
```

Expected: `pong`.

## 7. Memory Caps

```bash
docker compose exec backend python -c "
from app.config import settings
for k in ('LOAD_FILE_MAX_BYTES','WORKER_RSS_MAX_BYTES','RESOURCE_MAX_BYTES','ARTIFACT_PREVIEW_MAX_BYTES','CROSS_JOIN_MAX_OUTPUT_ROWS','MULTI_JOIN_MAX_OUTPUT_ROWS'):
    print(f'{k}={settings.get(k)}')
"
```

## 8. Stop

Non-destructive stop:

```bash
docker compose stop
```

Destructive reset for a disposable validation host only:

```bash
docker compose down -v
rm -f .env.docker
```

## 9. Validation Script

Preferred runner:

```bash
bash scripts/docker-validate.sh --dry-run
bash scripts/docker-validate.sh
```

The script must remain non-destructive. It must not remove volumes or
overwrite `.env.docker`.

## 10. Frontend Gap

Until frontend delivery is added, Docker validation proves backend runtime
health only. A 1.0 Docker claim requires one of:

- frontend service in compose;
- documented static frontend build served by the backend/reverse proxy;
- separate but validated frontend container tied to the same backend.
