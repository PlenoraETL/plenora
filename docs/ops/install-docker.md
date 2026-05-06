# Clean install -- Docker (FIRST SCAFFOLD)

> **Status: FIRST SCAFFOLD.** As of this commit Plenora-ETL ships
> a `backend/Dockerfile`, a top-level `docker-compose.yml`, a
> `.dockerignore`, and `.env.docker.example`
> (`BE-GO-READY-INSTALL-011`). The scaffold is **executable**
> (`docker compose config`, `docker compose build`,
> `docker compose up -d`) but **NOT YET VALIDATED end-to-end on
> a real Linux PC**. There is no Helm chart, no Kubernetes
> manifest, and no frontend image yet.
>
> The single-machine path
> ([install-non-docker.md](install-non-docker.md)) remains the
> go-ready first-PC test path. Use the Docker scaffold to test
> the scaffold itself, not as the Linux PC validation gate.

---

## Why this scaffold ships now

`BE-GO-READY-INSTALL-011-FIRST-DOCKER-SCAFFOLD`.

Operators arriving from external docs need to be able to test
"does the Docker path even build?" without manually deriving
a Dockerfile from the single-machine runbook. INSTALL-011
crosses that gap with a minimal multi-stage Dockerfile reused
across backend / worker / beat services, plus a five-service
compose (postgres, redis, backend, worker, beat) that mirrors
the smoke harness in
[install-marco-dry-run.md](install-marco-dry-run.md) section 5.

Frontend, MinIO, the systemd backup unit, and AWS-side IAM
bits remain explicitly out of this scaffold; they are still
target-shape items (see "Target shape" below).

---

## What ships in this scaffold

| File | Purpose |
|---|---|
| `backend/Dockerfile` | Multi-stage: build with `libpq-dev`/`libgdal-dev`/`build-essential` + venv install; runtime with `libpq5`/`libgdal32`/`libmagic1` + non-root `plenora` user; `EXPOSE 5004`; `HEALTHCHECK` on `/health`. |
| `docker-compose.yml` | Five services (`postgres`, `redis`, `backend`, `worker`, `beat`). Healthchecks on postgres + redis; `depends_on: service_healthy` for backend. Worker uses `--pool=solo`. Beat uses `PlenoraBeatScheduler`. Named volumes: `postgres-data`, `redis-data`, `data-dir`. |
| `.dockerignore` | Excludes `.git`, `.env*`, venvs, pytest temps, node_modules, frontend `dist/`, agent state. |
| `.env.docker.example` | Template for `.env.docker` (gitignored). Shows the canonical `ENVIRONMENT` / `DB_SCHEMA` / `STORAGE_POLICY` / `BACKUP_BUCKET` plus six commented memory caps. Secrets must be generated before first up. |

The scaffold does NOT add a CI lane to validate it. The
existing `ci-cleanroom.yml` continues to validate the
single-machine boot path; a Docker-side CI lane is a separate
follow-up stream.

---

## Status today (command-first, executable scaffold)

`BE-GO-READY-INSTALL-010-LINUX-EXECUTION-PACK` (table contract;
INSTALL-011 supersedes the per-row status with executable
commands).

The eight harness items map to executable Docker commands now;
each row also lists the equivalent single-machine command for
operators who want to compare the two paths. The "Validated on
Linux PC" column is uniformly **NO (scaffold)** until a
real-host validation report ships.

| # | Harness item | Single-machine command (today) | Docker command (scaffold) | Validated on Linux PC |
|---|---|---|---|---|
| 1 | prereqs | `bash scripts/install.sh --check` | `docker --version && docker compose version && docker compose config` | NO (scaffold) |
| 2 | env decisions | `bash scripts/install.sh --non-interactive` with `PLENORA_INSTALL_*` env vars | `cp .env.docker.example .env.docker` then fill `PLENORA_ENCRYPTION_KEY` + `JWT_SECRET_KEY` (see `.env.docker.example` for one-liners) | NO (scaffold) |
| 3 | install (deps + DB init) | `pip install -r requirements.txt` then first uvicorn boot triggers `migrations_runner.ensure_schema(DB_SCHEMA)` | `docker compose build backend && docker compose up -d postgres redis backend` (the backend container's first boot triggers the same schema autocreate) | NO (scaffold) |
| 4 | smoke (foreground) | three terminals: uvicorn / celery worker `--pool=solo` / celery beat | `docker compose up -d worker beat` then `docker compose logs -f backend worker beat` (Ctrl-C to stop following; services keep running) | NO (scaffold) |
| 5 | health/ready | `curl -fsS http://127.0.0.1:5004/{health,ready}` | `docker compose ps` (look for `(healthy)` on backend) and `curl -fsS http://127.0.0.1:5004/health` + `curl -fsS http://127.0.0.1:5004/ready` (compose publishes 5004 on the host) | NO (scaffold) |
| 6 | Celery check (real worker round-trip) | `./venv/bin/python -c "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))"` | `docker compose exec worker python -c "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))"` (expect `pong`) | NO (scaffold) |
| 7 | memory caps visibility | `./venv/bin/python` heredoc using `app.config.settings.get(KEY)` for the six caps | `docker compose exec backend python -c "from app.config import settings; [print(f'{k}={settings.get(k)}') for k in ('LOAD_FILE_MAX_BYTES','WORKER_RSS_MAX_BYTES','RESOURCE_MAX_BYTES','ARTIFACT_PREVIEW_MAX_BYTES','CROSS_JOIN_MAX_OUTPUT_ROWS','MULTI_JOIN_MAX_OUTPUT_ROWS')]" | NO (scaffold) |
| 8 | non-destructive rollback | `pkill -INT -f 'uvicorn app.main:app'` (and same for celery) | `docker compose stop` (preserves volumes + named-volume state; `docker compose down` would remove containers; `docker compose down -v` would also remove volumes -- DESTRUCTIVE) | NO (scaffold) |

Validation status: every command above is syntactically
correct against the shipped scaffold but has not been
end-to-end validated on a real Linux PC yet. Treat any "this
should work" comment in the scaffold as a hypothesis until a
follow-up stream closes the validation gap.

---

## Operator runbook (scaffold)

Copy/paste sequence for testing the scaffold on a Linux PC
that already has Docker Engine + the compose plugin installed.
This sequence does NOT replace the single-machine first-PC
test in [install-marco-dry-run.md](install-marco-dry-run.md);
run it in addition to (not instead of) the go-ready path.

### Step 1 -- prereqs

```bash
docker --version
docker compose version
docker compose config   # validates docker-compose.yml syntax (no apply)
```

`docker compose config` exits 0 only when the compose file
parses cleanly and every `${VAR}` reference resolves. Run
from the repo root.

### Step 2 -- env decisions

```bash
cp .env.docker.example .env.docker
python3 -c "import secrets; print('PLENORA_ENCRYPTION_KEY=' + secrets.token_urlsafe(48))" >> .env.docker
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env.docker
chmod 600 .env.docker
```

`.env.docker` is in `.gitignore` and `.dockerignore`. Both
secrets must be set BEFORE step 3 -- the backend container
fail-fasts on missing `PLENORA_ENCRYPTION_KEY` /
`JWT_SECRET_KEY` in `ENVIRONMENT=production`. The
`.env.docker.example` defaults to `ENVIRONMENT=development`,
so an empty secret will fall back to a generated dev key with
a warning log; for any real test, set them explicitly.

### Step 3 -- build + bring up the stack

```bash
docker compose build backend
docker compose up -d postgres redis
docker compose up -d --wait backend
docker compose up -d worker beat
```

`--wait` on backend gates on the `HEALTHCHECK` in the
Dockerfile; the command exits 0 only after `/health` returns
HTTP 200 inside the container. The schema autocreates on first
boot via `migrations_runner.ensure_schema(DB_SCHEMA)` -- same
code path as the single-machine install.

### Step 4 -- /health and /ready (host-side curl)

```bash
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
```

Expected: HTTP 200 on both. `/ready` reports the same six
checks as the single-machine install (postgres, redis,
pool_headroom, migrations_aligned, object_storage,
reconciliation_backlog) plus the optional `beat_heartbeat`
and the fail-CLOSED `bootstrap_complete`.

If `/ready` returns 503, inspect:

```bash
docker compose logs --tail 200 backend
docker compose logs --tail 200 worker
docker compose logs --tail 200 beat
```

The four boot gates log loud (`Settings.validate_startup`,
`validate_storage_config`, `init_all_dbs`+migrations,
`_repair_psycopg2_ssl_env`) -- whichever first prints
`[ERROR]` is the one to fix.

### Step 5 -- cleanroom.ping round-trip (Celery check)

```bash
docker compose exec worker python -c \
    "from workers.celery_app import celery_app; \
     print(celery_app.send_task('cleanroom.ping').get(timeout=30))"
```

Expected output: `pong`. Same primitive as the single-machine
smoke (`docs/ops/install-marco-dry-run.md` section 5.3) and
the cleanroom CI lane.

### Step 6 -- memory caps visibility

```bash
docker compose exec backend python -c "
from app.config import settings
for k in ('LOAD_FILE_MAX_BYTES','WORKER_RSS_MAX_BYTES','RESOURCE_MAX_BYTES','ARTIFACT_PREVIEW_MAX_BYTES','CROSS_JOIN_MAX_OUTPUT_ROWS','MULTI_JOIN_MAX_OUTPUT_ROWS'):
    print(f'{k}={settings.get(k)}')
"
```

Expected: the six cap values the runtime will enforce inside
the container. Override via `.env.docker` if the host has
constrained memory.

### Step 7 -- non-destructive stop (preserves state)

```bash
docker compose stop
```

Stops all services without removing them. Volumes
(`postgres-data`, `redis-data`, `data-dir`) are preserved;
the next `docker compose start` resumes with the same DB
schema, Redis state, and `.env.docker` config.

For a destructive total reset (PC scaffold-test only):

```bash
docker compose down -v   # DESTRUCTIVE: removes containers AND volumes
rm -f .env.docker        # DESTRUCTIVE: removes generated secrets
```

---

## Known scaffold limitations (validation gaps)

These are the explicit gaps the scaffold does NOT close. Each
is the closure path for a follow-up stream:

- **No Linux-PC validation report**: every command above is a
  hypothesis until run on a real Ubuntu/Debian host. Closure:
  follow-up stream `BE-GO-READY-INSTALL-012-DOCKER-SCAFFOLD-VALIDATE-ON-LINUX`.
- **No frontend image**: the scaffold ships only the backend
  side. An operator who wants the full SPA must build the
  frontend separately. Closure: `INSTALL-013-FRONTEND-DOCKER`.
- **No MinIO profile**: `STORAGE_POLICY=db` is the only path
  the scaffold validates; switching to `object_storage`
  requires either MinIO + bucket setup or AWS credentials.
  Closure: `INSTALL-014-MINIO-PROFILE`.
- **No CI lane**: `ci-cleanroom.yml` continues to validate
  only the single-machine boot path. A Docker-side CI lane
  (`docker compose up + cleanroom.ping`) is bounded but
  non-trivial. Closure: `INSTALL-015-DOCKER-CI`.
- **No HTTPS / reverse proxy**: the scaffold publishes 5004
  directly on the host. Production needs nginx/Caddy/Apache
  in front; out of scope.
- **No `--wait` propagation to worker/beat**: `docker compose
  up -d --wait` only gates on backend's HEALTHCHECK; worker
  and beat have no healthcheck yet. Closure: optional
  `INSTALL-016-WORKER-BEAT-HEALTHCHECK`.
- **Native deps risk**: the build stage installs
  `libgdal-dev`. If a Debian point-release moves the major
  version, fiona/geopandas wheels will need a rebuild. The
  build will fail loudly (compile error) rather than silently;
  no validation gap here, just a known maintenance touchpoint.

If `docker compose build backend` fails on a real PC, the
most likely root cause is `libgdal-dev` major mismatch
between the bookworm base and the wheel ABI. Workaround:
pin `libgdal-dev` to a known version OR switch to a
`ubuntu:24.04`-based image. Either is a real stream, not a
patch in this scaffold.

---

## Target shape (specification only)

When the full Docker batch lands (`INSTALL-013..016`), it must
deliver these artifacts and satisfy these contracts. This
section is normative for that future work -- not a procedure
operators run today.

### A. `frontend/Dockerfile` (NOT YET SHIPPED)

Two-stage:

1. Node build: `npm ci && npm run build` produces `dist/`.
2. nginx static serve from `dist/`. Healthcheck:
   `wget -qO- http://127.0.0.1/ || exit 1`.

### B. `docker-compose.yml` extended services

Beyond the five services in this scaffold, the production-
shape compose adds:

- `frontend` (built from `frontend/Dockerfile`),
  `depends_on: backend`;
- optional `minio` service behind `--profile minio`, with
  named volume `minio-data`.

### C. `.env.docker.example` extended

The shipped `.env.docker.example` is the minimum. Production
needs:

```
# Frontend
PLENORA_FRONTEND_PORT=8080
# Object storage (when STORAGE_POLICY != db)
OBJECT_STORAGE_ENABLED=true
OBJECT_STORAGE_BUCKET=plenora-runtime-...
PLENORA_ARTIFACT_BUCKET=plenora-runtime-...
CLOUD_PROVIDER=minio   # or aws
MINIO_ENDPOINT_URL=http://minio:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
```

### D. Target install procedure (when batch lands)

```
git clone <repo>
cd plenora
cp .env.docker.example .env.docker
python3 -c "import secrets; print('PLENORA_ENCRYPTION_KEY=' + secrets.token_urlsafe(48))" >> .env.docker
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env.docker
docker compose --profile minio up -d --wait
# Visit http://localhost:8080/.
```

The today-procedure (Steps 1-7 above) is the proper subset.

---

## Out of scope for this scaffold AND for the follow-up batches

These items are explicitly NOT solved by Docker compose at any
stage and remain separate investments:

- IAM / SSM secrets workflow (the AWS production path is
  host-bound today; mapping it to compose secrets is a
  separate stream).
- Bucket lifecycle rules (`backend/deploy/terraform/`).
- Multi-replica / Kubernetes scale-out
  (`PlenoraBeatScheduler` is leader-elected via Redis SETNX
  so multiple beat replicas are safe, but a Kubernetes
  manifest layer is a separate batch).
- TLS termination and public-facing reverse-proxy (assumed
  external).

---

## Pointers

- Audit context: `.agent-temp/BE_INSTALLATION_AUDIT.md`
  (working-copy audit at the time of writing).
- Non-Docker install: [install-non-docker.md](install-non-docker.md).
- First-PC dry-run pack:
  [install-marco-dry-run.md](install-marco-dry-run.md).
- Cleanroom CI verifier (HTTP boot from empty Postgres + Redis):
  `.github/workflows/ci-cleanroom.yml` -- the closest existing
  approximation to a Docker-style "fresh stack" smoke (still
  not Docker-based).
