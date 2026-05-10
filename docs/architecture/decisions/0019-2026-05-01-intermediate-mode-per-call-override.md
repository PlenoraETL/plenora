# ADR-0019: Intermediate Persistence Mode Per-Call Override

**Status**: Accepted

**Date**: 2026-05-01

## Context

`INTERMEDIATE_PERSISTENCE_MODE` controls how much intermediate pipeline data is
materialized. A global setting is useful for deployment defaults, but operators
and tests sometimes need a per-execution override without changing env vars and
restarting services.

## Decision

Support an optional per-call override for intermediate persistence mode on
execution and preview entry points.

The accepted modes are:

- `metadata_only`: persist metadata and references where possible.
- `hybrid`: persist metadata and bounded row data according to policy.
- `full_pg`: persist all intermediate rows in PostgreSQL for compatibility
  reads and debugging.

The request-level value overrides `INTERMEDIATE_PERSISTENCE_MODE` only for the
current execution. The global setting remains the deployment default.

## API Contract

- The override is additive and optional.
- Existing requests without the override preserve current behavior.
- Invalid mode values fail fast with a client-visible validation error.
- The selected mode must propagate consistently through orchestrator,
  Manipola, and Arricchisci execution paths.

## Rationale

A query parameter keeps the change additive for shared sync/async request
bodies and avoids forcing all existing consumers to update their JSON payloads.

## Consequences

Positive:

- Operators can run targeted executions with a different materialization mode.
- Tests can exercise persistence behavior without mutating process-wide env.
- The default deployment policy remains centralized.

Negative:

- Execution code must pass the selected mode explicitly.
- Documentation and tests must treat mode propagation as part of the execution
  contract.

## References

- `backend/app/core/orchestrator/router.py`
- `backend/app/core/manipola/executor.py`
- `backend/app/core/arricchisci/api_connector.py`
- [ADR-0015](0015-2026-04-22-minio-object-storage-provider.md)
