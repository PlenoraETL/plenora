# Plenora-ETL

ETL platform for geospatial and tabular data pipelines.

Backend: FastAPI, Dramatiq, PostgreSQL, RabbitMQ. Frontend: React, Vite,
ReactFlow-based pipeline canvas.

> `Plenora-ETL` is the canonical product and repository name. Runtime
> configuration uses `PLENORA_*`.

---

## Quickstart

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

export PLENORA_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DATABASE_URL="postgresql://plenora:plenora@localhost:5432/plenora"
export DRAMATIQ_BROKER_URL="amqp://guest:guest@localhost:5672/"

uvicorn app.main:app --reload --port 5004
python -m dramatiq workers.dramatiq_app --processes 1 --threads 8
python -m workers.beat
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Extended setup:

- Development onboarding: [docs/development/onboarding.md](docs/development/onboarding.md)
- Backend setup: [docs/development/backend.md](docs/development/backend.md)
- Frontend setup: [docs/development/frontend.md](docs/development/frontend.md)
- Windows native install: [docs/ops/install-windows.md](docs/ops/install-windows.md)
- Clean install, non-Docker: [docs/ops/install-non-docker.md](docs/ops/install-non-docker.md)
- Clean install, Docker: [docs/ops/install-docker.md](docs/ops/install-docker.md)

---

## Local Quality Gates

Backend hygiene:

```bash
python -m ruff check backend/app backend/workers backend/scripts backend/tests2 scripts --select F401,F403,F405,F811,F821,F841
python -m pyflakes backend/app backend/workers backend/scripts backend/tests2 scripts
```

Frontend build:

```bash
cd frontend
npm run build
```

See [docs/development/contributing.md](docs/development/contributing.md)
for the full contributor workflow.

---

## Documentation

All canonical documentation lives under [docs/](docs/).

- Product: [overview](docs/product/overview.md), [release 1.0 readiness](docs/product/release-1.0.md)
- Architecture: [overview](docs/architecture/overview.md), [macro components](docs/architecture/macro-components.md), [horizontal engines](docs/architecture/horizontal-engines.md), [invariants](docs/architecture/invariants.md)
- Development: [onboarding](docs/development/onboarding.md), [backend](docs/development/backend.md), [frontend](docs/development/frontend.md), [contributing](docs/development/contributing.md), [code map](docs/development/code-map.md), [bug ledger](docs/development/bug-ledger.md)
- Ops: [install Windows](docs/ops/install-windows.md), [install non-Docker](docs/ops/install-non-docker.md), [install Docker](docs/ops/install-docker.md), [runbook](docs/ops/runbook.md), [backup/restore](docs/ops/backup-restore.md), [incident response](docs/ops/incident_response.md)
- Compliance: [secrets management](docs/compliance/secrets-management.md), [SBOM](docs/compliance/sbom.md), [ISO 27001 SoA](docs/compliance/soa-iso-27001.md), [GDPR register](docs/compliance/registro-trattamenti-gdpr.md)

Start from [docs/README.md](docs/README.md) for the full index.

---

## License and Contacts

Proprietary. Private repository.

Operations contact: [docs/ops/handoff.md](docs/ops/handoff.md)
Incident response: [docs/ops/incident_response.md](docs/ops/incident_response.md)
