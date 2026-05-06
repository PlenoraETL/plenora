# Execution Engine Seam

`BE-GO-READY-ARCH-055-W2-EXECUTION-ENGINE-DUPLICATION-SEAM`.

Migration map toward a shared canvas execution engine. Produced in
Batch 055 Worker 2 (2026-05-06).

Companion to [`horizontal-engines.md`](horizontal-engines.md) (engine
boundary index) and [`macro-components.md`](macro-components.md).

---

## 1. Current vertical responsibilities

### Manipola (`app.core.manipola.executor.ManipolaExecutor`)

Responsible for the **dataframe transformation pipeline**:

1. Security context initialisation: DB lookup → `group_id` (explicit,
   no singleton).
2. Source loading (3 chains, all ctx-strict post TE-007/008):
   - `project_source` — cross-tenant check via `ArtifactResolver`,
     recursive upstream re-execution via `orchestrator_callback`.
   - `file_source` — via `SourceLoader` (extracted to
     `manipola/source_loading.py`).
   - `staticValues` — pure in-memory template expansion.
3. Topological sort of transform/multi_join nodes → shared
   `app.shared.graph.kahn_topological_order` (on_cycle=`'raise'`).
4. Node execution loop: sequential, linear through sorted nodes.
5. Multi-join with pre-merge OOM guard (`MemoryLimitExceededError`).
6. Intermediate checkpoint snapshots (`record_checkpoint_snapshot` +
   `record_final_snapshot`).
7. GROUP BY aggregation on result node.
8. Result persistence via `save_pipeline_result`.
9. Columns metadata sync via `save_columns_metadata_with_retry`.
10. Step tracking via `append_step`.

### Arricchisci (`app.core.arricchisci.executor.EnrichExecutor` +
`canvas_orchestrator.execute_canvas_enrichment` +
`api_connector.ApiConnectionService` mixins)

Responsible for the **row-by-row HTTP enrichment pipeline**:

1. Security context initialisation: DB lookup → typed frozen
   `ArricchisciExecutionContext` (project_id, group_id, preview_limit).
2. Source loading (3 chains, all ctx-strict post TE-019/030B):
   - `project_source` — cross-tenant check via `SourceLoader`,
     recursive upstream re-execution via standalone `OrchestratorService`.
   - `file_source` — via `SourceLoader` (mixin
     `ApiConnectionSourceLoadingMixin`).
   - `staticValues` — pure in-memory template expansion.
3. Topological sort of canvas nodes → shared
   `app.shared.graph.kahn_topological_order` (on_cycle=`'truncate'`).
4. Node execution: parallel group computation (`compute_parallel_groups`)
   + sequential/parallel dispatch per step type.
5. Per-row HTTP enrichment with retry, rate limiting, deduplication.
6. Intermediate checkpoint snapshots (`_write_intermediate_snapshot` →
   `record_checkpoint_snapshot`).
7. File-mode result path (`save_result_blob`) + DataFrame result path
   (`save_pipeline_result`).
8. Columns metadata sync via `save_columns_metadata_with_retry`.
9. Step tracking via `append_step`.

---

## 2. Duplication map

| Capability | Manipola | Arricchisci | Status |
|---|---|---|---|
| Topological sort (Kahn) | `kahn_topological_order` | `kahn_topological_order` | ✅ Already shared via `app.shared.graph` |
| Source loading — `file_source` | `load_file_sources` (extracted) | `_load_file_source` mixin | ⚠️ Same logic, separate impls |
| Source loading — `staticValues` | `_carica_sorgenti_statiche` | `_load_static_values` mixin | ⚠️ Same logic, separate impls |
| Source loading — `project_source` | `_carica_sorgenti_progetto` | `_load_source_project` mixin | ⚠️ Same ArtifactResolver path, different recompute orchestration |
| Execution context (group_id) | `_init_security_context` → `str` | `build_execution_context` → `ArricchisciExecutionContext` | ⚠️ Identical DB lookup, different typed output |
| Intermediate snapshots | `record_checkpoint_snapshot` + `record_final_snapshot` | `_write_intermediate_snapshot` → `record_checkpoint_snapshot` | ✅ Shared via `app.core.intermediate.snapshot` |
| Result saving — DataFrame | `save_pipeline_result` | `save_pipeline_result` | ✅ Same function |
| Result saving — file | not applicable | `save_result_blob` | KEEP_CUSTOM (file-mode enrichment) |
| Columns metadata sync | `save_columns_metadata_with_retry` | `save_columns_metadata_with_retry` | ✅ Shared via `app.shared.columns_metadata` |
| Step tracking | `append_step` | `append_step` | ✅ Shared via `app.utils.logger` |
| Memory caps | `MemoryLimitExceededError` (multi-join + cross-join) | None | KEEP_CUSTOM (different threat model: row-by-row HTTP) |
| Parallel execution | None (linear) | `compute_parallel_groups` + thread pool | KEEP_CUSTOM (enricher-specific) |
| Node accessor pattern | `_nf` (Pydantic node attrs) | `_nd` (dict node) | ⚠️ `app.shared.canvas_node.CanvasNode` exists but not yet widely adopted |
| Error return shape | `{"success": False, "message": str}` | `{"success": False, "error": str, "user_error": bool}` | ⚠️ Divergent; unification is a next-step item |
| Executor protocol (execute/preview) | `ManipolaExecutor` | `EnrichExecutor` | ✅ **First seam added** — `app.shared.executor_protocol.CanvasExecutorProtocol` |

---

## 3. What is already shared

- `app.shared.graph.kahn_topological_order` — topo sort (Batch 053).
- `app.shared.exceptions.*` — typed error hierarchy.
- `app.shared.columns_metadata.save_columns_metadata_with_retry` — columns sync.
- `app.shared.canvas_node.CanvasNode` — typed node accessor (added W3, Batch 055).
- `app.core.intermediate.snapshot` — checkpoint + final snapshot.
- `app.core.artifacts.SourceLoader` — cross-tenant file/project loading.
- `app.utils.result_store.save_pipeline_result` — DataFrame → blob.
- `app.utils.logger.append_step` — step tracking.
- **`app.shared.executor_protocol.CanvasExecutorProtocol`** — ← **this batch**.

---

## 4. Target horizontal execution engine responsibilities

The target `TRANSFORMATION_ENGINE` boundary (per `BE_RULES.md`) owns:

- **Execution context**: one typed immutable object per pipeline run
  carrying `project_id`, `group_id`, `preview_limit`. Currently only
  Arricchisci has the typed form (`ArricchisciExecutionContext`).
- **Source loading dispatcher**: single entry point for the 3 source
  types (project_source, file_source, staticValues) with an explicit
  `ExecutionContext` input. Currently duplicated across both engines.
- **Node execution contract**: Protocol + typed result.
- **Executor dispatch table**: `OrchestratorService` selects among
  `{estrai, manipola, arricchisci}` by module_type string. A typed
  dispatch table backed by `CanvasExecutorProtocol` removes the
  if-elif chain and makes the dispatch explicit.

The engine intentionally does **not** own:
- transformation math (SQL, joins, geo, HTTP enrichment) — stays
  domain-specific in each vertical;
- parallel HTTP enrichment scheduling — KEEP_CUSTOM in Arricchisci;
- cross-join / multi-join OOM cardinality estimation — KEEP_CUSTOM in
  Manipola transforms;
- file-mode result path (`save_result_blob`) — KEEP_CUSTOM in
  Arricchisci.

---

## 5. What must stay domain-specific

| Domain | Reason |
|---|---|
| Manipola transform math (joins, filters, group-by, geo) | Pure Pandas/GeoPandas; no shared abstraction needed |
| Arricchisci HTTP enrichment (retry, rate limit, dedup, OAuth) | External I/O specifics; generalising would create a God HTTP Client |
| Manipola multi-join OOM guard (`MemoryLimitExceededError`) | Cartesian-product risk is join-specific; enrichment is row-by-row |
| Arricchisci parallel group scheduling | Specific to concurrent API call fan-out |
| Estrai SQL generation / ArcGIS REST path | Completely different data plane |

---

## 6. Safe extraction order — next 3 steps

### Step 1 — Shared `ExecutionContext` type (risk: LOW)

Extract `ArricchisciExecutionContext` into `app.shared.execution_context`
as a generic `CanvasExecutionContext`. Rename the Arricchisci class as
a KEEP_COMPAT alias. Migrate `ManipolaExecutor._init_security_context`
to build and return a `CanvasExecutionContext` instead of a bare `str`.

- Files: `app.shared.execution_context` (new), `arricchisci/execution_context.py`
  (alias), `manipola/executor.py` (return typed ctx).
- Risk: LOW. Pure refactor; `group_id` access becomes `ctx.group_id`.
  Existing strangler-contract tests catch regressions.
- Prerequisite: none.

### Step 2 — Shared source-loading dispatcher (risk: MEDIUM)

Extract the 3 source loaders (`project_source`, `file_source`,
`staticValues`) from both engines into a shared
`app.shared.source_loader_dispatch.load_canvas_sources(canvas,
dataframes, ctx)` function. Both engines call it.

- Files: `app.shared.source_loader_dispatch` (new), `manipola/executor.py`
  (thin delegators), `arricchisci/api_connection_source_loading.py` (thin
  delegators).
- Risk: MEDIUM. Arricchisci has a file-mode passthrough branch
  (`_is_file_source_for_file_processor`) that must stay in the
  orchestrator, not the dispatcher. Requires careful boundary
  separation.
- Prerequisite: Step 1 (shared `CanvasExecutionContext`).

### Step 3 — Typed OrchestratorService dispatch table (risk: LOW)

Replace the `if/elif module_type` chains in `OrchestratorService.
execute_pipeline` and `execute_preview` with a typed dict keyed by
module type and valued by `CanvasExecutorProtocol`. Add `isinstance`
guard at service init time.

- Files: `orchestrator/service.py`.
- Risk: LOW. Pure structural refactor; no runtime behavior change.
  The Protocol is already in place from Batch 055.
- Prerequisite: none (Protocol already exists).

---

## References

- `backend/app/shared/executor_protocol.py` — first shared seam (this batch).
- `backend/app/shared/graph.py` — shared topological sort.
- `backend/app/shared/canvas_node.py` — typed node accessor.
- `backend/app/core/arricchisci/execution_context.py` — typed Arricchisci ctx.
- `backend/app/core/manipola/executor.py:199` — `_init_security_context`.
- `backend/app/core/orchestrator/service.py` — executor dispatch (3 if-elif chains).
- `docs/architecture/horizontal-engines.md` — engine boundary index.
- `BE_RULES.md` — `TRANSFORMATION_ENGINE` alignment.
