# Plenora-ETL

ETL platform for geospatial and tabular data pipelines.

Backend: FastAPI, Celery, PostgreSQL, Redis. Frontend: React, Vite,
ReactFlow-based pipeline canvas.

> Status: backend structural hardening and horizontalization are in progress.
> The current focus is go-ready architecture, clean installation, Docker
> validation, and documentation cleanup before a stable 1.0 freeze.

> Brand note: `Plenora-ETL` is the canonical product and repository name.
> Historical audit/ADR material may mention older names, but live runtime
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
export CELERY_BROKER_URL="redis://localhost:6379/0"
export CELERY_RESULT_BACKEND="redis://localhost:6379/0"

uvicorn app.main:app --reload --port 5004
celery -A workers.celery_app worker --loglevel=info
celery -A workers.celery_app beat --loglevel=info
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
- Clean install, non-Docker: [docs/ops/install-non-docker.md](docs/ops/install-non-docker.md)
- Clean install, Docker scaffold: [docs/ops/install-docker.md](docs/ops/install-docker.md)

---

## Documentation

All canonical documentation lives under [docs/](docs/).

- Product: [overview](docs/product/overview.md), [release 1.0 readiness](docs/product/release-1.0.md), [roadmap](docs/product/roadmap.md)
- Architecture: [overview](docs/architecture/overview.md), [macro components](docs/architecture/macro-components.md), [horizontal engines](docs/architecture/horizontal-engines.md), [ADRs](docs/architecture/decisions/)
- Development: [onboarding](docs/development/onboarding.md), [backend](docs/development/backend.md), [frontend](docs/development/frontend.md), [contributing](docs/development/contributing.md), [code map](docs/development/code-map.md)
- Ops: [install non-Docker](docs/ops/install-non-docker.md), [install Docker](docs/ops/install-docker.md), [runbook](docs/ops/runbook.md), [backup/restore](docs/ops/backup-restore.md), [incident response](docs/ops/incident_response.md)
- Compliance: [secrets management](docs/compliance/secrets-management.md), [SBOM](docs/compliance/sbom.md), [ISO 27001 SoA](docs/compliance/soa-iso-27001.md), [GDPR register](docs/compliance/registro-trattamenti-gdpr.md)

Start from [docs/README.md](docs/README.md) for the full index.

---

## License and Contacts

Proprietary. Private repository.

Operations contact: [docs/ops/handoff.md](docs/ops/handoff.md)
Incident response: [docs/ops/incident_response.md](docs/ops/incident_response.md)
