# BE-GO-READY-INSTALL-011-FIRST-DOCKER-SCAFFOLD
#
# Multi-stage backend image. First scaffold: minimal, executable, but
# not yet hardened for production deployment. The single-machine path
# (docs/ops/install-non-docker.md) remains the production reference.
#
# Build stage installs the apt packages required to compile any source
# wheels and lock the native shared libs (libpq, libgdal, libmagic1).
# Runtime stage carries only the runtime *.so libraries plus the venv.
#
# Reused for backend / worker / beat by overriding the CMD in
# docker-compose.yml; the image itself does not bake a single role.

# ---------------------------------------------------------------------------
# Build stage
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Native deps required to install/compile the requirements.txt wheels:
#   libpq-dev      -> psycopg2-binary already ships libpq, but compile
#                     fallback path needs the headers
#   libgdal-dev    -> fiona/geopandas load GDAL via ctypes at import
#   libmagic1      -> precautionary; filetype is pure-Python so this
#                     only matters if a future dep pulls libmagic
#   build-essential, gcc, g++ -> any source-only wheel
#   curl + ca-certificates -> for HEALTHCHECK and TLS to PyPI mirrors
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libgdal-dev \
        libmagic1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create the venv inside /opt so the runtime stage can copy it
# verbatim. Python interpreter is the same 3.11 slim base.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# Copy only the requirements.txt first to let Docker cache the
# pip-install layer when application source changes.
COPY backend/requirements.txt ./requirements.txt

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy the application source. Build context is the repo root (set by
# docker-compose.yml `context: .`), so all paths are repo-relative.
COPY backend/ ./

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# Runtime native libs only (no -dev headers). libpq5 e' richiesto da
# psycopg2 wheels at runtime; libgdal* + libmagic1 mirror il build
# stage. curl resta per la probe HEALTHCHECK.
#
# postgresql-client-17 fornisce `pg_dump` / `pg_restore` allineati al
# server PG 17 dell'image `postgres:17-alpine`. Senza un major-match
# il dump fallisce con "aborting because of server version mismatch"
# (pg_dump rifiuta di leggere un server piu' recente). Repo ufficiale
# PostgreSQL apt necessario perche' Debian bookworm porta solo 15.x.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libgdal32 \
        libmagic1 \
        gnupg \
        curl \
        ca-certificates \
    && install -d /usr/share/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client-17 \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. The data dir under /data is mounted from a
# named volume (docker-compose.yml volumes:); we chown it on first
# boot via the entrypoint, NOT during build (would conflict with the
# anonymous build-time UID).
RUN groupadd --system --gid 1100 plenora \
    && useradd --system --uid 1100 --gid 1100 \
       --home-dir /home/plenora --create-home --shell /bin/bash plenora \
    && mkdir -p /data \
    && chown plenora:plenora /data

WORKDIR /app

# Copy the venv from the build stage (already holds psycopg2 + fiona +
# geopandas + shapely + pyarrow + pandas + filetype + boto3 + ... in
# their canonical wheel locations under /opt/venv/lib/python3.11/site-packages).
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app

# Drop privileges. Mounts that need write access are pre-chowned
# above (/data) or are read-only (/app source).
USER plenora:plenora

EXPOSE 5004

# /health is the canonical liveness probe. Bounded: 3-second timeout
# inside the container; the docker daemon retries per its own
# `--interval`. This is the same surface single-machine smoke uses
# (`curl -fsS http://127.0.0.1:5004/health`).
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5004/health || exit 1

# Default CMD = backend (uvicorn). docker-compose.yml overrides CMD
# for the worker and beat services; the image is shared.
ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app"]
CMD ["--host", "0.0.0.0", "--port", "5004", "--workers", "1"]
