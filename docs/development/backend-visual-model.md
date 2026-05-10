# Backend Visual Model

This page is the compact working model for the Plenora backend. It describes
the current runtime boundaries and the dependency direction expected for
release 1.0 work.

## Runtime Topology

```text
Browser UI
  |
  | REST + JWT
  v
FastAPI app
  |
  +-- auth and request middleware
  +-- domain routers
  +-- orchestration services
  +-- shared contracts
  |
  +--> PostgreSQL
  +--> Redis
  +--> object storage
  +--> external databases / REST APIs / ArcGIS-like providers

Celery worker
  |
  +-- executes project graphs
  +-- resolves resources and artifacts
  +-- writes run state and results

Celery beat
  |
  +-- scheduled execution
  +-- retention and operational maintenance
```

## Component Boundaries

| Area | Owns | Must not own |
|---|---|---|
| Auth / Identity | JWT, MFA, RBAC, request identity, tenant checks | Business execution rules |
| Connections | External DB/API/ArcGIS provider contracts and clients | IMDF or canvas-specific behavior |
| Execution | Canvas structure, graph order, orchestration, typed results | DataFrame, SQL, HTTP, or ArcGIS domain semantics |
| Transformation | EstrAi, Manipola, Arricchisci domain behavior | Shared graph primitives or tenant bypasses |
| Resources / Storage | Bounded loading, artifacts, object storage, result blobs | Unbounded payload transport |
| Persistence / DB | PostgreSQL pool, repositories, migrations, query execution | Concrete frontend or canvas behavior |
| Observability / Install | Health, readiness, logs, backup/restore, install checks | Runtime business decisions |

See [macro-components.md](../architecture/macro-components.md) and
[horizontal-engines.md](../architecture/horizontal-engines.md) for the
canonical maps.

## Dependency Direction

Allowed direction:

```text
routers
  -> domain services
  -> shared contracts / DB repositories / storage factories
  -> infrastructure clients
```

Rules:

- Routers validate HTTP concerns and delegate.
- Domain services own business behavior.
- Shared packages expose contracts and small leaf utilities.
- DB access goes through `app.db` or focused repositories.
- Storage access goes through `app.shared.cloud_backends` and resource
  loaders.
- Frontend contracts are served by API models and response shapes, not by
  importing frontend assumptions into backend code.

## Shared Package Shape

`app.shared` is restricted to leaf contracts and utilities:

- `app.shared.execution`: canvas structure, graph utilities, executor
  protocol, typed result payloads.
- `app.shared.resources`: resource metadata and loaded-resource envelopes.
- `app.shared.connections`: provider protocols and connection value objects.
- `app.shared.contracts`: cross-cutting public contracts.

Do not add new broad root-level shared modules unless the boundary is
explicitly documented and tested.

## Execution Flow

```text
Project canvas
  -> CanvasGraph / CanvasNode structural parsing
  -> OrchestratorService
  -> domain executor
  -> resource loaders / intermediate store
  -> ExecutionResult
  -> result catalog + artifact references
```

Execution contract:

- Parse canvas dictionaries at the boundary when practical.
- Keep node-type behavior inside the owning domain executor.
- Use `ExecutionResult` as the typed boundary.
- Never place DataFrames, raw bytes, streams, or file handles in generic
  result payloads.
- Move large data through references, previews, caps, and artifact storage.

## Resource And Result Flow

```text
source reference
  -> tenant/project check
  -> bounded loader
  -> LoadedResource + ResourceMetadata
  -> domain processing
  -> artifact/result reference
```

Rules:

- Every resource read has an explicit tenant/project scope.
- Every external or object-storage read is bounded.
- Temporary file materialization is a fallback and must stay visible.
- Result blobs follow `STORAGE_POLICY`.
- In `object_storage` policy, new result payloads are written to object
  storage; PostgreSQL stores metadata and references.

## External Providers

External systems are provider boundaries:

- User databases are accessed through connection/query layers.
- REST APIs are accessed through Arricchisci connection services.
- ArcGIS-like systems are Connections providers used by Features, Estrai,
  Carica, and IMDF through narrow contracts.
- IMDF does not own ArcGIS provider behavior.

## Review Checklist

Before changing backend architecture, check:

- Does the change preserve tenant/group isolation?
- Does it keep large payloads out of generic dictionaries and API responses?
- Does it use an existing engine boundary instead of adding a broad facade?
- Does it avoid direct infrastructure clients in business modules?
- Does it keep frontend/backend contracts explicit?
- Do architecture docs and ADRs still match the runtime behavior?

## References

- [Architecture Overview](../architecture/overview.md)
- [Horizontal Engines](../architecture/horizontal-engines.md)
- [Macro Components](../architecture/macro-components.md)
- [Execution Engine Seam](../architecture/execution-engine-seam.md)
- [Architecture Invariants](../architecture/invariants.md)
