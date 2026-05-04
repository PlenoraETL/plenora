# Strategic Backlog

This backlog captures strategic ideas that are useful for PLENORA-ETL but are not part of the current narrow BE/FE coordination batches.

## Go-Ready And Backend Architecture

- Schema inference parity is closed after the Phase 2 zero-residual track.
  - `PASSTHROUGH_WRONG_GAP == 0`.
  - New transform work must preserve the lock: every registry transform is either handled by dispatcher/schema contract or explicitly rows-only passthrough-OK.
- Consider extracting `SchemaInferenceService` handlers into category modules only if the file becomes a real maintenance blocker.
  - The dispatcher is already registry/map based; the next cleanup is physical decomposition, not another dispatch rewrite.
- Evaluate typed config boundaries for transform families.
  - Current FE canvas config is still dict-shaped for compatibility.
  - Future Go port will benefit from explicit config structs.
- Audit runtime/registry mismatches.
  - Example: runtime can differ from registry metadata on output shape or type shifts.
  - Keep this as a contract-test habit, not a one-off audit.

## Gemini Advice Triage

This triage captures `consigli_gemini.md` as backlog input, not as an implementation directive.

### Near-Term Go-Ready Candidates

- Global API error envelope.
  - Useful because it improves FE handling, logs, and future Go parity.
  - Bound it first with a response contract and regression tests for existing error surfaces.
- `pydantic-settings` for typed startup config.
  - Good bounded BE task if it preserves existing env names and production fail-fast behavior.
  - Avoid touching secret-loading semantics without a security-specific batch.
- Structured JSON logging to stdout.
  - High operational value and small enough if introduced behind current logger helpers.
  - Must preserve `request_id` propagation.
- Circuit breakers for external enrichment providers.
  - Good candidate because it protects Celery/API workloads without broad architecture churn.
  - Implement per provider boundary, not as a global catch-all.
- Runtime/schema drift validation for config-explicit outputs.
  - Candidate follow-up after `flatten_json`/`transpose` schema contracts.
  - Prefer warnings/validation reports over changing runtime transforms.

### Already Partly Covered

- RBAC and group isolation exist already.
  - Gemini's "add RBAC" should be read as "make permissions more granular per resource/action", not as greenfield RBAC.
- Request ID tracing exists and is documented.
  - Next step is structured log emission, not request-id redesign.
- Hybrid metadata + object/file storage is already aligned with the recommendation.
  - Future work is snapshot reuse and governance, not a storage reset.
- Registry-driven transform exposure already exists.
  - The completed schema-inference parity work made it safer.

### Strategic But Not Immediate

- Async SQLAlchemy/asyncpg.
  - Large blast radius; require evidence of Uvicorn DB blocking or connection pressure first.
  - PgBouncer may be a lower-risk first response to connection fan-out.
- Polars/DuckDB/Arrow/Dask.
  - Treat as measured performance experiments for selected heavy transforms.
  - Do not replace Pandas/GeoPandas globally without benchmarks and spatial compatibility checks.
- Smart re-run / incremental execution.
  - Valuable, but needs stable node hash, snapshot freshness rules, and invalidation semantics.
- OIDC/SAML and granular enterprise IAM.
  - Product/enterprise track. Requires permission model design before coding.
- Column-level lineage and governance UI.
  - High value, but it depends on stable transform contracts and schema propagation.

### Deferred Or Requires Product Decision

- Multi-output nodes / FME-style multi-port transformers.
  - Major canvas and execution model change. Needs product design before BE/FE tasks.
- Universal Attribute Manager.
  - Useful UX consolidation, but it changes how users compose transforms. Needs design and migration story.
- Data-quality validator nodes.
  - Good feature family, but should reuse the validation contract work instead of creating a parallel system.
- Cloud warehouses, SaaS connectors, IFC/DWG/DXF, COG/raster.
  - Strategic connectivity roadmap, not go-ready cleanup.
- OS-level credential encryption / Vault / Secrets Manager.
  - Security architecture decision; must be handled as an explicit threat-model batch.

### Explicit Non-Goals For Current Phase

- No broad async DB rewrite as a cleanup task.
- No global Pandas replacement.
- No data-sampling inside `SchemaInferenceService`.
- No multi-port canvas model without product design.
- No migration of unresolved Phase 2 residuals; the Phase 2 rule remains zero residuals.

## Database And Services

- Evaluate DB layer modernization only after current contracts stabilize.
  - Async SQLAlchemy/asyncpg may be useful, but should be justified by measured Uvicorn DB blocking or connection pressure.
  - PgBouncer may be a lower-risk first step if the main issue is connection fan-out from Uvicorn/Celery workers.
- Consider structured dependency wiring.
  - FastAPI `Depends()` can help at router boundaries.
  - Keep service interfaces explicit and testable; avoid a broad DI rewrite without a concrete pain point.
- Consider `pydantic-settings` for fail-fast typed environment/config loading.
- Standardize API error responses behind a global error envelope.

## ETL Runtime And Performance

- Implement smart re-run / incremental execution using stable node hashes and snapshot reuse.
- Evaluate Polars for tabular-heavy transforms.
  - Treat as selective adoption, not a global Pandas replacement.
  - GeoPandas/spatial transforms need separate evaluation.
- Add circuit breakers around external enrichment providers.

## Enterprise Readiness

- Add OIDC/SAML integration for enterprise identity providers.
- Expand RBAC into granular project roles.
- Expose data lineage in the UI.
- Plan zero-downtime migration practices with Alembic-safe rollout patterns.
- Add structured JSON logging to stdout for production observability.

## Notes

- The project is moving in a Go-ready direction, but "Go-ready" should be read as architectural progress, not runtime portability.
- The largest Go-porting gap remains the dataframe/spatial runtime, not only service wiring.
- Strategic work should be pulled into BE/FE coordination files only when it becomes a bounded implementation batch.
