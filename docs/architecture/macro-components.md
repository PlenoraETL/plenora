# Backend macro-component map

`BE-GO-READY-ARCH-055-W1-MACRO-COMPONENT-MAP-ARCGIS-PROVIDER`

Single-screen reference for the seven backend macro components and the
external-system plane they interact with. Companion to
[`overview.md`](overview.md) (full architecture),
[`horizontal-engines.md`](horizontal-engines.md) (engine boundaries and
do-not-import rules), and [`invariants.md`](invariants.md) (binding rules).

The classification here is binding for BE work: new code strengthens the
relevant macro block; cross-block imports are explicit, typed, and minimal.

---

## 1. Macro-component blocks

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Plenora Backend                                   │
│                                                                          │
│  ┌──────────────────────────┐  ┌────────────────────────────────────┐   │
│  │   A. Auth / Identity     │  │  B. Connections / Ext. Providers   │   │
│  │  JWT, RBAC, MFA,         │  │  DB connections, API connections,  │   │
│  │  tenant isolation,       │  │  ArcGIS connections, datasources.  │   │
│  │  crypto at-rest          │  │  ★ ArcGIS client lives here.       │   │
│  │  app/core/auth/          │  │  app/core/features/                │   │
│  │  app/shared/ownership.py │  │  app/core/connections/             │   │
│  └──────────────────────────┘  │  app/core/datasources/             │   │
│                                └────────────────────────────────────┘   │
│  ┌──────────────────────────┐  ┌────────────────────────────────────┐   │
│  │  C. Execution Engine     │  │  D. Transformation Capabilities    │   │
│  │  Pipeline orchestration, │  │  33+ transform ops, spatial,       │   │
│  │  Estrai / Manipola /     │  │  SQL/API functions, validation,    │   │
│  │  Arricchisci / Carica,   │  │  data quality                      │   │
│  │  Celery workers,         │  │  app/core/manipola/transforms/     │   │
│  │  scheduler, run lifecycle│  │  app/core/estrai/                  │   │
│  │  app/core/orchestrator/  │  │  app/utils/                        │   │
│  │  workers/                │  └────────────────────────────────────┘   │
│  └──────────────────────────┘                                            │
│  ┌──────────────────────────┐  ┌────────────────────────────────────┐   │
│  │  E. Resource / Storage   │  │  F. Persistence / DB               │   │
│  │  Artifacts, blobs, file  │  │  PostgreSQL DAL, config/results/   │   │
│  │  sources, IMDF bundles,  │  │  logs, migrations, pg_connection   │   │
│  │  object storage (S3 /    │  │  pool, query executor              │   │
│  │  MinIO), result store    │  │  app/db/                           │   │
│  │  app/core/resources/     │  └────────────────────────────────────┘   │
│  │  app/core/artifacts/     │                                            │
│  │  app/core/imdf/          │  ┌────────────────────────────────────┐   │
│  │  app/services/           │  │  G. Observability / Health /       │   │
│  └──────────────────────────┘  │     Install                        │   │
│                                │  /health, /ready, logging,         │   │
│                                │  audit_actions, backup obs,        │   │
│                                │  install scripts, Docker scaffold  │   │
│                                │  app/observability/                │   │
│                                │  scripts/                          │   │
│                                └────────────────────────────────────┘   │
└────────────┬──────────────┬──────────────┬───────────────┬──────────────┘
             │              │              │               │
             ▼              ▼              ▼               ▼
      ┌──────────┐  ┌───────────┐  ┌────────────┐  ┌──────────────────┐
      │PostgreSQL│  │   Redis   │  │ S3 / MinIO │  │ External         │
      │plenora   │  │blacklist  │  │object store│  │ Providers        │
      │schema    │  │+ beat lock│  │+ backup    │  │ ★ ArcGIS REST    │
      └──────────┘  └───────────┘  └────────────┘  │   (read + write) │
                                                    │   User DBs       │
                                                    │   REST APIs      │
                                                    └──────────────────┘
```

---

## 2. ArcGIS: external read/write provider, not IMDF subdomain

### Formal decision

> **ADR-0023** — ArcGIS is an external read/write provider/datastore
> capability, classified in macro block B (Connections / External
> Providers). IMDF is a publish consumer of that provider. See
> [`decisions/0023-2026-05-06-arcgis-external-provider-boundary.md`](decisions/0023-2026-05-06-arcgis-external-provider-boundary.md).

### Classification table

| Concern | Macro block | Module(s) | Classification |
|---|---|---|---|
| ArcGIS REST client (token, pagination, batch write) | **B — Connections** | `app/core/features/arcgis_client.py` | `GENERIC_ALREADY_OK` — canonical ArcGIS transport |
| ArcGIS connection CRUD + browsing + discovery | **B — Connections** | `app/core/features/features_service.py`, `routes.py` | `GENERIC_ALREADY_OK` |
| IMDF → ArcGIS publish adapter (5-layer bundle write) | **E — Resource/Storage** | `app/core/imdf/arcgis_uploader.py`, `arcgis/` | `KEEP_CUSTOM` — domain-specific orchestration over provider; see §2.2 |
| Estrai featureLayerNode (ArcGIS read source) | **C — Execution Engine** | `app/core/estrai/` | `GENERIC_ALREADY_OK` — reads via provider |
| Carica `carica_arcgis` node (ArcGIS write sink) | **C — Execution Engine** | `app/core/carica/` | `GENERIC_ALREADY_OK` — writes via provider |

### 2.1 Why ArcGIS is a provider, not a domain

ArcGIS is a **remote datastore with a REST API**: it stores geospatial
feature layers (points, lines, polygons) and exposes them with standard
CRUD semantics (query / addFeatures / updateFeatures / deleteFeatures /
applyEdits). From the backend's perspective it is symmetrical to:

- **PostgreSQL**: read/write via SQL, holds rows and spatial data.
- **S3 / MinIO**: read/write blobs, addressed by key.
- **External REST APIs**: read/write data over HTTP with auth.

Provider responsibilities owned by block B:
- Authentication lifecycle (token fetch, cache, refresh, C12 policy).
- Connection credentials management and encryption.
- Retry and rate-limit policies.
- Type mapping (Esri field types ↔ Plenora schema buckets).

### 2.2 IMDF's relationship to ArcGIS

IMDF (`app/core/imdf/`) converts a building bundle (DXF → GeoJSON →
ArcGIS layers) and publishes it via the ArcGIS REST API. The
`arcgis_uploader.py` facade and `arcgis/` subpackage are the
**publish adapter**: they contain IMDF-specific batch write logic
(5-layer publication order, service creation/lifecycle, upsert/append
modes, OBJECTID resolution). This is `KEEP_CUSTOM` because:

1. The publication protocol is tightly coupled to IMDF layer semantics
   (Localita, OperaPrincipale, Livello, Locale, Details order is
   non-generic).
2. The adapter is not usable for other ArcGIS write scenarios; it is an
   IMDF-to-ArcGIS transform, not a general write helper.

Boundary rule: **IMDF publish adapter code must NOT import from
`app.core.features` (the connection management domain)**. The IMDF
arcgis subpackage speaks directly to the ArcGIS REST API using its own
client session; it does not route through `FeaturesService` or
`ArcGISClient` from Features. Locked by
`tests2/shared/test_arcgis_provider_boundary_guard.py`.

### 2.3 Follow-up code moves recommended

These are not in-scope for W1 (docs only); classified for future batches.

| Finding | Classification | Recommended action |
|---|---|---|
| `arcgis_uploader.py` + `arcgis/` duplicate token/retry logic already in `arcgis_client.py` | `CUSTOM_BUT_SHOULD_BE_GENERIC` | Medium-term: extract shared transport to a stable `ArcGISClientProtocol`; let both consumers adapt. Risk: medium (IMDF publish has batch-write specifics). |
| `EsriTypeMapper` in `arcgis_client.py` used by IMDF validator indirectly via `features_service` | `CUSTOM_BUT_SHOULD_BE_GENERIC` | Move `EsriTypeMapper` to `app/shared/` or a provider-agnostic types module. |
| `ArcGISConnectionInfo` dataclass lives in `features/arcgis_client.py` but is used by both Features and IMDF | `CUSTOM_BUT_SHOULD_BE_GENERIC` | Move to `app/core/connections/arcgis_types.py` or similar to avoid cross-domain import. |

---

## 3. Block responsibilities and canonical paths

### A. Auth / Identity — `AUTH_SECURITY_ENGINE`

| Responsibility | Canonical module |
|---|---|
| JWT middleware | `app/core/auth/middleware.py` |
| Role guards | `app/core/auth/dependencies.py` |
| Ownership helpers | `app/shared/ownership.py` |
| Fernet crypto at-rest | `app/shared/crypto.py` |
| Tenant assertion | `app/shared/authz_helpers.py` |

### B. Connections / External Providers — `CONNECTION_ENGINE`

| Responsibility | Canonical module |
|---|---|
| ArcGIS REST client (canonical) | `app/core/features/arcgis_client.py` |
| ArcGIS connection CRUD | `app/core/features/features_service.py` |
| DB connections | `app/core/connections/` |
| API connections | `app/core/arricchisci/` (connection management slice) |
| Datasource discovery | `app/core/datasources/` |

### C. Execution Engine — `ORCHESTRATION_ENGINE` + `TRANSFORMATION_ENGINE`

| Responsibility | Canonical module |
|---|---|
| Pipeline orchestration | `app/core/orchestrator/` |
| Estrai executor | `app/core/estrai/executor.py` |
| Manipola executor | `app/core/manipola/executor.py` |
| Arricchisci executor | `app/core/arricchisci/api_connector.py` |
| Carica executor | `app/core/carica/` |
| Celery workers | `workers/tasks/` |
| Scheduler | `workers/beat_scheduler.py` |

### D. Transformation Capabilities — `TRANSFORMATION_ENGINE`

| Responsibility | Canonical module |
|---|---|
| 33+ transform ops | `app/core/manipola/transforms/` |
| Spatial operations | `app/utils/geo.py`, `app/utils/dataframe.py` |
| Formula eval | `app/utils/formula_evaluator.py` |

### E. Resource / Storage — `STORAGE_ARTIFACT_ENGINE`

| Responsibility | Canonical module |
|---|---|
| Bounded reader | `app/core/resources/bounded_reader.py` |
| Resource registry | `app/core/resources/registry.py` |
| Artifact resolver | `app/core/artifacts/resolver.py` |
| Result store | `app/core/*/result_store.py` |
| File source dispatch | `app/services/file_service.py` |
| Object storage | `app/shared/cloud_backends.py` |
| IMDF publish adapter | `app/core/imdf/arcgis_uploader.py`, `arcgis/` |

### F. Persistence / DB — `CONNECTION_ENGINE` / `DEPLOYMENT_RUNTIME_ENGINE`

| Responsibility | Canonical module |
|---|---|
| Connection pool | `app/db/pg_connection.py` |
| Config DAL | `app/db/config/*.py` |
| Results / logs DAL | `app/db/results_db_service.py` |
| User-DB query executor | `app/db/query_executor.py` |

### G. Observability / Health / Install — `OBSERVABILITY_ENGINE` + `DEPLOYMENT_RUNTIME_ENGINE`

| Responsibility | Canonical module |
|---|---|
| Health / readiness | `app/observability/health.py` |
| Structured logging | `app/utils/logger.py` |
| Audit actions | `app/shared/audit.py` |
| Install scripts | `scripts/install.sh`, `scripts/docker-validate.sh` |

---

## 4. Cross-block import policy

Only the following cross-block directions are allowed:

```
C (Execution)  →  B (Connections)   via provider interface/client
C (Execution)  →  D (Transforms)    via pure function calls
C (Execution)  →  E (Storage)       via result_store / artifact resolver
C (Execution)  →  F (DB)            via config / results DAL
C (Execution)  →  A (Auth)          via auth dependencies (HTTP gate only)
E (Storage)    →  F (DB)            via results / file source DAL
A (Auth)       →  F (DB)            via config.users / sessions
B (Connections)→  F (DB)            via config.connections / api_connections
```

Do NOT:
- Route E (IMDF) → B (Features) for ArcGIS calls. ADR-0023.
- Import auth middleware from execution engine internals.
- Let D (Transforms) import DB, HTTP, or auth.

---

## See also

- [`overview.md`](overview.md) — full architecture (sec. 1-10).
- [`horizontal-engines.md`](horizontal-engines.md) — engine boundaries and do-not-import locks.
- [`invariants.md`](invariants.md) — binding rules.
- [`decisions/0023-2026-05-06-arcgis-external-provider-boundary.md`](decisions/0023-2026-05-06-arcgis-external-provider-boundary.md) — ArcGIS provider boundary ADR.
- [`decisions/0016-2026-04-23-imdf-boundaries.md`](decisions/0016-2026-04-23-imdf-boundaries.md) — IMDF module boundaries.
- [`decisions/0017-2026-04-24-features-vertical-status.md`](decisions/0017-2026-04-24-features-vertical-status.md) — Features vertical status.
- `tests2/shared/test_arcgis_provider_boundary_guard.py` — import boundary guard.
