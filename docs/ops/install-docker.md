# Docker Install Procedures

This is the Docker installation entry point for Plenora.

Status marker: FIRST SCAFFOLD, NOT YET VALIDATED end-to-end on a real Linux PC.
Traceability: BE-GO-READY-INSTALL-011-FIRST-DOCKER-SCAFFOLD.

Use one of these procedures:

| Target | Procedure |
|---|---|
| One server, one Docker Engine, local compose-managed Postgres/RabbitMQ | [Single-server Docker install](install-docker-single-server.md) |
| Multiple Docker hosts or separated Docker roles, shared Postgres/RabbitMQ/object storage | [Multi-server Docker install](install-docker-multi-server.md) |

## Decision Rule

Use the single-server procedure for first validation, demos, small internal
deployments, and release acceptance on one clean host.

Use the multi-server procedure only when the single-server procedure is already
green and the deployment needs separate app workers, external Postgres/RabbitMQ,
load balancing, or shared object storage.

Do not mix the two procedures halfway. The root `docker-compose.yml` is the
single-server scaffold. The multi-server procedure intentionally uses explicit
per-role Docker commands and external dependencies because compose service DNS
names such as `postgres` and `rabbitmq` are local to one compose network.

## Automated Installers

Single-server Docker has executable installers:

```bash
bash scripts/install-docker.sh --check
bash scripts/install-docker.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-docker.ps1 -Check
powershell -ExecutionPolicy Bypass -File scripts/install-docker.ps1
```

Use `--install-deps` on Linux Debian/Ubuntu to install Git, curl, Python 3,
Docker Engine, and the Docker Compose v2 plugin through apt. Use `-InstallDeps`
on Windows to install Git and Docker Desktop through winget.

On Linux, the installer prompts for the first superadmin username/password
during interactive installs. For unattended installs, set:

```bash
export PLENORA_BOOTSTRAP_ADMIN_USERNAME=admin
export PLENORA_BOOTSTRAP_ADMIN_PASSWORD='<strong-password>'
bash scripts/install-docker.sh
```

Use `--skip-admin-bootstrap` to leave the user table empty.

The Linux installer also asks for the number of Dramatiq worker containers.
Default is one worker. For unattended installs:

```bash
export PLENORA_WORKER_REPLICAS=2
bash scripts/install-docker.sh
```

For remote frontend builds:

```bash
bash scripts/install-docker.sh --frontend-api-url http://<host-ip-or-dns>:5004
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-docker.ps1 -FrontendApiUrl http://<host-ip-or-dns>:5004
```

If SQL Server connections must be validated during install, add
`--require-sqlserver-odbc` on Linux or `-RequireSqlServerOdbc` on Windows. This
fails the provider check unless the backend image exposes Microsoft ODBC Driver
18 or 17 through `pyodbc.drivers()`.

## Status today (command-first, executable scaffold)

BE-GO-READY-INSTALL-010-LINUX-EXECUTION-PACK.

| Harness item | Procedure | Validated on Linux PC |
|---|---|---|
| prereqs | `docker --version`, `docker compose version` | NO (scaffold) |
| env decisions | create `.env.docker` from `.env.docker.example` | NO (scaffold) |
| install | `docker compose config --quiet` and `docker compose build backend frontend` | NO (scaffold) |
| smoke | `bash scripts/docker-validate.sh` | NO (scaffold) |
| health/ready | `curl -fsS http://127.0.0.1:5004/health` and `/ready` | NO (scaffold) |
| Dramatiq check | `cleanroom.ping` through the worker | NO (scaffold) |
| memory caps visibility | print backend memory cap settings | NO (scaffold) |
| non-destructive rollback | `docker compose stop` | NO (scaffold) |

Use the dedicated [single-server Docker install](install-docker-single-server.md)
for the current operator flow.

## Target shape (specification only)

Single-server Docker runs all services on one Docker Engine. Multi-server
Docker separates app roles from shared Postgres, RabbitMQ, and object storage.

## Validation pack for the Linux PC test

BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK.

Preferred runner:

```bash
bash scripts/docker-validate.sh --dry-run
bash scripts/docker-validate.sh
```

The validation script is non-destructive. It must not run
`docker compose down`, `docker compose down -v`, `docker volume rm`, or
`docker system prune`; the normal teardown is `docker compose stop`.

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
2. **`docker compose build backend frontend`**
   Expected: backend and frontend images build successfully.
3. **`docker compose up -d --wait backend`**
   Expected: Postgres, RabbitMQ, and backend become healthy enough for API probes.
4. **`curl -fsS http://127.0.0.1:5004/health`**
   Expected: HTTP 200 from the backend liveness surface, followed by HTTP 200
   from `/ready`.
5. **`docker compose up -d worker beat`**
   Expected: worker and beat containers stay running.
6. **`docker compose up -d --wait frontend`**
   Expected: frontend container becomes healthy.
7. **`curl -fsS http://127.0.0.1:3000/`**
   Expected: frontend SPA shell is served by nginx.
8. **`cleanroom.ping`**
   Expected: the Dramatiq worker runs the task and it reaches SUCCESS in
   PostgreSQL (`task_results`).
9. **memory caps visibility**
   Expected: runtime memory caps print from backend settings.
10. **`docker compose stop`**
   Expected: stack stops without deleting volumes or `.env.docker`.

## What ships today vs what does NOT

BE-GO-READY-INSTALL-018-DOCKER-FE-GAP-AUDIT.

| Area | Status |
|---|---|
| Backend image | Ships. |
| Frontend image | Ships. |
| Compose frontend service | Ships. |
| Single-server compose runbook | Ships in `install-docker-single-server.md`. |
| Multi-server procedure | Ships in `install-docker-multi-server.md`; requires external dependencies. |
| Real Linux host validation report | Required before relying on Docker for release operations. |

### When can the Docker path be called "validated"?

When the selected procedure is green on a real Linux host, including frontend
build/static delivery checks, backend readiness, worker round-trip, and the
API smoke. CI config/build checks are necessary coverage, but they are not a
replacement for the real-host runtime validation report.

## Known scaffold limitations

- The root `docker-compose.yml` is single-server only.
- Multi-server installs require external Postgres, RabbitMQ, and object storage.
- The validation report is still required per target environment.
- The frontend API URL is compiled at build time and must be rebuilt if the
  public API origin changes.

## Required Evidence

For either procedure, keep a validation report with:

- commit hash;
- host OS and Docker versions;
- generated `.env`/secret handling evidence without printing secrets;
- `/health` and `/ready` outputs;
- frontend URL checked from a browser;
- Dramatiq `cleanroom.ping` result;
- smoke project create/list/delete or full smoke pipeline result;
- stop conditions or deviations.
