# Riusiamo l'image del backend plenora come base: ha gia' fastapi /
# uvicorn / pydantic / python-multipart installati, e pip "vivo"
# (l'image base `python:3.11-slim` con pip 24.0 incappa in bug noti
# del parsing del simple index PyPI, vedi fromisoformat TypeError).
# L'image deve essere stata gia' buildata da `docker compose build`
# del root compose; il riferimento per nome:tag e' stabile.
FROM plenora-backend:scaffold

USER root
WORKDIR /app

# curl is used by the HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Installiamo solo i pacchetti specifici del fake-rest non gia' nel
# backend (pyshp per il geo). Il resto e' un no-op (Already satisfied).
RUN pip install --no-cache-dir --index-url https://pypi.org/simple/ -r requirements.txt

COPY . .

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD curl -fsS http://localhost:8080/health || exit 1

ENTRYPOINT []
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
