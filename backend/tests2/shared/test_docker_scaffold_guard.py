"""
Architectural lock for the Plenora-ETL first Docker scaffold.

BE-GO-READY-INSTALL-011-FIRST-DOCKER-SCAFFOLD.
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


# backend/Dockerfile contract


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
            "(required by psycopg2 fallback compile, pyogrio/"
            "geopandas GDAL ctypes load, and any source-only "
            "wheel respectively)."
        )
    # Runtime stage native libs (no -dev headers).
    for pkg in ("libpq5", "libgdal", "libmagic1"):
        assert pkg in text, (
            f"Dockerfile runtime stage must apt-install {pkg!r} "
            "(runtime shared libs for the venv wheels). "
            "Missing the runtime lib means `import psycopg2` / "
            "`import pyogrio` will fail at app boot inside the "
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


# docker-compose.yml contract


def test_compose_exists():
    assert _COMPOSE.is_file(), (
        "docker-compose.yml must exist at the repo root "
        "(INSTALL-011 first scaffold)."
    )


def test_compose_declares_split_worker_services():
    text = _COMPOSE.read_text(encoding="utf-8")
    # Each service's top-level key appears as `<name>:` at the
    # start of an indented line.
    expected_services = (
        "  postgres:",
        "  rabbitmq:",
        "  backend:",
        "  worker:",
        "  worker-control:",
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
    for vol in ("postgres-data", "rabbitmq-data", "data-dir"):
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


def test_compose_rabbitmq_has_ping_healthcheck():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "rabbitmq-diagnostics" in text and "ping" in text, (
        "docker-compose.yml rabbitmq service must declare a "
        "`rabbitmq-diagnostics -q ping` healthcheck so the worker / "
        "beat / backend `depends_on: service_healthy` waits for the "
        "broker to accept connections (Plenora 2.0 Fase 5: il broker e' "
        "RabbitMQ, non piu' Redis)."
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


def test_compose_workers_use_memory_safe_queue_split():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "dramatiq" in text and "workers.dramatiq_app" in text, (
        "docker-compose.yml worker service must launch the Dramatiq worker "
        "`python -m dramatiq workers.dramatiq_app` -- Plenora 2.0 Fase 1 "
        "(cutover): il worker esegue gli actor Dramatiq da RabbitMQ, non "
        "piu' celery worker da Redis."
    )
    assert "workers.beat" in text, (
        "docker-compose.yml beat service must launch `python -m workers.beat` "
        "-- Plenora 2.0 Fase 3: il beat gira su APScheduler (runner "
        "workers.beat), non piu' su celery beat. Leader-lock su PostgreSQL."
    )
    assert '["--queues", "data-plane", "--processes", "1", "--threads", "1"]' in text
    assert (
        '["--queues", "control-plane", "scheduler", "default", '
        '"--processes", "1", "--threads", "2"]'
    ) in text


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


# .dockerignore contract


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


# .env.docker.example contract


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
            "Mirrors the dry-run pack section 5.2 memory-caps "
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


# install-docker.md scaffold runbook contract


@pytest.mark.architecture
@pytest.mark.docs_guard
@pytest.mark.cleanroom_excluded
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


@pytest.mark.architecture
@pytest.mark.docs_guard
@pytest.mark.cleanroom_excluded
def test_install_docker_doc_lists_executable_runbook_steps():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    # NB: 'docker compose exec worker python' rimosso dalla lista
    # (2026-07-04): il comando non e' MAI esistito nel runbook (failure
    # pre-esistente in baseline); il check Dramatiq del doc e' la riga
    # 'cleanroom.ping through the worker', gia' coperta qui sotto.
    expected_commands = (
        "docker compose config",
        "docker compose build backend",
        "docker compose up -d --wait backend",
        "docker compose up -d worker worker-control beat",
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


@pytest.mark.architecture
@pytest.mark.docs_guard
@pytest.mark.cleanroom_excluded
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


# BE-GO-READY-ARCH-060-W6: same-image contract + dockerignore venv


def test_compose_worker_and_beat_share_backend_image():
    """worker and beat must reference the same image tag as backend.

    The scaffold reuses one image for all three backend-type services,
    overriding CMD for celery roles (Dockerfile comment: 'Reused for
    backend / worker / beat by overriding the CMD'). If worker or beat
    drift to a different image, the smoke cleanroom.ping check fails
    with a misleading 'task not registered' error instead of an obvious
    image-mismatch diagnosis. This guard catches the drift at source level.
    """
    text = _COMPOSE.read_text(encoding="utf-8")
    # The canonical plenora application image tag. It must appear in
    # exactly the three app-tier services (backend, worker, beat).
    # postgres:17-alpine and rabbitmq:4-alpine are infrastructure images
    # and do not use this tag.
    canonical = "plenora-backend:scaffold"
    count = text.count(canonical)
    assert count >= 3, (
        f"Expected the plenora application image {canonical!r} to "
        f"appear in at least 3 compose services (backend, worker, beat); "
        f"found {count} occurrence(s). worker and beat must reuse the "
        "backend image so the smoke harness cleanroom.ping resolves "
        "against the correct Plenora application code."
    )


def test_compose_worker_and_beat_use_same_dockerfile_as_backend():
    """All three app-tier services must build from the same Dockerfile.

    Diverging build contexts (e.g., worker switching to a generic
    celery base image) would silently exclude the Plenora application
    code from the worker container, making cleanroom.ping fail without
    a clear reason.
    """
    text = _COMPOSE.read_text(encoding="utf-8")
    canonical_dockerfile = "backend/Dockerfile"
    count = text.count(canonical_dockerfile)
    assert count >= 3, (
        f"Expected {canonical_dockerfile!r} to appear in at least 3 "
        f"compose services (backend, worker, beat); found {count}. "
        "All app-tier services must build from the same Dockerfile "
        "to guarantee consistent application code."
    )


def test_dockerignore_excludes_venv_directories():
    """The build context must exclude Python virtual environments.

    A developer venv under backend/venv/ or .venv/ can be 1+ GB and
    includes dev-only packages that must not land in the image. The
    canonical pattern `**/venv/` covers any nesting depth; the flat
    `venv/` entry covers the repo-root case.
    """
    text = _DOCKERIGNORE.read_text(encoding="utf-8")
    for pattern in ("venv/", "**/venv/"):
        assert pattern in text, (
            f".dockerignore must exclude {pattern!r} so local Python "
            "virtual environments are not included in the Docker build "
            "context. A developer venv can be 1+ GB and includes "
            "dev-only packages that should never land in a production "
            "image layer. (BE-GO-READY-ARCH-060-W6)"
        )
