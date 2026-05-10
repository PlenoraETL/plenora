# ADR-0016: IMDF Boundaries

**Status**: Accepted

**Date**: 2026-04-23

## Context

The IMDF module handles bundle decoding, local materialization, remote bundle
storage, validation, publish orchestration, and HTTP access. Those concerns
must stay separated so cross-node cache behavior, path validation, and
`site_id` handling remain predictable.

## Decision

IMDF is split by responsibility:

- `loader`: decodes input bundles and owns `site_id` inference.
- `persist`: materializes a bundle locally and writes metadata. It does not
  upload to remote storage.
- `storage`: backs up and hydrates bundle directories through object storage.
  It works with paths and bytes, not `ImdfBundle`.
- `router`: owns HTTP, ACL, cache/hydrate decisions, and response shaping.
- `validator`: reads a hydrated bundle and produces validation reports.
- `app.shared.path_policy.validate_imdf_task_id`: owns task id path-safety.

`site_id` is produced once by the loader, stored in `plenora_metadata.json`,
and read by router/validator paths from metadata.

## Operational Contract

- Router paths that read bundle content must hydrate local cache when needed.
- Path traversal protection for IMDF task ids goes through
  `validate_imdf_task_id`.
- Bundle metadata is part of the persisted local contract.
- `persist` must clean temporary directories and fail loudly when metadata
  cannot be written.
- Remote upload failure is handled by the orchestrating worker, not hidden
  inside `persist`.
- IMDF uses ArcGIS as an external provider boundary; it does not own ArcGIS
  provider behavior.

## Consequences

Positive:

- IMDF bundle behavior is testable per module.
- Cross-node bundle access has one hydrate policy.
- `site_id` has one source of truth.
- Local persistence and remote storage are decoupled.

Negative:

- Cache miss reads can be slower because they may hydrate from object storage.
- Some old bundle directories may lack metadata and need compatibility handling
  at read time.

## References

- `backend/app/core/imdf/`
- `backend/app/shared/path_policy.py`
- `backend/workers/tasks/enrichment_task.py`
- [ADR-0007](0007-2026-04-21-protocol-runtime-checkable-vs-abc.md)
- [ADR-0011](0011-2026-04-21-file-sources-dispatch-s3-threshold.md)
- [ADR-0023](0023-2026-05-06-arcgis-external-provider-boundary.md)
