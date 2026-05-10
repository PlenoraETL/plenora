# ADR-0014: Log Archive Pipeline

**Status**: Accepted

**Date**: 2026-04-21

## Context

`plenora.logs` and audit tables can grow without bound. Production needs
retention, archive-before-purge behavior, and observability without exposing a
user path that can delete log history.

## Decision

Plenora archives eligible log and audit rows before retention purge.

The pipeline uses:

- an isolated archive writer;
- explicit SELECT callables for rows eligible by retention cutoff;
- explicit DELETE callables guarded by the retention cleanup bypass;
- object storage for archive files;
- scheduler hooks for `logs` and audit retention;
- append-only application log behavior outside the authorized retention path.

User-facing log clear endpoints are not part of the contract.

## Operational Contract

- `plenora.logs` is append-only for application code.
- Retention purge is allowed only through the scheduled archive-before-purge
  flow.
- The retention bypass is scoped to the purge transaction and must not be
  exposed through routers or general DB helpers.
- Archive writes must complete before rows are purged.
- Archive observability may insert a success row directly into `plenora.logs`
  with the canonical column shape. This exception is limited to the archive
  hooks and remains append-only.
- Failure observability uses structured warnings; failure paths must not purge
  rows that were not archived.

## Configuration

Relevant runtime settings include:

- `LOG_RETENTION_DAYS`
- `AUDIT_RETENTION_DAYS`
- `LOG_ARCHIVE_ENABLED`
- `LOG_ARCHIVE_BUCKET`
- object-storage provider settings used by `get_log_archive_storage()`

Validation must fail fast when archive storage is required but not configured.

## Consequences

Positive:

- Log tables have a bounded retention path.
- Audit evidence can survive database retention through object storage.
- Application code has no normal delete path for log rows.

Negative:

- Archive storage becomes part of the operational runtime.
- Retention bugs can affect both database growth and audit availability, so
  scheduler coverage and configuration validation are required.

## References

- `backend/workers/beat_scheduler.py`
- `backend/app/shared/cloud_backends.py`
- `backend/app/db/*log*`
- [Architecture Invariants](../invariants.md)
