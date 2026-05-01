# URBY-ETL

ETL platform per pipeline di dati geo-spaziali e tabulari.
Backend FastAPI + Celery + PostgreSQL + Redis. Frontend React + Vite +
ReactFlow per la composizione visuale di pipeline.

> **Status**: Phase 1 hardening completata. Production endpoint
> `lab.plenora.it`. Code base in attivo refinement post-stabilization.
> Vedi [docs/ROADMAP.md](docs/ROADMAP.md) per residui aperti e Phase 2+.

> **Brand note**: il package storico era `PLENORA-ETL`, oggi `URBY-ETL`.
> Riferimenti `PLENORA_*` env var, `plenora.*` schema PG sono attuali.

---

## Current capabilities

Tutti i moduli sotto sono **LIVE** in produzione.

### Pipeline visuale (canvas-based)

| Modulo | Cosa fa |
|---|---|
| **Estrai** | Query SQL multi-dialect (PostgreSQL, MySQL, Oracle, MSSQL, SQLite) + ArcGIS Feature Service. Output a file (csv/json/xlsx/geojson/gpkg/shp/parquet). |
| **Manipola** | 33+ trasformazioni dati (filter, join, multi-join, pivot, aggregate, dedup, geo ops: clip / dissolve / overlay / sjoin / nearest, ecc.). |
| **Arricchisci** | Pipeline arricchimento via REST API esterne (auth: token / OAuth2 / public / custom), REST File processor, executable command-based. Parametri statici, mapping risposta JSON, parallelizzazione nodi indipendenti. |
| **Carica** | DB writer (replace atomic / append / upsert / Oracle redef) + ArcGIS Feature Service publish (replace / append / update / upsert). |
| **IMDF** | Apple Indoor Mapping Data Format: bundle generation + ArcGIS publish con renderers configurabili. |

### Operatività

| Componente | Cosa fa |
|---|---|
| **Scheduler** | Cron + Celery beat con leader election (Redis SETNX), heartbeat, catchup tracking, log archive. |
| **Executor pubblicati** | "Eseguibili" = pipeline serializzate eseguibili da non-admin (`esecutore` role kiosk-only) con upload temporaneo. |
| **Auth** | JWT HS256 (8h, no refresh), MFA TOTP opt-in, 5-role RBAC, group-based tenant isolation. |
| **Audit & observability** | Run/log persistence in PG, intermediate snapshot mode (`metadata_only` / `preview_only` / `full_pg` / `full_artifact`) con per-call override superadmin. |
| **Intermediate persistence** | Object storage backend (AWS S3 / MinIO) per Parquet artifact + manifest, opt-in via mode. |

---

## Repo structure

```
URBY-ETL/
├── README.md                    # questo file
├── CONTRIBUTING.md              # branch policy + PR rules
├── CHANGELOG.md                 # changelog raggruppato per tema
├── CODE_MAP.md                  # mappa puntuale dei moduli (high-density)
├── docs/                        # docs di prodotto cross-stack
│   ├── ONBOARDING.md            # setup nuovo dev
│   ├── ARCHITECTURE.md          # core platform + boundaries
│   └── ROADMAP.md               # residui aperti + Phase 2+
├── backend/                     # FastAPI app + Celery workers
│   ├── README.md                # setup BE + test policy + ADR refs
│   ├── app/                     # core/<modulo>/{router, executor, service, models}
│   ├── workers/                 # Celery tasks + beat scheduler
│   ├── tests2/                  # contract test suite (pytest)
│   ├── docs/
│   │   ├── architecture/decisions/  # ADR formali (Nygard)
│   │   ├── ops/                     # runbook + incident response
│   │   └── compliance/              # SBOM + GDPR + ISO 27001 SoA
│   ├── migrations/              # SQL migrations
│   └── scripts/                 # backup, restore, SBOM, secrets migration
├── frontend/                    # React 18 + Vite + ReactFlow
│   ├── README.md                # setup FE + folder structure
│   ├── src/                     # pages, components, hooks, store, api
│   └── tests/                   # vitest + RTL contract tests
├── deploy/                      # deploy profile (prod.conf)
├── deploy.sh                    # deploy script con dry-run + health gate
└── .github/workflows/           # CI: backend pytest + frontend vitest
```

---

## Quickstart dev

Setup minimo per far girare backend + frontend in locale. Vedi
[docs/ONBOARDING.md](docs/ONBOARDING.md) per la versione estesa con
troubleshooting.

### Prerequisiti

- Python 3.12, Node 20+, PostgreSQL 14+, Redis 7+
- Sistema: Linux/macOS/Windows (WSL2 raccomandato su Windows)

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# Env vars minime
export PLENORA_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DATABASE_URL="postgresql://dataflow:dataflow@localhost:5432/dataflow_test"
export REDIS_URL="redis://localhost:6379/0"

# Server + worker + beat (3 shell separate)
uvicorn app.main:app --reload --port 5004
celery -A workers.celery_app worker --loglevel=info
celery -A workers.celery_app beat --loglevel=info
```

Lista completa env vars: [`backend/docs/ops/runtime-prereqs.md`](backend/docs/ops/runtime-prereqs.md).

### Frontend

```bash
cd frontend
npm install
npm run dev              # vite dev server (default :5173)
npm run test             # vitest
npm run build            # production bundle in frontend/dist/
```

Vedi [`frontend/README.md`](frontend/README.md) per dettagli.

---

## Test policy locale

**Niente full suite locale per default.** GitHub CI fa la full
regression. Budget locale target 2-5 min, max 10 min.

| Tipo PR | Test locale richiesto |
|---|---|
| docs / compat / test-only | mirati sui file modificati |
| prod runtime / security core | mirati + max 1 wider di dominio |
| auth / scheduler / conftest globale | estendere con giudizio |

Vedi [CONTRIBUTING.md](CONTRIBUTING.md) per dettagli + commit format +
PR template.

---

## Documentazione

### Docs cross-stack

- [docs/ONBOARDING.md](docs/ONBOARDING.md) — setup nuovo dev (BE + FE + DB + Redis).
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — core platform, boundaries BE/FE, execution flow, persistence layer, auth model.
- [docs/ROADMAP.md](docs/ROADMAP.md) — residui audit aperti, Phase 2/3/4+, productization gaps.

### Backend specifici

- [backend/README.md](backend/README.md) — setup dev BE + test policy + compatibility table + ADR/runbook refs.
- [backend/docs/architecture/decisions/](backend/docs/architecture/decisions/) — ADR registry (formato Nygard, append-only).
- [backend/docs/ops/](backend/docs/ops/) — runbook recovery + incident response + deploy checklist + DR drill + JWT incident playbook.
- [backend/docs/compliance/](backend/docs/compliance/) — SBOM + GDPR registro + ISO 27001 SoA + secrets management.

### Frontend specifici

- [frontend/README.md](frontend/README.md) — setup FE + folder structure + API client conventions + test patterns.

### Storia decisionale

- [CHANGELOG.md](CHANGELOG.md) — PR raggruppate per tema (Phase 1 hardening, R-25 intermediate persistence, R-04 test reliability, ecc.).
- [CODE_MAP.md](CODE_MAP.md) — mappa puntuale moduli (~71KB, riferimento high-density per chi naviga il codice).

---

## License & contatti

Proprietary — Plenora Holding. Repo privato (non distribuito).

Operations contact: vedi [`backend/docs/ops/handoff.md`](backend/docs/ops/handoff.md).
Incident response: vedi [`backend/docs/ops/incident_response.md`](backend/docs/ops/incident_response.md).
