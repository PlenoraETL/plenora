# Horizontal Engines

This is the active backend boundary map. It is intentionally shorter than
the historical inventory documents and should be kept current as the codebase
is horizontalized.

---

## 1. Auth / Security Engine

Canonical surfaces:

- `app/core/auth/middleware.py`
- `app/core/auth/dependencies.py`
- `app/shared/contracts/auth.py`
- `app/shared/jwt_token.py`
- `app/shared/password_hashing.py`

Rules:

- Do not add new imports from the retired auth facade.
- Disclosure policy and auth error codes are governed by ADR-0022.
- Tenant/group binding must remain explicit at cross-tenant boundaries.

---

## 2. Connections Engine

Canonical surfaces:

- `app/shared/connections/*`
- `app/core/features/arcgis_client.py`
- `app/core/connections/arcgis_connection_resolver.py`
- `app/core/datasources/*`
- `app/db/query_executor.py`

Current ArcGIS shape:

- `ArcGISConnectionInfo` lives in `app/shared/connections/arcgis_connection.py`.
- `ArcGISProviderProtocol` lives in `app/shared/connections/arcgis_provider.py`.
- IMDF uses the neutral connection resolver instead of depending directly on
  `FeaturesService` or a Features-owned adapter.

Rules:

- ArcGIS is an external read/write provider, not an IMDF subdomain.
- Shared connection contracts must not import concrete clients or DB code.
- New provider semantics belong in `connections`, not in canvas node models.

---

## 3. Execution Engine

Canonical surfaces:

- `app/shared/execution/canvas_node.py`
- `app/shared/execution/canvas_graph.py`
- `app/shared/execution/graph.py`
- `app/shared/execution/executor_protocol.py`
- `app/shared/execution/payloads.py`
- `app/core/orchestrator/*`

Current contracts:

- `CanvasNode` and `CanvasGraph` are structural-only wrappers around canvas
  input. They may expose `id`, `type`, `data`, `get_field`, raw lookup,
  edges, and minimal result lookup.
- `kahn_topological_order` and `GraphCycleError` live in
  `app/shared/execution/graph.py`.
- `ExecutionResult` is the typed result boundary.
- `ArtifactPayload` lives in `app/shared/execution/payloads.py`.

Rules:

- Do not add node-type business behavior to `CanvasNode` or `CanvasGraph`.
- Do not put DataFrames, bytes, or streams in `ExecutionResult.payload`.
- Do not create a generic `BaseGraphExecutor` until duplication proves it is
  needed.

---

## 4. Transformation Engine

Canonical surfaces:

- `app/core/manipola/*`
- `app/core/estrai/*`
- `app/core/arricchisci/*`
- transformation-specific tests under `backend/tests2`

Current direction:

- Manipola and Arricchisci are being moved toward shared structural graph
  intake without merging their business semantics.
- Shared metadata should travel with loaded resources instead of being
  reconstructed repeatedly from canvas dictionaries.

Rules:

- Keep transformation behavior in the owning engine.
- Use shared structural contracts only for structure, not business execution.
- Avoid reintroducing global mutable execution context.

---

## 5. Resource / Storage Engine

Canonical surfaces:

- `app/core/resources/*`
- `app/core/artifacts/*`
- `app/shared/resources/metadata.py`
- `app/shared/cloud_backends.py`
- `app/shared/storage_reconciliation.py`

Current contracts:

- Resource loading is bounded by explicit byte limits.
- `LoadedResource` pairs loaded data with `ResourceMetadata`.
- Object storage is governed by `STORAGE_POLICY`.

Rules:

- No unbounded `b"".join(stream)` over external or object-storage streams.
- Path materialization is fallback behavior and must be inventoried.
- Resource loaders must not become vertical domain services.

---

## 6. Persistence / DB Engine

Canonical surfaces:

- `app/db/pg_connection.py`
- `app/db/config/*`
- `app/db/results_db_service.py`
- `app/db/query_executor.py`

Rules:

- Prefer direct domain repositories over broad facade imports.
- Do not re-expand retired DB facades.
- Keep SQL and connection handling out of shared leaf contracts.

---

## 7. Observability / Install Engine

Canonical surfaces:

- `app/observability/health.py`
- `scripts/install.sh`
- `scripts/docker-validate.sh`
- `backend/Dockerfile`
- `docker-compose.yml`
- `docs/ops/*`

Current state:

- Non-Docker single-machine installation is the most detailed runbook.
- Docker scaffold exists for backend, worker, beat, Postgres, and Redis.
- Frontend Docker image is still a known gap until explicitly added and
  validated.
- First-PC Linux validation is still required before calling the install path
  stable.

Rules:

- Prefer automated checks over long manual checklists.
- Keep `/health` cheap and `/ready` bounded.
- Do not claim Docker production readiness until a real Linux-PC validation
  report exists.

---

## Canonical References

- [Macro components](macro-components.md)
- [Architecture overview](overview.md)
- [Invariants](invariants.md)
- [ADR registry](decisions/README.md)
- [Clean install, non-Docker](../ops/install-non-docker.md)
- [Clean install, Docker](../ops/install-docker.md)
