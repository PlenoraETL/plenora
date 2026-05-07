# Backend Macro Components

This page is the high-level map of the backend. It complements
[horizontal-engines.md](horizontal-engines.md), which lists the active
engine boundaries and import rules.

---

## Component Map

```text
Plenora Backend

  A. Auth / Identity
     JWT, RBAC, MFA, disclosure policy, tenant isolation.
     Canonical: app/core/auth, app/shared/contracts/auth.py

  B. Connections / External Providers
     External DBs, REST APIs, ArcGIS, datasources.
     Canonical: app/core/features, app/core/datasources,
     app/shared/connections

  C. Execution / Orchestration
     Canvas graph parsing, execution results, pipeline orchestration,
     Celery worker/beat integration.
     Canonical: app/core/orchestrator, app/shared/execution, workers

  D. Transformation
     Manipola transforms, EstrAi query generation, Arricchisci enrichment
     execution, spatial operations.
     Canonical: app/core/manipola, app/core/estrai, app/core/arricchisci

  E. Resources / Storage
     Resource refs, artifact loading, bounded reads, object storage,
     storage reconciliation, result blobs.
     Canonical: app/core/resources, app/core/artifacts, app/shared/resources

  F. Persistence / DB
     PostgreSQL pool, migrations, config/result/log repositories,
     external DB query execution.
     Canonical: app/db

  G. Observability / Install
     Health/readiness, logs, backup/restore, install scripts, Docker scaffold.
     Canonical: app/observability, scripts, docs/ops
```

---

## External Systems

| External system | Backend classification | Notes |
|---|---|---|
| PostgreSQL | Persistence / DB | Catalog DB, runs, results, config, logs. |
| Redis | Runtime infrastructure | Celery broker/result backend, locks, blacklist. |
| S3 / MinIO | Resource / Storage | Object storage provider for artifacts and backups. |
| ArcGIS REST | Connections / External Providers | Read/write provider consumed by Features, Estrai, Carica, and IMDF. |
| User databases | Connections / External Providers | Queried through DB execution layer. |
| External REST APIs | Connections / External Providers | Used primarily by Arricchisci. |

---

## ArcGIS Boundary

ArcGIS is an external read/write provider, not an IMDF-owned vertical.

Current state:

- Shared provider contracts live in `app/shared/connections`.
- IMDF validation and publish paths consume a narrow adapter rather than the
  full Features service where possible.
- The remaining adapter still lives in `app/core/features` because DB lookup
  for ArcGIS connections has not yet moved to a neutral repository boundary.

Decision record:
[ADR-0023](decisions/0023-2026-05-06-arcgis-external-provider-boundary.md).

---

## Shared Package Shape

`app/shared` is being drained from a root-level utility drawer into engine
subpackages:

- `app/shared/execution`: graph utilities, canvas structural models,
  execution result contracts, typed payloads.
- `app/shared/resources`: resource metadata and loaded-resource envelopes.
- `app/shared/connections`: provider protocols and connection value objects.
- `app/shared/contracts`: cross-cutting public contracts.

Root-level shared modules are allowlisted and should shrink over time.

---

## Rules

- New code strengthens one macro component or one horizontal engine.
- Shared leaf packages must not import `app.core`, `app.db`, FastAPI,
  Pandas/GeoPandas, or storage/client-heavy runtime dependencies.
- Canvas structural models must not learn business node behavior.
- Cross-component dependencies must be typed and narrow.
