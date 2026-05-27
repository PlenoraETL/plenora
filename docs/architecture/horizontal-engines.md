# Horizontal Engines

BE-GO-READY-DOCS-001-CODEMAP-ARCHITECTURE-REFRESH.

This is the active backend boundary map. Keep it current when code moves
between engines.

---

## 1. Auth / Security Engine

Canonical surfaces:

- `app/core/auth/middleware.py`
- `app/core/auth/dependencies.py`
- `app/core/security/*`
- `app/shared/contracts/auth.py`

Rules:

- Do not add new imports from the retired auth facade.
- Runtime security helpers belong in `app/core/security`; `app/shared/*`
  security paths are compatibility aliases only.
- Disclosure policy and auth error codes are governed by ADR-0022.
- Tenant/group binding must remain explicit at cross-tenant boundaries.

---

## 2. Connections Engine

Canonical surfaces:

- `app/shared/connections/*`
- `app/core/connections/rest_sink.py`
- `app/core/connections/arcgis_sql_helpers.py`
- `app/core/connections/arcgis_publish_modes.py`
- `app/core/features/arcgis_client.py`
- `app/core/connections/arcgis_connection_resolver.py`
- `app/core/datasources/*`
- `app/db/query_executor.py`

Current ArcGIS shape:

- `ArcGISConnectionInfo` lives in `app/shared/connections/arcgis_connection.py`.
- `ArcGISProviderProtocol` lives in `app/shared/connections/arcgis_provider.py`.
- IMDF uses the neutral connection resolver instead of depending directly on
  `FeaturesService` or a Features-owned adapter.
- The ADR-0023 IMDF residual allowlist is empty: IMDF ArcGIS policy wrappers
  route generic discovery and lifecycle helper calls through
  `app.core.connections`, not `app.core.features`.
- Generic ArcGIS SQL and existing-service publish-mode helpers live under
  `app/core/connections`; legacy `features` wrapper modules are tombstoned.
- ArcGIS write mapping and writer protocols live under `app/core/connections`;
  legacy `app/core/features/arcgis_write_mapping.py` and
  `app/core/features/arcgis_writer_boundary.py` are tombstoned.

Rules:

- ArcGIS is an external read/write provider, not an IMDF subdomain.
- Shared connection contracts must not import concrete clients or DB code.
- New provider semantics belong in `connections`, not in canvas node models.
- Do not add new IMDF imports from `app.core.features`; the boundary guard has
  no remaining ADR-0023 IMDF exceptions.

---

## 3. Execution Engine

Canonical surfaces:

- `app/shared/execution/canvas_node.py`
- `app/shared/execution/canvas_graph.py`
- `app/shared/execution/canvas_types.py`
- `app/shared/execution/clock.py`
- `app/shared/execution/graph.py`
- `app/shared/execution/executor_protocol.py`
- `app/shared/execution/payloads.py`
- `app/shared/execution/schema_types.py`
- `app/core/orchestrator/*`
- `app/core/task_runtime/*`
- `app/core/task_runtime/probes.py`
- `app/core/scheduler/job_dispatcher.py`
- `app/core/scheduler/run_log_store.py`
- `app/core/imdf/publish_dispatcher.py`

Current contracts:

- `CanvasNode` and `CanvasGraph` are structural-only wrappers around canvas
  input. They may expose `id`, `type`, `data`, `get_field`, raw lookup,
  edges, and minimal result lookup.
- `kahn_topological_order` and `GraphCycleError` live in
  `app/shared/execution/graph.py`.
- `ExecutionResult` is the typed result boundary.
- `ArtifactPayload` lives in `app/shared/execution/payloads.py`.
- `ColumnSchema` is the shared schema contract for inferred canvas columns.
- `app/core/scheduler/job_dispatcher.py` is the scheduled-run dispatch
  boundary; scheduler application code should not call Celery tasks directly.
- `app/core/task_runtime/*` is the shared task-runtime adapter surface;
  app services depend on its ports instead of importing Celery,
  `workers.celery_app`, or concrete `workers.tasks.*` modules directly.
- `app/core/orchestrator/task_runtime.py` is a legacy pre-shared-runtime
  location and must not keep or regain concrete Celery/worker imports.
- `app/core/scheduler/run_log_store.py` is the scheduled-run DB log reader
  boundary; catalog/view code should not import scheduler DB directly.
- `app/core/imdf/publish_dispatcher.py` is the IMDF publish dispatch boundary;
  IMDF routers should not call Celery tasks directly.

Rules:

- Do not add node-type business behavior to `CanvasNode` or `CanvasGraph`.
- Do not put DataFrames, bytes, or streams in `ExecutionResult.payload`.
- Do not create a generic `BaseGraphExecutor` until duplication proves it is
  needed.
- Do not call `workers.tasks.schedule_task.schedule_pipeline_task.apply_async`
  from scheduler application code; use the dispatch boundary.
- Do not import Celery, `workers.celery_app`, or concrete `workers.tasks.*`
  modules from application services; use `app.core.task_runtime`.
- Do not import scheduler DB directly from scheduler catalog/view code; use
  `app.core.scheduler.run_log_store`.
- Do not call `workers.tasks.imdf_publish_task.execute_imdf_publish_task`
  from IMDF application code; use the dispatch boundary.

---

## 4. Transformation Engine

Canonical surfaces:

- `app/core/dataframes/*`
- `app/core/manipola/*`
- `app/core/estrai/*`
- `app/core/arricchisci/*`
- transformation-specific tests under `backend/tests2`

Current direction:

- Manipola and Arricchisci are being moved toward shared structural graph
  intake without merging their business semantics.
- Shared metadata should travel with loaded resources instead of being
  reconstructed repeatedly from canvas dictionaries.
- Pandas/geopandas-backed tabular and geospatial mechanics are confined to
  `app/core/dataframes/*`. Vertical engines must express business intent and
  call dataframe ports/helpers instead of importing pandas/geopandas directly.
  Vertical code may pass frame-like values across boundaries, but it must not
  own pandas/geopandas imports or implementation-specific mechanics.
- Pandas-neutral dataframe contracts live in `app/core/dataframes/ports.py`;
  this module must not import pandas/geopandas or vertical runtime services.
- File-source parser dispatch lives in `app/core/dataframes/load_dispatch.py`;
  the old `app/core/files/load_dispatch.py` wrapper is tombstoned.
- The default file-source loader that owns pandas/geopandas parsing lives in
  `app/core/dataframes/default_loader.py`; `app/core/files/service_deps.py`
  re-exports it for legacy service DI.
- DataFrame path/bytes I/O lives in `app/core/dataframes/frame_io.py` and
  geospatial export helpers live in `app/core/dataframes/geospatial_io.py`;
  matching `app/core/files/*_io.py` wrappers are tombstoned.
- DataFrame-backed column type inference and empty typed-frame construction live in
  `app/core/dataframes/schema.py`; pandas-specific helpers are not exposed from
  `app/core/resources/column_types.py`. `app/core/files/helpers.py` re-exports
  the legacy helper names while upload code migrates.
- Scalar null checks, DataFrame probes, and GeoSeries-like probes that depend
  on pandas/geopandas live in
  `app/core/dataframes/values.py`; vertical domains should use those helpers
  instead of importing pandas/geopandas only for scalar/type checks.
- Conditional split and parallel column merge helpers that depend on dataframe
  engine index/cardinality mechanics live in `app/core/dataframes/parallel_ops.py`;
  vertical orchestration should use them instead of owning pandas merge logic.
- DataFrame-to-record projection for generic REST payloads, tabular coercion,
  dtype probes, empty/false Series construction, concat/merge, JSON normalize,
  binning, and string-join helpers live in `app/core/dataframes/tabular.py`;
  vertical domains should keep transform intent but not import pandas only for
  these mechanics.
- Row iteration and row-drop/reset helpers live in
  `app/core/dataframes/tabular.py`; SQL writers should use those helpers
  instead of owning pandas row traversal or index-reset mechanics.
- Geodataframe helpers live in `app/core/dataframes/geodata.py`, including
  GeoDataFrame construction/probes and overlay, clip, and spatial join
  wrappers backed by the dataframe engine; legacy `features/geo_utils.py` is
  tombstoned.

Rules:

- Keep transformation behavior in the owning engine.
- Use shared structural contracts only for structure, not business execution.
- Avoid reintroducing global mutable execution context.
- Do not add direct pandas/geopandas imports in vertical domains. Route
  parser dispatch, constructors, probes, joins, serialization, hashing, and
  geospatial mechanics through `app.core.dataframes.*`.
- Do not put pandas/geopandas type imports in dataframe contracts; use
  `app.core.dataframes.ports` for pandas-neutral engine protocols.
- Do not add new concrete pandas/geopandas file-source loader behavior in
  `app.core.files`; use `app.core.dataframes.default_loader`.
- Do not add new direct dataframe path/bytes I/O in vertical domains; use
  `app.core.dataframes.frame_io` / `app.core.dataframes.geospatial_io` or the
  current compatibility wrappers.
- Do not add new pandas-backed column inference in `app.core.files`; use
  `app.core.dataframes.schema`.
- Do not import pandas/geopandas directly from `app.core.files`; route any
  frame behavior through `app.core.dataframes`.
- Do not import pandas/geopandas in vertical domains only for scalar null
  checks, DataFrame type probes, or GeoSeries-like probes; use
  `app.core.dataframes.values`.
- Do not duplicate pandas split/merge/index guard behavior in vertical
  orchestration; use `app.core.dataframes.parallel_ops`.
- Do not duplicate pandas record extraction for REST payloads in vertical
  write boundaries; use `app.core.dataframes.tabular`.
- Do not duplicate pandas row traversal or row-drop/reset mechanics in SQL
  writers; use `app.core.dataframes.tabular`.
- Do not import pandas/geopandas in vertical transform modules only for
  dataframe constructors, dtype probes, scalar missing values, concat/merge,
  JSON normalization, binning, or GeoDataFrame construction/overlay/clip/join;
  use `app.core.dataframes.tabular` or `app.core.dataframes.geodata`.
- Output-contract dtype drift checks consume
  `app.core.dataframes.schema.dtype_compatible_with_logical`; output contracts
  must not import pandas/geopandas directly.
- The direct pandas/geopandas vertical allowlist is empty. Any new engine
  import outside `app.core.dataframes` is architectural drift.

---

## 5. Resource / Storage Engine

Canonical surfaces:

- `app/core/resources/*`
- `app/core/resources/stores/*`
- `app/core/artifacts/*`
- `app/core/results/output_file_cleanup.py`
- `app/core/files/cleanup.py`
- `app/core/scheduler/run_archive_store.py`
- `app/core/eseguibili/output_store.py`
- `app/core/imdf/bundle_store.py`
- `app/core/intermediate/retention.py`
- `app/core/intermediate/runs_db.py`
- `app/core/intermediate/node_snapshots_db.py`
- `app/core/intermediate/run_lifecycle.py`
- `app/shared/resources/metadata.py`
- `app/shared/resources/file_cleanup.py`
- `app/infrastructure/storage/cloud_backends.py`
- `app/core/resources/storage_reconciliation.py`

Current contracts:

- Resource loading is bounded by explicit byte limits.
- `LoadedResource` pairs loaded data with `ResourceMetadata`.
- Object storage is governed by `STORAGE_POLICY`.
- Runtime intermediate callers use explicit leaf boundaries
  (`runs_db`, `node_snapshots_db`, `retention`, `run_lifecycle`), not the
  historical `app/core/intermediate/db.py` facade, which is tombstoned.
- DataFrame pipeline result persistence returns `PipelineResultSave` through
  `app/core/results/pipeline_result.py`; callers should not use legacy dict
  result payload wrappers.
- Result retention is applied through `app/core/results/retention.py`; result
  writers should not restore writer-local retention compatibility aliases.
- Result object-storage writes record `upload_intent` before upload and
  close it only after the `results` row is saved or after confirmed
  compensating delete.
- Project result output-file cleanup should go through
  `app/core/results/output_file_cleanup.py`.
- Scheduled-run filesystem archive lookup should go through
  `app/core/scheduler/run_archive_store.py`; the catalog should merge store
  outputs, not encode archive layout itself.
- Group cleanup for `file_sources` must use the storage-aware file-source
  cleanup boundary, not a direct group-row delete.
- Eseguibili output download, ownership metadata, and task-scoped cleanup
  should go through `app/core/eseguibili/output_store.py`; routers should not
  inspect raw `eseguibili-outputs/{task_id}/` keys directly.
- IMDF bundle path validation, cache hydration, metadata sidecar reads,
  publish GDB lookup, task listing, and deletion should go through the
  bundle lifecycle boundary.
- Intermediate run cleanup includes completed artifacts and failed artifacts
  that already have an `artifact_uri`; pending artifacts are not cleanup
  eligible.
- Beat janitors should remove filesystem paths through
  `app/shared/resources/file_cleanup.py`.

Rules:

- No unbounded `b"".join(stream)` over external or object-storage streams.
- Path materialization is fallback behavior and must be inventoried.
- Resource loaders must not become vertical domain services.
- Resource loader DB access lives in `app/core/resources/stores/*`; old
  `app/core/resources/loaders/*_store.py` wrappers are tombstoned.
- File-source blob storage dispatch lives in
  `app/core/resources/blob_dispatch.py`; the old
  `app/core/files/storage_dispatch.py` wrapper is tombstoned.
- Do not bypass file-source refcount/reconciliation cleanup with direct
  `DELETE FROM file_sources WHERE group_id`.
- Do not inspect or delete legacy `RESULTS_DIR/{project_id}` files directly
  from API routers; use the legacy result file-store boundary.
- Do not encode scheduled-run archive path/listing/lookup in catalog or router
  code; use `app.core.scheduler.run_archive_store`.
- Do not stream eseguibili outputs by calling raw storage key lookup directly
  from routers; use the output-store boundary so owner sidecars are filtered
  and validated consistently.
- Do not resolve, hydrate, inspect metadata, scan for publish GDBs, or delete
  IMDF bundle directories directly from routers/workers/validators.

---

## 6. Persistence / DB Engine

Canonical surfaces:

- `app/db/pg_connection.py`
- `app/db/config/*`
- `app/db/results_db_service.py`
- `app/db/query_executor.py`
- Domain DB boundaries: `app/core/**/db.py`, `app/core/**/*_store.py`,
  and `app/core/**/*_repository.py`

Rules:

- Prefer direct domain repositories over broad facade imports.
- Do not re-expand retired DB facades.
- Keep SQL and connection handling out of shared leaf contracts.
- Routers and vertical services should not import `app.db.pg_connection`
  directly; route reads/writes through the local domain DB boundary.
- `app/core/task_runtime/*` is the only app-core surface allowed to import
  Celery, `workers.celery_app`, or concrete `workers.tasks.*` modules.

---

## 7. Observability / Install Engine

Canonical surfaces:

- `app/observability/health.py`
- `app/core/task_runtime/probes.py`
- `scripts/install.sh`
- `scripts/docker-validate.sh`
- `backend/Dockerfile`
- `docker-compose.yml`
- `docs/ops/*`

Current state:

- Non-Docker single-machine installation is the most detailed runbook.
- Docker scaffold exists for backend, worker, beat, Postgres, Redis, and the
  frontend nginx image.
- The Docker path still requires real-host acceptance before being treated as
  a release operations path.
- Linux validation is required before calling the install path stable.

Rules:

- Prefer automated checks over long manual checklists.
- Keep `/health` cheap and `/ready` bounded.
- Admin status code should probe Celery through
  `app/core/task_runtime/probes.py`, not direct worker imports.
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

---

## Do not import

- Do not add new imports from the retired `app.core.auth.auth` facade.
- Do not re-expand retired DB facades such as `app.db.config_db_service`.
- Do not read external or object-storage streams with unbounded stream
  `b"".join(...)`; use `read_stream_bounded` at the resource boundary.
- Do not add new `mode="path"` resource callsites without an inventory entry.
- Do not import provider-neutral REST sink behavior from
  `app.core.features.rest_sink` in production code; use
  `app.core.connections.rest_sink`.
- Do not import generic ArcGIS SQL or publish-mode behavior from
  `app.core.features.arcgis_sql_helpers` or
  `app.core.features.arcgis_publish_modes` in production code; use
  `app.core.connections`.
- Do not add direct pandas/geopandas imports in vertical domains; use
  `app.core.dataframes.*` ports/helpers for parser dispatch, constructors,
  probes, joins, serialization, hashing, and geospatial mechanics.
- Do not put pandas/geopandas type imports in dataframe contracts; use
  `app.core.dataframes.ports` for pandas-neutral engine protocols.
- Do not add new concrete pandas/geopandas file-source loader behavior in
  `app.core.files`; use `app.core.dataframes.default_loader`.
- Do not add new direct dataframe path/bytes I/O in vertical domains; use
  `app.core.dataframes.frame_io` / `app.core.dataframes.geospatial_io` or the
  current compatibility wrappers.
- Do not add new pandas-backed column inference in `app.core.files`; use
  `app.core.dataframes.schema`.
- Do not import pandas/geopandas directly from `app.core.files`; route any
  frame behavior through `app.core.dataframes`.
- Do not import pandas/geopandas in vertical domains only for scalar null
  checks, DataFrame type probes, or GeoSeries-like probes; use
  `app.core.dataframes.values`.
- Do not duplicate pandas split/merge/index guard behavior in vertical
  orchestration; use `app.core.dataframes.parallel_ops`.
- Do not enqueue scheduled runs directly through
  `workers.tasks.schedule_task`; use `app.core.scheduler.job_dispatcher`.
- Do not import Celery, `workers.celery_app`, or concrete `workers.tasks.*`
  modules from application services; use `app.core.task_runtime`.
- Do not import scheduler DB directly from scheduler catalog/view code; use
  `app.core.scheduler.run_log_store`.
- Do not enqueue IMDF publish tasks directly through
  `workers.tasks.imdf_publish_task`; use `app.core.imdf.publish_dispatcher`.
- Do not inspect Celery workers directly from admin service code; use
  `app.core.task_runtime.probes`.
- Do not remove file-source group rows through the generic DB cleanup helper;
  use `app.core.files.cleanup`.
- Do not delete project result output files directly from API routers; use
  `app.core.results.output_file_cleanup`.
- Do not encode scheduled-run archive path/listing/lookup in catalog or router
  code; use `app.core.scheduler.run_archive_store`.
- Do not lookup or stream raw eseguibili output keys from API routers; use
  `app.core.eseguibili.output_store`.
- Do not resolve, hydrate, inspect metadata, scan for publish GDBs, or delete
  IMDF bundles directly from router/worker/validator code; use
  `app.core.imdf.bundle_store`.
