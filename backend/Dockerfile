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

FROM ubuntu:24.04 AS runtime

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

RUN curl -fsSL https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xvj -C /usr/local/bin --strip-components=1 bin/micromamba

RUN micromamba create -y -n plenora -c conda-forge \
        python=3.12 \
        gdal=3.13.0 \
        # libjxl pinnato: il build conda-forge di gdal 3.13.0 linka
        # libjxl.so.0.11; senza pin il resolver prende libjxl 0.12 e
        # gdalinfo non parte (drift upstream, visto 2026-07-04).
        "libjxl=0.11.*" \
        proj-data \
        pyogrio \
        geopandas-base \
        pyproj \
        shapely \
        pandas \
        numpy \
        pyarrow \
        openpyxl \
        pip \
    && micromamba clean -a -y

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt

# These packages are provided by conda-forge above. Let pip install the
# application/runtime packages only, avoiding duplicate native geospatial stacks.
RUN awk 'BEGIN {IGNORECASE=1} \
    /^[[:space:]]*(geopandas|numpy|openpyxl|pandas|pyarrow|pyogrio|pyproj|shapely)(\[|[<>=!~[:space:]]|$)/ {print "# conda-forge provides: " $0; next} \
    {print}' requirements.txt > requirements-pip.txt \
    && pip install --upgrade pip setuptools wheel \
    && pip install -r requirements-pip.txt

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
