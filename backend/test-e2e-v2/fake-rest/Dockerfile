FROM python:3.14-bookworm

COPY .python-version /opt/plenora/.python-version
COPY scripts/check_python_version.py /opt/plenora/check_python_version.py
RUN python /opt/plenora/check_python_version.py --runtime-only --version-file /opt/plenora/.python-version

USER root
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/test-e2e-v2/fake-rest/requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY backend/test-e2e-v2/fake-rest/ .

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD curl -fsS http://localhost:8080/health || exit 1

ENTRYPOINT []
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
