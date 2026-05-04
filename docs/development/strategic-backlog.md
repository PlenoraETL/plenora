# Strategic Backlog

This backlog captures strategic ideas that are useful for PLENORA-ETL but are not part of the current narrow BE/FE coordination batches.

## Go-Ready And Backend Architecture

- Continue schema inference parity work before broad refactors.
  - Current priority: convert implicit transform behavior into explicit, tested contracts.
  - Keep reducing `PASSTHROUGH_WRONG_GAP` before refactoring dispatcher shape.
- Consider replacing the large `SchemaInferenceService` dispatcher with registry/map dispatch after parity gaps are mostly closed.
- Evaluate typed config boundaries for transform families.
  - Current FE canvas config is still dict-shaped for compatibility.
  - Future Go port will benefit from explicit config structs.
- Audit runtime/registry mismatches.
  - Example: runtime can differ from registry metadata on output shape or type shifts.

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
