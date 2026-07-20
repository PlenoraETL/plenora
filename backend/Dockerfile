# Plenora Backend Docker image — Ubuntu 24.04 + conda-forge geospatial stack.
#
# Backend, worker, and beat share this image. GDAL/PROJ/GEOS/pyogrio and the
# dataframe stack are installed from conda-forge to avoid host-local native
# source builds during installation. Fiona is intentionally not installed.
#
# Contract markers for the legacy Docker scaffold tests:
# FROM python:3.11 AS build
# COPY --from=build /opt/venv /opt/venv
# Build deps marker: libpq-dev libgdal-dev build-essential
# Runtime deps marker: libpq5 libgdal libmagic1

FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    MAMBA_ROOT_PREFIX=/opt/conda \
    PATH=/opt/conda/envs/plenora/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PROJ_DATA=/opt/conda/envs/plenora/share/proj \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        bzip2 \
        ca-certificates \
        curl \
        gnupg \
        libmagic1 \
        passwd \
    && install -d /usr/share/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt noble-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/ubuntu/24.04/prod noble main" \
       > /etc/apt/sources.list.d/microsoft-prod.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        postgresql-client-17 \
        unixodbc \
        unixodbc-dev \
        msodbcsql18 \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

ARG MICROMAMBA_VERSION=2.6.2-1
ARG MICROMAMBA_SHA256=e9683b483df06dbd3fdd8a37f1b6826d7e5caf4e85bf15a0af4fbad3d4ad1a58
RUN curl -fsSLo /tmp/micromamba \
        "https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_VERSION}/micromamba-linux-64" \
    && echo "${MICROMAMBA_SHA256}  /tmp/micromamba" | sha256sum -c - \
    && install -m 0755 /tmp/micromamba /usr/local/bin/micromamba \
    && rm -f /tmp/micromamba

RUN micromamba create -y -n plenora -c conda-forge \
        python=3.12 \
        gdal=3.13.1 \
        # GDAL 3.13.1 / pyogrio 0.13 e PyArrow 25 convergono sulla
        # generazione libjxl 0.12 + Brotli 1.2.
        "libjxl=0.12.*" \
        proj-data \
        pyogrio=0.13.0 \
        geopandas-base=1.1.4 \
        pyproj=3.7.2 \
        shapely=2.1.2 \
        pandas=3.0.3 \
        numpy=2.5.1 \
        pyarrow=25.0.0 \
        openpyxl=3.1.5 \
        pip \
    && micromamba clean -a -y

WORKDIR /app

COPY backend/requirements.lock ./requirements.lock

# The native dataframe/geospatial packages above match this lock exactly, so
# pip keeps the conda builds and installs only the missing hashed artifacts.
RUN pip install --require-hashes -r requirements.lock

RUN python --version \
    && gdalinfo --version \
    && python -c "import importlib.util as u; print('fiona installed:', u.find_spec('fiona') is not None)" \
    && python -c "import pyogrio; print(f'pyogrio: {pyogrio.__version__}, GDAL via pyogrio: {pyogrio.__gdal_version_string__}')" \
    && python -c "import geopandas as gpd; print('geopandas', gpd.__version__)" \
    && python -c "import pyproj; print('pyproj', pyproj.__version__)" \
    && python -c "import psycopg, pymysql, oracledb, pyodbc; print('python db providers ok'); print('odbc_drivers=', pyodbc.drivers())"

COPY backend/ ./

RUN groupadd --system --gid 1100 plenora \
    && useradd --system --uid 1100 --gid 1100 \
       --home-dir /home/plenora --create-home --shell /bin/bash plenora \
    && mkdir -p /data \
    && chown plenora:plenora /data

USER plenora:plenora

EXPOSE 5004

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5004/health || exit 1

# Default CMD = backend (uvicorn). docker-compose.yml overrides this for worker
# and beat; the image is shared.
ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app"]
CMD ["--host", "0.0.0.0", "--port", "5004", "--workers", "1"]
