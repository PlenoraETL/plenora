# Plenora Backend Docker image — multi-stage Ubuntu 24.04 LTS.
#
# Stack target (verificato latest stable al 2026-05-25):
#   - Ubuntu 24.04 LTS (noble), base ~28MB, support fino 2029
#   - Python 3.12.3 (apt default)
#   - PROJ 9.8.1 (from source, apt default 9.4.0)
#   - GEOS 3.14.1 (from source, apt default 3.12.1)
#   - GDAL 3.13.0 (from source, no precompiled GDAL 3.13 in apt)
#   - PostgreSQL client 17 (apt.postgresql.org pgdg-noble)
#   - msodbcsql18 (packages.microsoft.com Ubuntu 24.04)
#
# Multi-stage:
#   1. gdal-build: compila PROJ+GEOS+GDAL from source in /opt/gdal
#   2. python-deps: venv Python + pip install requirements.txt con
#      pyogrio forzato `--no-binary` per linkare al GDAL custom
#   3. runtime: stage minimale (no -dev headers, no build tools);
#      copia /opt/gdal + /opt/venv + apt runtime libs.
#
# L'image risultante e' condivisa fra backend / worker / beat:
# docker-compose.yml overrida solo il CMD.

# =============================================================================
# Stage 1: gdal-build (compila PROJ + GEOS + GDAL from source)
# =============================================================================
FROM ubuntu:24.04 AS gdal-build

ENV DEBIAN_FRONTEND=noninteractive

# Build tools + dependencies di GDAL/PROJ/GEOS. Niente Python qui:
# il source build non lo richiede.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        ca-certificates \
        curl \
        pkg-config \
        libtiff-dev \
        libcurl4-openssl-dev \
        libxml2-dev \
        libsqlite3-dev \
        libexpat1-dev \
        libwebp-dev \
        libpng-dev \
        libjpeg-dev \
        libzstd-dev \
        liblzma-dev \
        zlib1g-dev \
        sqlite3 \
        nlohmann-json3-dev \
    && rm -rf /var/lib/apt/lists/*

# Versioni from-source (allineate alle latest stable verificate 2026-05-25).
ARG PROJ_VERSION=9.8.1
ARG GEOS_VERSION=3.14.1
ARG GDAL_VERSION=3.13.0

WORKDIR /build

# Build PROJ 9.8.1
RUN curl -fsSL "https://download.osgeo.org/proj/proj-${PROJ_VERSION}.tar.gz" -o proj.tar.gz \
    && tar xzf proj.tar.gz \
    && cd "proj-${PROJ_VERSION}" \
    && cmake -B build -S . \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/gdal \
        -DBUILD_TESTING=OFF \
        -DBUILD_APPS=OFF \
    && cmake --build build -j"$(nproc)" \
    && cmake --install build \
    && cd .. \
    && rm -rf "proj-${PROJ_VERSION}" proj.tar.gz

# Build GEOS 3.14.1
RUN curl -fsSL "https://download.osgeo.org/geos/geos-${GEOS_VERSION}.tar.bz2" -o geos.tar.bz2 \
    && tar xjf geos.tar.bz2 \
    && cd "geos-${GEOS_VERSION}" \
    && cmake -B build -S . \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/gdal \
        -DBUILD_TESTING=OFF \
        -DBUILD_DOCUMENTATION=OFF \
    && cmake --build build -j"$(nproc)" \
    && cmake --install build \
    && cd .. \
    && rm -rf "geos-${GEOS_VERSION}" geos.tar.bz2

# Build GDAL 3.13.0 linkato a PROJ/GEOS custom.
# - BUILD_PYTHON_BINDINGS=OFF: i binding Python li installiamo via pip
#   nello stage successivo (pip install --no-binary pyogrio).
# - BUILD_APPS=ON: serve gdal_translate/ogr2ogr per debugging operativo.
RUN curl -fsSL "https://github.com/OSGeo/gdal/releases/download/v${GDAL_VERSION}/gdal-${GDAL_VERSION}.tar.gz" -o gdal.tar.gz \
    && tar xzf gdal.tar.gz \
    && cd "gdal-${GDAL_VERSION}" \
    && cmake -B build -S . \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/gdal \
        -DCMAKE_PREFIX_PATH=/opt/gdal \
        -DBUILD_PYTHON_BINDINGS=OFF \
        -DBUILD_TESTING=OFF \
        -DBUILD_DOCS=OFF \
        -DGDAL_USE_PROJ=ON \
        -DGDAL_USE_GEOS=ON \
        -DGDAL_USE_TIFF=ON \
        -DGDAL_USE_CURL=ON \
        -DGDAL_USE_SQLITE3=ON \
        -DGDAL_USE_EXPAT=ON \
        -DGDAL_USE_WEBP=ON \
        -DGDAL_USE_PNG=ON \
        -DGDAL_USE_JPEG=ON \
        -DGDAL_USE_ZSTD=ON \
    && cmake --build build -j"$(nproc)" \
    && cmake --install build \
    && cd .. \
    && rm -rf "gdal-${GDAL_VERSION}" gdal.tar.gz

# Sanity check: il GDAL appena buildato deve linkare correttamente.
# LD_LIBRARY_PATH necessario qui perche' lo stage build non ha
# ldconfig configurato per /opt/gdal/lib.
ENV LD_LIBRARY_PATH=/opt/gdal/lib
RUN /opt/gdal/bin/gdalinfo --version \
    && /opt/gdal/bin/gdalinfo --formats | head -5

# =============================================================================
# Stage 2: python-deps (venv + pip install con GDAL custom)
# =============================================================================
FROM ubuntu:24.04 AS python-deps

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Native build deps necessarie per i pacchetti pip che compilano da
# source (pyogrio linka contro libgdal; psycopg[binary]
# usa libpq vendorizzata, niente -dev richiesto).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-full \
        python3-venv \
        python3-dev \
        build-essential \
        ca-certificates \
        curl \
        libmagic1 \
        libtiff6 \
        libcurl4 \
        libxml2 \
        libsqlite3-0 \
        libexpat1 \
        libwebp7 \
        libpng16-16t64 \
        libjpeg-turbo8 \
        libzstd1 \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Copia GDAL/PROJ/GEOS dal build stage.
COPY --from=gdal-build /opt/gdal /opt/gdal

# Env: pyogrio setup.py legge GDAL_CONFIG per linkare al
# GDAL custom invece del libgdal-dev di sistema. LD_LIBRARY_PATH
# permette l'import runtime durante il test post-install.
ENV GDAL_CONFIG=/opt/gdal/bin/gdal-config \
    LD_LIBRARY_PATH=/opt/gdal/lib \
    PROJ_DATA=/opt/gdal/share/proj \
    GDAL_DATA=/opt/gdal/share/gdal

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt

# pip install:
#   - upgrade pip per supportare i wheel format piu' recenti
#   - --no-binary pyogrio forza compile-from-source contro
#     il nostro GDAL 3.13.0 (le wheel ufficiali hanno GDAL vendored
#     piu' vecchio, mix non condivide /vsimem)
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-binary=pyogrio -r requirements.txt

# Sanity check post-install: pyogrio deve riportare la STESSA versione
# GDAL del nostro build (no /vsimem mismatch).
RUN python3 -c "import pyogrio; print(f'pyogrio: {pyogrio.__version__}, GDAL via pyogrio: {pyogrio.__gdal_version_string__}')"

COPY backend/ ./

# =============================================================================
# Stage 3: runtime (minimal, no build tools)
# =============================================================================
FROM ubuntu:24.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    GDAL_CONFIG=/opt/gdal/bin/gdal-config \
    LD_LIBRARY_PATH=/opt/gdal/lib \
    PROJ_DATA=/opt/gdal/share/proj \
    GDAL_DATA=/opt/gdal/share/gdal

# Runtime layer: solo runtime libs (no -dev), Python interpreter,
# postgresql-client-17 (pgdg-noble), msodbcsql18 (packages.microsoft.com).
#
# postgresql-client-17 deve essere allineato al server PG 17 della
# image `postgres:17-alpine`: pg_dump rifiuta server piu' nuovi del
# client ("aborting because of server version mismatch"). Repo
# ufficiale PostgreSQL apt necessario perche' Ubuntu noble porta
# solo PG 16.
#
# msodbcsql18 fornisce il driver ODBC per pyodbc -> SQL Server.
# ACCEPT_EULA=Y richiesto da Microsoft EULA.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        ca-certificates \
        curl \
        gnupg \
        libmagic1 \
        libtiff6 \
        libcurl4 \
        libxml2 \
        libsqlite3-0 \
        libexpat1 \
        libwebp7 \
        libpng16-16t64 \
        libjpeg-turbo8 \
        libzstd1 \
        zlib1g \
        libgomp1 \
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
        msodbcsql18 \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

# Copia GDAL custom + venv dai precedenti stage.
COPY --from=gdal-build /opt/gdal /opt/gdal
COPY --from=python-deps /opt/venv /opt/venv
COPY --from=python-deps /app /app

# Non-root runtime user. /data e' mount volume (chown nel compose
# entrypoint, NON qui in build per evitare conflitto UID).
RUN groupadd --system --gid 1100 plenora \
    && useradd --system --uid 1100 --gid 1100 \
       --home-dir /home/plenora --create-home --shell /bin/bash plenora \
    && mkdir -p /data \
    && chown plenora:plenora /data

WORKDIR /app
USER plenora:plenora

EXPOSE 5004

# /health canonical liveness probe. Bounded a 3s; docker daemon
# retry-a per --interval.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5004/health || exit 1

# Default CMD = backend (uvicorn). docker-compose.yml overrida CMD
# per worker e beat; l'image e' condivisa.
ENTRYPOINT ["python3", "-m", "uvicorn", "app.main:app"]
CMD ["--host", "0.0.0.0", "--port", "5004", "--workers", "1"]
