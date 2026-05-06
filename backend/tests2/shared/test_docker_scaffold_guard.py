"""Architectural lock for the Plenora-ETL first Docker scaffold.

BE-GO-READY-INSTALL-011-FIRST-DOCKER-SCAFFOLD.

Source-bound assertions that the four scaffold artifacts ship
with the canonical contract:

  - `backend/Dockerfile` (multi-stage build/runtime, non-root,
    HEALTHCHECK on /health, EXPOSE 5004, libgdal/libpq native
    deps);
  - `docker-compose.yml` (5 services: postgres, redis, backend,
    worker, beat; healthchecks; depends_on conditions; named
    volumes; cleanroom-aligned worker `--pool=solo` and beat
    `PlenoraBeatScheduler`);
  - `.dockerignore` (excludes `.env*`, venvs, agent state,
    pytest temps);
  - `.env.docker.example` (template; gitignored real
    `.env.docker` references; six commented memory caps).

Scope: existence + canonical-contract phrases. We do NOT spin
up Docker daemons or run `docker compose config` here -- the
test runs on Windows / CI without Docker. The contract is the
*shape* of the files; runtime validation is a separate stream.

Outside scope:
  - the install-docker.md scaffold runbook (locked by
    `test_install_docker_scaffold_doc_guard` below + the
    install-execution-pack guard);
  - the dry-run pack disclaimer (locked by
    `test_install_marco_dry_run_doc_guard.py`);
  - the canonical 8-item smoke harness (locked by
    `test_install_smoke_harness_doc_guard.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOCKERFILE = _REPO_ROOT / "backend" / "Dockerfile"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_DOCKERIGNORE = _REPO_ROOT / ".dockerignore"
_ENV_DOCKER_EXAMPLE = _REPO_ROOT / ".env.docker.example"
_GITIGNORE = _REPO_ROOT / ".gitignore"
_INSTALL_DOCKER = _REPO_ROOT / "docs" / "ops" / "install-docker.md"


# =====================================================================
# backend/Dockerfile contract
# =====================================================================


def test_dockerfile_exists():
    assert _DOCKERFILE.is_file(), (
        "backend/Dockerfile must exist (INSTALL-011 first "
        "scaffold). The file is the entry point of the Docker "
        "build; without it `docker compose build backend` fails "
        "with `unable to prepare context`."
    )


def test_dockerfile_is_multi_stage():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    # Multi-stage uses a build stage with `AS build` and a
    # runtime stage that COPYs from it. Both must appear.
    assert "FROM python:3.11" in text, (
        "Dockerfile must base on python:3.11 (matches the CI "
        "and single-machine pinned version)."
    )
    assert "AS build" in text, (
        "Dockerfile must declare a named build stage `AS build` "
        "so the runtime stage can copy /opt/venv from it. "
        "Single-stage images would bake build-only deps "
        "(libpq-dev, libgdal-dev, build-essential) into the "
        "runtime image and inflate it."
    )
    assert "COPY --from=build" in text, (
        "Dockerfile must copy artifacts from the build stage "
        "into the runtime stage via `COPY --from=build`. "
        "Without this the runtime stage has no application code."
    )


def test_dockerfile_installs_required_native_deps():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    # Build stage native deps required by the wheels.
    for pkg in ("libpq-dev", "libgdal-dev", "build-essential"):
        assert pkg in text, (
            f"Dockerfile build stage must apt-install {pkg!r} "
            "(required by psycopg2 fallback compile, fiona/"
            "geopandas GDAL ctypes load, and any source-only "
            "wheel respectively)."
        )
    # Runtime stage native libs (no -dev headers).
    for pkg in ("libpq5", "libgdal", "libmagic1"):
        assert pkg in text, (
            f"Dockerfile runtime stage must apt-install {pkg!r} "
            "(runtime shared libs for the venv wheels). "
            "Missing the runtime lib means `import psycopg2` / "
            "`import fiona` will fail at app boot inside the "
            "container."
        )


def test_dockerfile_runs_as_non_root():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert "useradd" in text, (
        "Dockerfile must create a non-root runtime user "
        "(`useradd`). Running uvicorn as root inside the "
        "container is unnecessary and complicates volume "
        "permissions on bind mounts."
    )
    assert "USER plenora" in text, (
        "Dockerfile must `USER plenora:plenora` (or equivalent) "
        "so the runtime stage drops privileges before "
        "ENTRYPOINT. Without the USER directive the container "
        "runs as root."
    )


def test_dockerfile_has_healthcheck_on_health_endpoint():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert "HEALTHCHECK" in text, (
        "Dockerfile must declare a HEALTHCHECK so `docker "
        "compose up --wait` can gate on backend readiness. "
        "Without it `--wait` returns immediately on container "
        "start, before the app boots."
    )
    assert "/health" in text, (
        "Dockerfile HEALTHCHECK must probe the /health endpoint "
        "(NOT /ready) -- /health is liveness; /ready is "
        "readiness. Probing /ready inside HEALTHCHECK risks "
        "oscillating between healthy/unhealthy on transient PG "
        "lag."
    )


def test_dockerfile_exposes_5004_and_uvicorn_default_cmd():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert "EXPOSE 5004" in text, (
        "Dockerfile must EXPOSE 5004 (the canonical backend "
        "port; matches `PLENORA_BACKEND_PORT` default and the "
        "single-machine systemd unit)."
    )
    # Default ENTRYPOINT is uvicorn for the backend service;
    # worker and beat services override CMD via compose.
    assert "uvicorn" in text and "app.main:app" in text, (
        "Dockerfile default ENTRYPOINT/CMD must launch uvicorn "
        "against `app.main:app`. The same image is reused for "
        "worker and beat by overriding CMD in docker-compose.yml."
    )


# =====================================================================
# docker-compose.yml contract
# =====================================================================


def test_compose_exists():
    assert _COMPOSE.is_file(), (
        "docker-compose.yml must exist at the repo root "
        "(INSTALL-011 first scaffold)."
    )


def test_compose_declares_five_canonical_services():
    text = _COMPOSE.read_text(encoding="utf-8")
    # Each service's top-level key appears as `<name>:` at the
    # start of an indented line.
    expected_services = (
        "  postgres:",
        "  redis:",
        "  backend:",
        "  worker:",
        "  beat:",
    )
    for service in expected_services:
        assert service in text, (
            f"docker-compose.yml must declare service "
            f"{service.strip(': ')!r}. The five-service "
            "scaffold mirrors the canonical smoke harness; "
            "dropping a service breaks the harness mapping."
        )


def test_compose_declares_named_volumes():
    text = _COMPOSE.read_text(encoding="utf-8")
    for vol in ("postgres-data", "redis-data", "data-dir"):
        assert vol in text, (
            f"docker-compose.yml must declare named volume "
            f"{vol!r}. Anonymous volumes get garbage-collected "
            "on `docker compose down`, defeating the "
            "non-destructive stop contract documented in "
            "install-docker.md."
        )


def test_compose_postgres_has_pg_isready_healthcheck():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "pg_isready" in text, (
        "docker-compose.yml postgres service must declare a "
        "`pg_isready` healthcheck so the backend's "
        "`depends_on: service_healthy` actually waits for PG to "
        "accept connections. Without the healthcheck the "
        "backend container races PG startup."
    )


def test_compose_redis_has_ping_healthcheck():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "redis-cli" in text and "PING" in text, (
        "docker-compose.yml redis service must declare a "
        "`redis-cli PING` healthcheck so the backend's "
        "`depends_on: service_healthy` waits for Redis to "
        "accept connections."
    )


def test_compose_uses_service_healthy_dependency():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "condition: service_healthy" in text, (
        "docker-compose.yml backend / worker / beat services "
        "must use `depends_on: <svc>: condition: service_healthy` "
        "(NOT just `depends_on: [postgres, redis]`). Without "
        "the healthy condition, the backend can start before "
        "PG accepts connections and the four boot gates fail."
    )


def test_compose_worker_uses_pool_solo_and_beat_uses_plenora_scheduler():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "--pool=solo" in text, (
        "docker-compose.yml worker service must launch celery "
        "with `--pool=solo` -- matches the single-machine "
        "smoke (dry-run pack §2.7) and the cleanroom CI lane. "
        "Switching to prefork in the scaffold would diverge "
        "from the validated path without justification."
    )
    assert "PlenoraBeatScheduler" in text, (
        "docker-compose.yml beat service must use "
        "`-S workers.beat_scheduler.PlenoraBeatScheduler` -- "
        "the canonical Plenora beat scheduler with Redis "
        "leader-lock."
    )


def test_compose_publishes_backend_5004():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert '"5004:5004"' in text or "5004:5004" in text, (
        "docker-compose.yml backend service must publish port "
        "5004 to the host so `curl -fsS "
        "http://127.0.0.1:5004/health` works from outside the "
        "compose network."
    )


def test_compose_loads_env_file():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "env_file" in text and ".env.docker" in text, (
        "docker-compose.yml backend / worker / beat services "
        "must load `env_file: .env.docker` so secrets and "
        "deployment policy can be injected without baking them "
        "into the image."
    )


# =====================================================================
# .dockerignore contract
# =====================================================================


def test_dockerignore_exists():
    assert _DOCKERIGNORE.is_file(), (
        ".dockerignore must exist at the repo root. Without it "
        "the build context includes `.git/`, the agent-temp "
        "scratchpad, and the frontend node_modules -- "
        "potentially leaking secrets and inflating image "
        "layers by hundreds of MB."
    )


def test_dockerignore_excludes_secrets_and_state():
    text = _DOCKERIGNORE.read_text(encoding="utf-8")
    # Critical: never bake `.env*` into a layer.
    expected_excludes = (
        ".env",
        ".agent-temp/",
        ".git/",
        "**/__pycache__/",
        "**/.pytest_cache/",
    )
    for needle in expected_excludes:
        assert needle in text, (
            f".dockerignore must exclude {needle!r}. "
            "Including secrets / agent state / pytest caches "
            "in the build context is a security and image-size "
            "bug."
        )


# =====================================================================
# .env.docker.example contract
# =====================================================================


def test_env_docker_example_exists():
    assert _ENV_DOCKER_EXAMPLE.is_file(), (
        ".env.docker.example must exist at the repo root. The "
        "compose file references `.env.docker` (gitignored); "
        "the .example variant is the committed template "
        "operators copy from."
    )


def test_env_docker_example_lists_canonical_keys():
    text = _ENV_DOCKER_EXAMPLE.read_text(encoding="utf-8")
    for key in (
        "ENVIRONMENT=",
        "DB_SCHEMA=",
        "STORAGE_POLICY=",
        "BACKUP_BUCKET=",
        "PLENORA_ENCRYPTION_KEY=",
        "JWT_SECRET_KEY=",
    ):
        assert key in text, (
            f".env.docker.example must contain {key!r}. The "
            "six canonical keys are the minimum needed for a "
            "first scaffold up; missing one means the operator "
            "must dig through install-non-docker.md to find it."
        )


def test_env_docker_example_documents_six_memory_caps():
    text = _ENV_DOCKER_EXAMPLE.read_text(encoding="utf-8")
    # Memory caps are commented (operator opts in) but must be
    # present so the override SHAPE is one copy/paste away.
    expected_caps = (
        "LOAD_FILE_MAX_BYTES",
        "WORKER_RSS_MAX_BYTES",
        "RESOURCE_MAX_BYTES",
        "ARTIFACT_PREVIEW_MAX_BYTES",
        "CROSS_JOIN_MAX_OUTPUT_ROWS",
        "MULTI_JOIN_MAX_OUTPUT_ROWS",
    )
    for cap in expected_caps:
        assert cap in text, (
            f".env.docker.example must reference memory cap "
            f"{cap!r} (commented is fine; the SHAPE matters). "
            "Mirrors the dry-run pack §5.2 memory-caps "
            "visibility list."
        )


def test_env_docker_real_file_is_gitignored():
    # The committed example file is `.env.docker.example`; the
    # real file `.env.docker` MUST be gitignored. We assert this
    # by checking the .gitignore root contains an `.env*` rule
    # (the existing one matches `.env.docker`).
    text = _GITIGNORE.read_text(encoding="utf-8")
    assert ".env" in text, (
        ".gitignore must continue to ignore `.env*` files so "
        "`.env.docker` (which contains generated secrets) "
        "never lands in git history."
    )


# =====================================================================
# install-docker.md scaffold runbook contract
# =====================================================================


def test_install_docker_doc_advertises_first_scaffold_status():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    assert "FIRST SCAFFOLD" in text, (
        "docs/ops/install-docker.md must advertise the FIRST "
        "SCAFFOLD status (replacing the prior NOT IMPLEMENTED "
        "status). Without this, an operator may think Docker "
        "is still unsupported."
    )
    assert "NOT YET VALIDATED" in text, (
        "install-docker.md must keep the 'NOT YET VALIDATED "
        "end-to-end on a real Linux PC' qualifier so the "
        "scaffold is not mistaken for a validated path."
    )
    assert "BE-GO-READY-INSTALL-011-FIRST-DOCKER-SCAFFOLD" in text, (
        "install-docker.md must reference the "
        "BE-GO-READY-INSTALL-011 stream tag."
    )


def test_install_docker_doc_lists_executable_runbook_steps():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    expected_commands = (
        "docker compose config",
        "docker compose build backend",
        "docker compose up -d --wait backend",
        "docker compose up -d worker beat",
        "docker compose exec worker python",
        "cleanroom.ping",
        "docker compose stop",
    )
    for cmd in expected_commands:
        assert cmd in text, (
            f"install-docker.md must include the executable "
            f"command {cmd!r} in the scaffold runbook. Each "
            "command corresponds to a harness item; missing "
            "one means an operator has to fabricate the "
            "command from the single-machine docs."
        )


def test_install_docker_doc_documents_validation_gaps():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    assert "Known scaffold limitations" in text, (
        "install-docker.md must contain a 'Known scaffold "
        "limitations' section. Without explicit gap "
        "enumeration, an operator may assume the scaffold is "
        "production-ready."
    )
    # The most important gap: no real-host validation.
    assert "validation report" in text.lower() or \
           "real Linux PC" in text, (
        "install-docker.md must explicitly call out the "
        "missing Linux-PC validation report as the primary "
        "scaffold gap."
    )
