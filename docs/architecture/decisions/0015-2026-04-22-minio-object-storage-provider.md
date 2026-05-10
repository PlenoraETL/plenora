# ADR-0015: MinIO Object Storage Provider

**Status**: Accepted

**Date**: 2026-04-22

## Context

Plenora needs object storage for artifacts, result blobs, backups, and
retention archive paths. AWS S3 is not the only deployment target; on-premise
or local deployments need an S3-compatible provider.

## Decision

Support MinIO as an object storage provider through the existing S3-compatible
storage abstraction.

Runtime code must use storage factories and the `ObjectStorage` protocol
rather than constructing provider clients directly.

## Operational Contract

- Provider selection is configuration-driven.
- `STORAGE_POLICY=object_storage` requires object storage to be enabled and a
  runtime artifact bucket to be configured.
- Runtime artifacts use the runtime artifact bucket, not the backup bucket.
- Backup and archive storage can have separate factories and validation rules.
- Business modules must not call `boto3.client("s3")` or equivalent provider
  clients directly.

## Consequences

Positive:

- Local and on-premise deployments can use object storage without AWS.
- Storage behavior remains behind one protocol and factory layer.
- Contract tests can validate provider parity at the boundary.

Negative:

- Deployment docs must describe MinIO credentials, endpoint URL, and bucket
  setup clearly.
- Provider-specific edge cases still need validation at install time.

## References

- `backend/app/shared/cloud_backends.py`
- `backend/app/config/storage_config.py`
- [ADR-0007](0007-2026-04-21-protocol-runtime-checkable-vs-abc.md)
- [ADR-0014](0014-2026-04-21-log-archive-pipeline.md)
