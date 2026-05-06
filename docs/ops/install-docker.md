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

## Validation pack for the Linux PC test (FIRST SCAFFOLD, NOT YET VALIDATED)

`BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK`.

> **Status: FIRST SCAFFOLD, NOT YET VALIDATED.** This pack
> packages the Step 1-7 commands above into a single contiguous
> copy/paste sequence with the **exact** expected output for
> each command. Until Marco runs it on his Linux PC and pastes
> the outputs back, every line below is a hypothesis. The
> wrapper script `scripts/docker-validate.sh` (also dry-run
> friendly via `--dry-run`) is the convenience runner; the raw
> commands below are the contract.

### What Marco runs

From the repo root, after Step 2 above (`.env.docker` exists
with both secrets set):

```bash
# 0. Convenience wrapper (recommended). Use --dry-run first
#    to print the exact command sequence without executing it.
bash scripts/docker-validate.sh --dry-run
bash scripts/docker-validate.sh
```

The wrapper is **non-destructive**: it never runs
`docker compose down`, `down -v`, `rm -rf volumes/`, or
overwrites `.env.docker`. It only runs the eight commands
below in order, and it preserves volumes (`postgres-data`,
`redis-data`, `data-dir`) on stop.

If Marco prefers raw commands (skip the wrapper), the eight
items map to the canonical 1:1 sequence:

```bash
# 1. config (no apply)
docker compose config

# 2. build backend image (also produces worker/beat image
#    since they reuse it)
docker compose build backend

# 3. up postgres + redis + backend (gates on healthchecks)
docker compose up -d postgres redis
docker compose up -d --wait backend

# 4. /health and /ready from the host
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready

# 5. up worker + beat
docker compose up -d worker beat

# 6. cleanroom.ping round-trip
docker compose exec worker python -c \
    "from workers.celery_app import celery_app; \
     print(celery_app.send_task('cleanroom.ping').get(timeout=30))"

# 7. memory caps visibility
docker compose exec backend python -c "
from app.config import settings
for k in ('LOAD_FILE_MAX_BYTES','WORKER_RSS_MAX_BYTES','RESOURCE_MAX_BYTES','ARTIFACT_PREVIEW_MAX_BYTES','CROSS_JOIN_MAX_OUTPUT_ROWS','MULTI_JOIN_MAX_OUTPUT_ROWS'):
    print(f'{k}={settings.get(k)}')
"

# 8. non-destructive stop (preserves volumes)
docker compose stop
```

### Expected output (paste back exactly)

For each numbered item, Marco should paste back the **exact
stdout** he sees. The shapes below are the assertion targets.

1. **`docker compose config`** -- exit 0. Stdout: a fully
   resolved YAML dump of the same five services
   (`postgres`, `redis`, `backend`, `worker`, `beat`) with
   `${VAR}` references substituted from `.env.docker`.
   Failure paste: any `services.<svc>.environment.* required
   variable ... is not set` line.

2. **`docker compose build backend`** -- exit 0. Final
   stdout line ends with
   `=> => naming to docker.io/library/plenora-backend:scaffold`
   (or local equivalent). Failure paste: the last 50 lines
   of `apt-get install` output (the most likely failure is
   `libgdal-dev` major-version mismatch on the bookworm
   base).

3. **`docker compose up -d --wait backend`** -- exit 0.
   Final line: `Container plenora-backend-1  Healthy`. The
   command does NOT exit until the Dockerfile's
   `HEALTHCHECK` reports healthy (probe = `/health` inside
   the container). Failure paste:
   `docker compose ps` (look for `(unhealthy)` on backend)
   AND `docker compose logs --tail 200 backend`.

4. **`curl -fsS http://127.0.0.1:5004/health`** + same on
   `/ready`. Both return HTTP 200 with a JSON envelope
   `{"status":"ok","checks":{...}}` for `/health` and
   `{"status":"ready","checks":{...}}` for `/ready`. The
   `/ready` payload exposes 8 keys: `postgres`, `redis`,
   `pool_headroom`, `migrations`, `object_storage`,
   `reconciliation_backlog`, `beat_heartbeat`,
   `bootstrap_complete`. Failure paste:
   `curl -is http://127.0.0.1:5004/ready` (with `-i` to
   see status line) AND `docker compose logs --tail 200
   backend`.

5. **`docker compose up -d worker beat`** -- exit 0. Both
   containers report `Started`. Verify with
   `docker compose ps` that `plenora-worker-1` and
   `plenora-beat-1` are `running`. Failure paste:
   `docker compose logs --tail 200 worker beat`.

6. **`cleanroom.ping`** -- exit 0. Stdout: literal `pong`.
   Failure paste: the full traceback (most common failure:
   `kombu.exceptions.OperationalError: Error 111 connecting
   to redis:6379. Connection refused.` -- means worker is
   up but Redis is not reachable from inside the worker
   container, which would also block step 5).

7. **memory caps visibility** -- exit 0. Stdout: six lines
   of the form `<KEY>=<VALUE>`. Default scaffold values
   (no override in `.env.docker`):

   ```
   LOAD_FILE_MAX_BYTES=209715200
   WORKER_RSS_MAX_BYTES=1500000000
   RESOURCE_MAX_BYTES=209715200
   ARTIFACT_PREVIEW_MAX_BYTES=26214400
   CROSS_JOIN_MAX_OUTPUT_ROWS=50000000
   MULTI_JOIN_MAX_OUTPUT_ROWS=50000000
   ```

   Override values (if `.env.docker` sets any of these)
   are also acceptable -- the shape is what matters.
   Failure paste: the full traceback (most common: the
   key is missing from `Settings.DEFAULTS`, returning
   `None`).

8. **`docker compose stop`** -- exit 0. Final stdout block
   shows each container `Stopped`. After the command,
   `docker compose ps -a` lists the five containers as
   `Exited (0)`, and
   `docker volume ls --filter name=plenora` lists
   `postgres-data`, `redis-data`, `data-dir` (volumes
   preserved). Failure paste:
   `docker compose ps -a` AND `docker volume ls`.

### What the script does NOT do

The wrapper `scripts/docker-validate.sh` deliberately
**never** runs:

- `docker compose down` (removes containers; the scaffold
  uses `stop` for non-destructive teardown);
- `docker compose down -v` (removes volumes -- DESTRUCTIVE);
- `rm` against `.env.docker` (never overwrite or delete
  generated secrets);
- `docker volume rm` of any `plenora-*` volume;
- any `docker system prune` variant.

If a `--dry-run` flag is passed, the script prints each
command exactly as it would run, with no execution, and
exits 0. This lets Marco preview the sequence before
spending the disk + time on the build step.

### Reporting back

When Marco runs the pack, the artifacts to paste back are:

1. The full stdout of `bash scripts/docker-validate.sh`
   (or, if running raw commands, stdout of each step in
   sequence).
2. For any failed step, the corresponding "Failure paste"
   command output listed above.
3. The host metadata: `uname -a`, `docker --version`,
   `docker compose version`,
   `cat /etc/os-release | grep PRETTY_NAME`.

The validation gate closes only after the round-trip
succeeds end-to-end on a real Linux host. Until then this
section's status header (`FIRST SCAFFOLD, NOT YET
VALIDATED`) MUST stay accurate.

---

## Known scaffold limitations (validation gaps)

These are the explicit gaps the scaffold does NOT close. Each
is the closure path for a follow-up stream.

### What ships today vs what does NOT

`BE-GO-READY-INSTALL-018-DOCKER-FE-GAP-AUDIT` -- audit-only
truth table. The scaffold is BACKEND-ONLY and the full
"Plenora-ETL on Docker" experience does NOT exist yet.

| Component | Ships in this scaffold? | Validated on Linux PC? | Closure stream |
|---|---|---|---|
| `backend/Dockerfile` (FastAPI / uvicorn :5004) | YES | NO (FIRST SCAFFOLD) | `INSTALL-012-DOCKER-SCAFFOLD-VALIDATE-ON-LINUX` |
| `worker` service (Celery `--pool=solo`, reuses `plenora-backend:scaffold` image) | YES | NO (FIRST SCAFFOLD) | same |
| `beat` service (`PlenoraBeatScheduler`, reuses `plenora-backend:scaffold` image) | YES | NO (FIRST SCAFFOLD) | same |
| `postgres` service (postgres:17-alpine) | YES | NO (FIRST SCAFFOLD) | same |
| `redis` service (redis:7-alpine) | YES | NO (FIRST SCAFFOLD) | same |
| `frontend/Dockerfile` (SPA build + nginx static serve) | NO (no file at `frontend/Dockerfile*`) | NO | `INSTALL-013-FRONTEND-DOCKER` |
| `frontend` service in `docker-compose.yml` | NO (5 services: postgres / redis / backend / worker / beat) | NO | `INSTALL-013-FRONTEND-DOCKER` (compose row added with the Dockerfile) |
| Reverse proxy / TLS termination | NO | NO | out of scope (operator-side nginx/Caddy/Apache) |
| MinIO profile (`STORAGE_POLICY=object_storage`) | NO | NO | `INSTALL-014-MINIO-PROFILE` |
| Backup / backup-test units (systemd timers) | NO | NO | not on the Docker roadmap; systemd-only path |
| Docker-side CI lane (`docker compose up + cleanroom.ping`) | NO (only `ci.yml`, `ci-cleanroom.yml`, `ci-functional.yml`; none invoke `docker build` / `docker compose`) | NO | `INSTALL-015-DOCKER-CI` |

Therefore the "full-stack Docker" answer is: **does NOT exist
today**. An operator running `docker compose up` against this
repo gets a backend-only API with PG + Redis. Serving the SPA
requires building the frontend separately on the host (per
`install-non-docker.md` section 8) and pointing a reverse
proxy at the published `:5004` for `/api/*` and at the
host-side `frontend/dist/` for the SPA assets.

### Item-level closure pointers

- **No Linux-PC validation report**: every command in this
  doc is a hypothesis until run on a real Ubuntu/Debian host.
  Closure: `INSTALL-012-DOCKER-SCAFFOLD-VALIDATE-ON-LINUX`.
- **No frontend image** (NO `frontend/Dockerfile*` file
  exists in the repo as of this commit): the scaffold ships
  only the backend side. An operator who wants the full SPA
  must build the frontend separately. Closure:
  `INSTALL-013-FRONTEND-DOCKER`.
- **No MinIO profile**: `STORAGE_POLICY=db` is the only path
  the scaffold exercises; switching to `object_storage`
  requires either MinIO + bucket setup or AWS credentials.
  Closure: `INSTALL-014-MINIO-PROFILE`.
- **No Docker CI lane**: `ci-cleanroom.yml` continues to
  validate only the single-machine boot path. A Docker-side
  CI lane (`docker compose up + cleanroom.ping`) is bounded
  but non-trivial. Closure: `INSTALL-015-DOCKER-CI`.
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

### When can the Docker path be called "validated"?

A path is "validated" only when ALL of the following hold:

1. A green
   [`first-pc-validation-report-template.md`](first-pc-validation-report-template.md)
   section 3.10 (9-line acceptance summary) lands on a real
   Linux PC at a known commit SHA -- closes the
   backend/worker/beat side.
2. `frontend/Dockerfile` exists, builds reproducibly, AND a
   `frontend` service is added to `docker-compose.yml` AND
   the SPA is reachable through a reverse-proxy service in
   compose -- closes `INSTALL-013-FRONTEND-DOCKER`.
3. `.github/workflows/` has a CI job that runs `docker
   compose up` and asserts `cleanroom.ping -> pong` from
   inside the worker container -- closes
   `INSTALL-015-DOCKER-CI`.

Until all three land, the FIRST SCAFFOLD / NOT YET VALIDATED
markers in this doc and in
`install-marco-dry-run.md` MUST stay. A doc PR that flips the
markers without citing the three artifacts above is a
regression of the honesty contract.

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
