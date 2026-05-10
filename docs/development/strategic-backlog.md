# Strategic Backlog

This backlog is a parking lot for useful ideas that are not part of the
release 1.0 documentation contract. Items here are not implementation
commitments.

## Backend Architecture

- Keep schema inference and transform registry behavior aligned with contract
  tests.
- Split `SchemaInferenceService` by transform family only if the file becomes
  a proven maintenance blocker.
- Add typed config boundaries for transform families when canvas contracts are
  ready for explicit schemas.
- Standardize API error responses behind a global envelope after existing
  frontend error handling is fully mapped.
- Evaluate `pydantic-settings` or an equivalent typed settings boundary while
  preserving current env names and fail-fast behavior.

## Runtime And Performance

- Evaluate async DB access only with evidence of request-thread blocking or
  connection pressure.
- Consider PgBouncer before a broad database access rewrite when the problem is
  connection fan-out.
- Evaluate Polars, DuckDB, Arrow, or Dask only for measured heavy transforms.
- Add circuit breakers around external enrichment providers.
- Implement smart re-run using stable node hashes, snapshot freshness rules,
  and invalidation semantics.
- Add runtime/schema drift validation for transforms with explicit output
  contracts.

## Product And Enterprise

- Add OIDC/SAML integration for enterprise identity providers.
- Expand RBAC into granular project/resource roles.
- Expose column-level lineage and governance in the UI.
- Add data-quality validator nodes on top of existing validation contracts.
- Evaluate multi-output nodes only with a product design for canvas semantics.
- Evaluate broader connector families such as cloud warehouses, SaaS systems,
  IFC/DWG/DXF, and raster formats.

## Operations

- Add structured JSON logging to stdout while preserving `request_id`.
- Define zero-downtime database change practices for production deployments.
- Evaluate managed secrets providers or OS-level secret isolation as a
  dedicated security design.

## Non-Goals

- No global Pandas replacement without benchmarks and spatial compatibility
  checks.
- No data sampling inside schema inference as a shortcut for undefined
  transform contracts.
- No broad dependency-injection rewrite without a concrete ownership problem.
- No multi-port canvas model without product design.
