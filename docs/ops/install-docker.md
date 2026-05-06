# Clean install — Docker (NOT IMPLEMENTED)

> **Status: NOT IMPLEMENTED.** As of this commit there is no
> `Dockerfile`, no `docker-compose.yml`, and no Kubernetes manifest
> anywhere in the repo. An operator following this document cannot
> install Plenora-ETL today via Docker.
>
> This file documents the **target shape** for a future Docker batch
> (`BE-GO-READY-INSTALLATION-010..013`, see
> `BE_INSTALLATION_AUDIT.md` §7.B). Until that batch ships, use
> [install-non-docker.md](install-non-docker.md) for any real
> install.

---

## Why this stub exists

Operators arriving from external docs, internal hand-off notes, or
search results need an unambiguous answer to *"can I `docker compose
up` this thing?"*. The answer is no, and the reasons + the path to
yes are recorded here so the question does not need to be re-asked.

The non-Docker install path is fully supported and documented at
[install-non-docker.md](install-non-docker.md). The production
deployment at `lab.plenora.it` runs that path on a single EC2 host
under systemd.

For a first-time clean install on a fresh Linux PC (with or
without Docker pre-installed), the executable copy/paste pack
is [install-marco-dry-run.md](install-marco-dry-run.md). That
page assumes `STORAGE_POLICY=db`, local Postgres, local Redis,
no Docker, no MinIO, no AWS - the smallest viable smoke profile.

---

## What is missing

Verified at base commit (file-system glob):

- `**/Dockerfile*` — zero matches;
- `**/docker-compose*` — zero matches;
- `**/*.helm` / `**/Chart.yaml` / `**/kustomization.yaml` — none.

Audit reference: `BE_INSTALLATION_AUDIT.md` §6.1 (P0 blocker), §7.1.

---

## Status today (command-first, executable)

`BE-GO-READY-INSTALL-010-LINUX-EXECUTION-PACK`.

If your PC has Docker installed, the table below maps every
harness item to the **command an operator can run today** and
the explicit **NOT IMPLEMENTED** marker for the Docker side.
The "Today's command" column is the same single-machine path
as [install-marco-dry-run.md](install-marco-dry-run.md) sec. 2 +
sec. 5; **Docker is not an alternative path - there is no
Dockerfile to bake an image from.**

| # | Harness item | Today's command (single-machine) | Docker status (today) |
|---|---|---|---|
| 1 | prereqs | `bash scripts/install.sh --check` (after `apt install` per dry-run sec. 2.1) | NOT IMPLEMENTED - no Dockerfile, no compose; `docker compose up` will fail with `no configuration file provided` |
| 2 | env decisions | `bash scripts/install.sh --non-interactive` with `PLENORA_INSTALL_*` env vars (dry-run sec. 2.5) | NOT IMPLEMENTED - target `.env.docker.example` is documented below in section "Target shape D" |
| 3 | install (deps + DB init) | `./venv/bin/pip install -r requirements.txt` then first uvicorn boot triggers `migrations_runner.ensure_schema(DB_SCHEMA)` (dry-run sec. 2.6 + sec. 2.7) | NOT IMPLEMENTED - target `backend/Dockerfile` build stage is documented below in section "Target shape A" |
| 4 | smoke (foreground) | three terminals: uvicorn / celery worker `--pool=solo` / celery beat (dry-run sec. 2.7) | NOT IMPLEMENTED - target compose `services: backend / worker / beat` documented below in section "Target shape C" |
| 5 | health/ready | `curl -fsS http://127.0.0.1:5004/{health,ready}` (dry-run sec. 2.8) | NOT IMPLEMENTED - target `HEALTHCHECK` in Dockerfile + compose `--wait` documented in target shape A + C |
| 6 | Celery check (real worker round-trip) | `./venv/bin/python -c "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))"` -> `pong` (dry-run sec. 5.3) | NOT IMPLEMENTED - target `docker compose exec worker python -c "..."` requires the worker container which does not exist |
| 7 | memory caps visibility | `./venv/bin/python` heredoc using `app.config.settings.get(KEY)` for the six caps (dry-run sec. 5.2) | NOT IMPLEMENTED on Docker, but the override SHAPE is fixed: three commented `LOAD_FILE_MAX_BYTES` / `WORKER_RSS_MAX_BYTES` / `RESOURCE_MAX_BYTES` lines in the target `.env.docker.example` (section D) |
| 8 | non-destructive rollback | `pkill -INT -f 'uvicorn app.main:app'` and same for celery (dry-run sec. 4.8) | NOT IMPLEMENTED today; the future Docker equivalent is `docker compose stop` (preserves volumes) - documented as target only |

**Operator instruction**: a PC with Docker installed should
proceed via the single-machine path
([install-marco-dry-run.md](install-marco-dry-run.md)).
Having Docker installed is neutral - it does NOT change the
install procedure today, and trying `docker compose up` will
fail with a missing-config error within seconds.

---

## Target shape (specification only)

When the Docker batch lands, it must deliver these artifacts and
satisfy these contracts. This section is normative for that future
work — not a procedure operators run today.

### A. `backend/Dockerfile`

- Multi-stage. Build stage: Debian/Ubuntu base with `libpq-dev`,
  `libgdal-dev`, `libmagic1`, `build-essential`. Installs
  `requirements.txt` into `/opt/venv`.
- Runtime stage: slim base with only the runtime libraries
  (`libpq5`, `libgdal*`, `libmagic1`). Copies `/opt/venv` and the
  application source. Runs as a non-root user.
- `EXPOSE 5004`, `ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app"]`,
  default `CMD ["--host", "0.0.0.0", "--port", "5004", "--workers", "2"]`.
- `HEALTHCHECK --interval=10s --timeout=3s CMD curl -fsS http://127.0.0.1:5004/health || exit 1`.

The same image must be reusable for the worker and beat services by
overriding `CMD`:

- worker: `["celery", "-A", "workers.celery_app", "worker", "--loglevel=info"]`
- beat: `["celery", "-A", "workers.celery_app", "beat", "-S", "workers.beat_scheduler.PlenoraBeatScheduler", "--loglevel=info"]`

### B. `frontend/Dockerfile`

Two-stage:

1. Node build: `npm ci && npm run build` produces `dist/`.
2. nginx static serve from `dist/`. Healthcheck:
   `wget -qO- http://127.0.0.1/ || exit 1`.

### C. `docker-compose.yml`

Services (minimum):

- `postgres:15-alpine` with named volume `postgres-data` and
  `pg_isready` healthcheck;
- `redis:7-alpine` with named volume `redis-data` (AOF on) and
  `redis-cli ping` healthcheck;
- `backend` (built from `backend/Dockerfile`),
  `depends_on: postgres + redis (condition: service_healthy)`;
- `worker` (same image, different CMD), `depends_on: redis +
  backend`;
- `beat` (same image, different CMD), `depends_on: redis + backend`;
- `frontend` (built from `frontend/Dockerfile`),
  `depends_on: backend`;
- optional `minio` service behind `--profile minio`, with named
  volume `minio-data`.

Networks: a single user-defined bridge so service-name DNS works
(`postgres`, `redis`, `minio`).

Volumes:

- `postgres-data` (persistence);
- `redis-data` (AOF for beat leader-lock resilience);
- `data-dir` mounted into the backend at `/data` (the `DATA_DIR`
  setting).

### D. `.env.docker.example`

Minimal env tuned for in-cluster service-name resolution:

```
ENVIRONMENT=development
DATABASE_URL=postgresql://plenora:plenora@postgres:5432/plenora
DB_SCHEMA=plenora
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
STORAGE_POLICY=db
BACKUP_BUCKET=plenora-docker-no-real-s3
DATA_DIR=/data
# Memory caps (override in compose if container memory differs):
# LOAD_FILE_MAX_BYTES=209715200       # 200 MB upload cap
# WORKER_RSS_MAX_BYTES=1500000000     # 1.5 GB worker RSS
# RESOURCE_MAX_BYTES=524288000        # 500 MB single-blob read cap
# ARTIFACT_PREVIEW_MAX_BYTES=26214400 # 25 MB intermediate preview cap
#                                      (separate from RESOURCE_MAX_BYTES;
#                                      bounds the API-thread Parquet read)
# Generate before first up:
# PLENORA_ENCRYPTION_KEY=
# JWT_SECRET_KEY=
```

### E. Target install procedure

```
git clone <repo>
cd plenora
cp .env.docker.example .env
# Generate the two secrets:
# python -c "import secrets; print('PLENORA_ENCRYPTION_KEY=' + secrets.token_urlsafe(48))" >> .env
# python -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
docker compose up -d --wait
# Wait for backend health to be 200 (compose --wait gates on healthcheck).
# Visit http://localhost:<frontend-port>/.
```

Schema auto-create runs on first boot of the backend container via
`migrations_runner.ensure_schema(DB_SCHEMA)` — same path as the
non-Docker install.

---

## Out of scope for the Docker batch

These items are explicitly NOT solved by the future Docker compose
batch and remain separate investments:

- IAM / SSM secrets workflow (the AWS production path is host-bound
  today; mapping it to compose secrets is a separate stream).
- Bucket lifecycle rules (`backend/deploy/terraform/`).
- Multi-replica / Kubernetes scale-out (`PlenoraBeatScheduler` is
  leader-elected via Redis SETNX so multiple beat replicas are safe,
  but a Kubernetes manifest layer is a separate batch — audit §7.C).
- TLS termination and public-facing reverse-proxy (assumed external).

---

## Pointers

- Audit blocker: [`../../.agent-temp/BE_INSTALLATION_AUDIT.md`](../../.agent-temp/BE_INSTALLATION_AUDIT.md)
  §6.1, §7.1, §7.B (only available in the working-copy audit at the
  time of writing).
- Non-Docker install: [install-non-docker.md](install-non-docker.md).
- Cleanroom CI verifier (HTTP boot from empty Postgres + Redis):
  `.github/workflows/ci-cleanroom.yml` —
  the closest existing approximation to a Docker-style "fresh stack"
  smoke.
