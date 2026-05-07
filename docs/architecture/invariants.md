# Architecture Invariants

Status reviewed: 2026-05-07.

These are the live engineering rules for Plenora. They are intentionally
shorter than the historical invariant reports. Historical detail remains in
audits and ADRs.

## 1. Tenant And Auth

- Tenant/group identity must be explicit in runtime paths.
- Do not store request-specific group/user context in mutable singletons.
- Frontend authorization is UX only; backend authorization is authoritative.
- Superadmin bypasses must be explicit, narrow, and auditable.
- Auth dependencies should import focused contracts/helpers, not broad legacy
  facades.

## 2. Resource Handling

- No unbounded reads from files, object storage, database blobs, or external
  streams.
- Large payloads move by reference, not through generic dictionaries.
- `ExecutionResult` payloads must not contain DataFrames, raw bytes, streams,
  or file handles.
- Temporary file materialization is fallback behavior and must stay visible.
- Resource metadata should travel with the loaded resource instead of being
  reconstructed repeatedly from canvas dictionaries.

## 3. Execution

- Parse canvas dictionaries at the boundary and work with typed structural
  contracts internally.
- Shared canvas structures must stay structural; node-type business behavior
  belongs in the owning execution/transform engine.
- Use shared graph utilities for topological ordering and cycle detection.
- Do not create a generic mega-executor until duplicated behavior proves it is
  the right abstraction.
- Execution outputs must be typed or explicitly bounded.

## 4. Persistence

- Internal PostgreSQL access goes through the DB layer.
- Business modules must not create private pools or driver-level shortcuts.
- Prefer focused repositories over broad DB facades.
- Do not re-expand retired compatibility facades.
- SQL compatibility shims must have a removal path or a clearly documented
  reason to remain.

## 5. Connections

- ArcGIS-like systems are external read/write providers, not IMDF internals.
- Provider contracts belong in shared connection packages.
- Concrete clients and provider-heavy dependencies must not leak into shared
  contracts.
- Adding a provider should not require editing unrelated canvas logic.

## 6. Errors

- Use typed/domain errors where the frontend or operator needs stable meaning.
- Do not add one-off error codes when an existing class communicates the
  failure.
- Internal server errors must avoid leaking stack traces or secrets.
- Router-level catch-all blocks should not hide classifiable failures.

## 7. Configuration

- Runtime configuration should have one canonical name.
- Legacy aliases may be documented historically but must not silently return as
  runtime fallbacks.
- Secrets are not normal config and must not be logged.
- Memory/resource caps must be centralized or follow a documented split.

## 8. Shared Package

- `app.shared` is not a junk drawer.
- New shared code must live under a thematic package such as `execution`,
  `resources`, `connections`, or `contracts`.
- Shared leaf contracts must not import `app.core`, FastAPI routers, Pandas,
  GeoPandas, concrete cloud clients, or DB connection code.
- Root-level shared files should shrink over time.

## 9. Tests

- Contract tests are valuable when they enforce architecture.
- Avoid tests that only pin implementation noise.
- AST/source-shape tests are acceptable as guardrails for high-risk legacy
  regressions, but should not become the main design mechanism.
- Prefer behavior tests for runtime correctness.

## 10. Documentation

- Canonical docs describe current reality.
- ADRs are append-only.
- Historical audits are read-only.
- If a batch changes an engine boundary, installation behavior, security
  contract, or resource contract, update docs in the same batch.

## Release Gate

Before 1.0, these invariants must be true in production paths:

- no known mutable singleton tenant context;
- no known unbounded resource read in hot paths;
- no active frontend dependency on legacy backend error payloads;
- no hidden install requirement outside the install docs;
- no canonical doc that contradicts the current architecture.
