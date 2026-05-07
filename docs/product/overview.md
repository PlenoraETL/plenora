# Product Overview

Plenora-ETL is an internal ETL platform for geospatial and tabular data
pipelines. Users compose pipelines on a visual canvas and connect typed
nodes for sources, transformations, enrichments, and outputs.

> Status: the product is in active hardening toward a stable 1.0. Backend
> work is focused on horizontal engine boundaries, runtime guardrails,
> clean installation, Docker validation, and documentation cleanup.

---

## Live Capabilities

| Area | Capability |
|---|---|
| Estrai | Multi-dialect SQL extraction plus ArcGIS Feature Service reads. |
| Manipola | Data transformations, joins, aggregations, spatial operations, CRS handling. |
| Arricchisci | REST/API enrichment, API generators, REST file processing, executable-based enrichment. |
| Carica | Database writing and ArcGIS Feature Service publishing. |
| IMDF | Apple Indoor Mapping Data Format bundle generation and ArcGIS publish flow. |
| Scheduler | Celery worker/beat, scheduled runs, heartbeat, run history. |
| Auth | JWT, RBAC, MFA, tenant/group isolation. |
| Storage | PostgreSQL, filesystem, object storage policy, resource/artifact loading. |
| Observability | Health/readiness, logs, audit actions, backup/restore operational docs. |

---

## Positioning

Plenora-ETL is not currently a public multi-tenant SaaS. It is an internal
Plenora platform for repeatable data operations:

- database-to-ArcGIS data publication;
- API enrichment pipelines;
- geospatial and tabular transformation workflows;
- IMDF/indoor mapping publication;
- scheduled ETL runs with operational traceability.

---

## Out Of Scope For 1.0

These are roadmap items, not current release blockers:

- AI/capability services beyond the current execution model;
- Kafka/Pulsar-style streaming ingestion;
- advanced raster/satellite analytics;
- full public SaaS hardening;
- broad data catalog and column-level lineage beyond the current metadata work.

---

## References

- [Architecture overview](../architecture/overview.md)
- [Macro components](../architecture/macro-components.md)
- [Horizontal engines](../architecture/horizontal-engines.md)
- [Release 1.0 readiness](release-1.0.md)
- [Roadmap](roadmap.md)
- [Backend setup](../development/backend.md)
- [Frontend setup](../development/frontend.md)
