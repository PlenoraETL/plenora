# Architecture Overview

Plenora is a no-code/low-code ETL platform built around visual canvases,
asynchronous execution, tenant isolation, and resource-aware artifact
handling.

This page describes the current architecture. Detailed decisions live in
[`docs/architecture/decisions/`](decisions/).

## Runtime Shape

```text
Browser UI
  |
  | REST + JWT
  v
FastAPI backend
  |
  +-- routers and dependencies
  +-- horizontal services and execution engines
  +-- Celery task dispatch
  |
  +--> PostgreSQL
  +--> Redis
  +--> object storage, when configured
  +--> external systems: databases, APIs, ArcGIS-like providers

Celery worker
  |
  +-- executes project graphs
  +-- reads and writes resources through resolvers/loaders
  +-- records task, run, result, and reconciliation state

Celery beat
  |
  +-- scheduled runs
  +-- reconciliation and operational maintenance
```

The frontend is React/Vite. The backend is FastAPI/Celery/PostgreSQL/Redis.
Object storage is optional but required for large-artifact paths and some
clean deployment profiles.

## Horizontal Direction

The backend is being moved from vertical project modules toward horizontal
capabilities:

- Auth engine: authentication, authorization, user/group contracts.
- Connection engine: external databases, APIs, ArcGIS-like providers.
- Execution engine: canvas graph parsing, orchestration, task results.
- Transformation engine: dataframe and geospatial operations.
- Resource engine: files, result blobs, artifacts, metadata, caps.
- Storage engine: object storage, backup, reconciliation, retention.
- Observability/ops: health, readiness, logs, install validation.

Vertical domains such as Estrai, Manipola, Arricchisci, Carica, and IMDF
should become consumers of these capabilities rather than owners of
duplicated infrastructure.

See [Horizontal Engines](horizontal-engines.md) for the current map.

## Shared Package Rule

`app.shared` is for contracts and small leaf utilities that are useful
across engines. It must not become a junk drawer.

Allowed patterns:

- `app.shared.execution`: canvas, graph, execution result contracts.
- `app.shared.resources`: resource references, metadata, caps, payloads.
- `app.shared.connections`: provider protocols and connection contracts.

Avoid adding new root-level shared files unless there is a deliberate
architecture reason and a contract test allows it.

## Execution Contract

Execution paths should converge on typed contracts:

- Parse incoming canvas dictionaries once.
- Work with typed canvas nodes and graphs internally.
- Return an `ExecutionResult` shape.
- Keep payloads bounded and reference-based.
- Never pass DataFrames, raw bytes, or file handles through result payloads.

Large data must move through resources, references, previews, and caps.

## Resource Contract

The resource layer owns:

- tenant and project binding for resource reads;
- size caps and bounded reads;
- artifact references and metadata;
- object storage access;
- compatibility reads for stored data written by older runtime paths.

Loaders should return bytes, streams, paths, or metadata only when the
capability explicitly requires it. Temporary file materialization is a
fallback path, not the preferred path.

## Auth And Tenancy

Every runtime path must preserve group isolation. Superadmin bypasses must
be explicit and auditable. Request-specific context should be passed as
data, not hidden in mutable singleton state.

Frontend authorization is only a UX convenience. Backend checks are the
source of truth.

## Installation State

Clean installation work has two tracks:

- single-machine installation with PostgreSQL, Redis, backend, worker, beat,
  and frontend;
- Docker installation with backend, worker, beat, PostgreSQL, Redis, and
  frontend image/static delivery documented in the compose path.

The release target is not only "starts locally"; it must be reproducible on
a clean machine.

## Non-Goals

- No new business features during architecture cleanup.
- No speculative mega-engine that replaces small focused capabilities.
- No new compatibility facade without a removal plan.
- No broad rewrite of accepted ADRs unless the recorded decision is wrong or
  no longer operative.

## References

- [Horizontal Engines](horizontal-engines.md)
- [Architecture Invariants](invariants.md)
- [Backend Development](../development/backend.md)
- [Frontend Development](../development/frontend.md)
- [Docker Install](../ops/install-docker.md)
