# Plenora-ETL

ETL platform per pipeline di dati geo-spaziali e tabulari.
Backend FastAPI + Celery + PostgreSQL + Redis. Frontend React + Vite +
ReactFlow per la composizione visuale di pipeline.

> **Status**: Phase 1 hardening + structural closure completata
> 2026-05-02. Production endpoint `lab.plenora.it`. FE e BE sono in
> zero-residuals operativo; i rimanenti FE item sono decision gate
> Phase 2 documentati. Vedi [docs/product/roadmap.md](docs/product/roadmap.md).

> **Brand note**: il package storico era `URBY-ETL`, oggi `Plenora-ETL`.
> Le env `PLENORA_*` e lo schema PG `plenora.*` sono il riferimento
> canonico attuale; gli alias `URBY_*` sono mantenuti come legacy
> fallback.

---

## Quickstart (dev locale)

```bash
# Backend
cd backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

export PLENORA_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DATABASE_URL="postgresql://plenora:plenora@localhost:5432/plenora"
# Una sola URL Redis canonica copre broker, result backend, leader lock,
# blacklist JWT. Niente REDIS_URL separata.
export CELERY_BROKER_URL="redis://localhost:6379/0"
export CELERY_RESULT_BACKEND="redis://localhost:6379/0"

uvicorn app.main:app --reload --port 5004
celery -A workers.celery_app worker --loglevel=info       # in altra shell
celery -A workers.celery_app beat --loglevel=info         # in altra shell

# Frontend (in altra shell)
cd frontend
npm install
npm run dev
```

Setup esteso + troubleshooting:
[docs/development/onboarding.md](docs/development/onboarding.md).
Clean install di un host nuovo (non-Docker, single-machine):
[docs/ops/install-non-docker.md](docs/ops/install-non-docker.md).
Docker (NON ancora supportato, target shape):
[docs/ops/install-docker.md](docs/ops/install-docker.md).

---

## Documentazione

Tutta la documentazione vive in [`docs/`](docs/).

### Per dominio

- **Product**: [overview](docs/product/overview.md) · [roadmap](docs/product/roadmap.md)
- **Architecture**: [overview](docs/architecture/overview.md) · [horizontal engines map](docs/architecture/horizontal-engines.md) · [decisions (ADR)](docs/architecture/decisions/) · [invariants](docs/architecture/invariants.md) · [inventory](docs/architecture/inventory_report.md)
- **Development**: [onboarding](docs/development/onboarding.md) · [backend](docs/development/backend.md) · [frontend](docs/development/frontend.md) · [contributing](docs/development/contributing.md) · [changelog](docs/development/changelog.md) · [code map](docs/development/code-map.md)
- **Ops**: [install non-Docker](docs/ops/install-non-docker.md) · [install Docker (status)](docs/ops/install-docker.md) · [runbook](docs/ops/runbook.md) · [deploy checklist](docs/ops/deploy-checklist.md) · [backup/restore](docs/ops/backup-restore.md) · [DR drill](docs/ops/dr_drill_playbook.md) · [incident response](docs/ops/incident_response.md) · [JWT incident](docs/ops/jwt-incident-response.md) · [runtime prereqs](docs/ops/runtime-prereqs.md)
- **Compliance**: [secrets management](docs/compliance/secrets-management.md) · [SBOM](docs/compliance/sbom.md) · [ISO 27001 SoA](docs/compliance/soa-iso-27001.md) · [GDPR](docs/compliance/registro-trattamenti-gdpr.md)

### Indice navigabile

[`docs/README.md`](docs/README.md) — indice completo delle aree e dei
file principali.

---

## License & contatti

Proprietary — Plenora Holding. Repo privato (non distribuito).

Operations contact: [`docs/ops/handoff.md`](docs/ops/handoff.md).
Incident response: [`docs/ops/incident_response.md`](docs/ops/incident_response.md).
