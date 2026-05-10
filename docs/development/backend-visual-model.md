# Backend Visual Model

Modello visuale del backend Plenora/URBY ETL, aggiornato dallo stato reale di
`backend/app`, `backend/workers`, `backend/tests2` e dal wiring runtime.

Scopo: aiutare a leggere l'architettura, capire se il codice si sta
orizzontalizzando, individuare moduli sparsi e separare capability verticali
da engine tecnici condivisi.

Questo file usa solo diagrammi ASCII per evitare problemi di rendering.

## 0. Lettura Architetturale

Il backend e' ibrido:

```text
                         HTTP / async / scheduled entrypoints
                                      |
                                      v
                         vertical product capabilities
                                      |
                                      v
                         horizontal technical engines
                                      |
                                      v
                  PostgreSQL / Redis / object storage / providers
```

La direzione e' chiara: i moduli di prodotto restano verticali, mentre i
contratti condivisi scendono in engine orizzontali.

```text
Vertical capabilities
  auth, projects, files, datasources, connections, estrai, manipola,
  arricchisci, carica, features, imdf, intermediate, orchestrator,
  scheduler, eseguibili, logs, tickets, admin_backups, admin_system_status

Horizontal engines
  AUTH_SECURITY_ENGINE
  CONNECTION_PROVIDER_ENGINE
  STORAGE_ARTIFACT_RESOURCE_ENGINE
  EXECUTION_ORCHESTRATION_ENGINE
  OBSERVABILITY_ENGINE
  DEPLOYMENT_RUNTIME_ENGINE
```

Il segnale positivo dell'orizzontalizzazione e' che le capability verticali
dipendono da superfici piccole in `app.shared`, `app.runtime`, `app.db` e dai
propri moduli `app.core.<domain>`. I container `app.services` e le facade DB
larghe non sono superfici operative del codice produttivo.

## 0.1 Full Backend Block Map

Questa e' la vista completa dall'alto al basso. Le sezioni successive fanno zoom
sui singoli blocchi.

```text
                                  clients / frontend / operators
                                               |
                                               v
                                     FastAPI app.main
                                               |
        +----------------------+---------------+---------------+----------------------+
        |                      |                               |                      |
        v                      v                               v                      v
   bootstrap              middleware                    router registry          RuntimeServices
   startup checks         request id                    app.core.* routers       service factory
   storage config         DB cleanup                    dependency binding       singleton wiring
   DB init facade         auth/JWT/CORS                                         
        |                      |                               |                      |
        +----------------------+---------------+---------------+----------------------+
                                               |
                                               v
                                    vertical capabilities
                                               |
        +------------+-------------+-------------+-------------+-------------+--------+
        |            |             |             |             |             |        |
        v            v             v             v             v             v        v
    projects       files       datasources   connections     auth        logs     tickets
    canvas         upload      local/FTP     DB registry     users       runs     support
    schema         preview     SFTP/SP       inspectors      groups      audit    debt
    lineage        raw/multi   adapters      providers      shares

        +------------+-------------+-------------+-------------+-------------+--------+
        |            |             |             |             |             |
        v            v             v             v             v             v
     estrai      manipola     arricchisci      carica       features       imdf
     SQL gen     transforms   API enrich       DB/REST      ArcGIS FS      bundles
     inspect     source load  merge/group      DDL/modes    facade        publish
     execute                  schema infer     sinks        provider ops  validate

        +------------+-------------+-------------+-------------+-------------+--------+
        |            |             |             |             |             |
        v            v             v             v             v             v
   orchestrator  intermediate scheduler     eseguibili   admin_backups admin_system_status
   run/preview   snapshots    schedules     chains       backups       health admin
   download      artifacts    locks/retry   scanner      restore       status
                                               |
                                               v
                                  horizontal technical engines
                                               |
        +------------------+------------------+------------------+------------------+
        |                  |                  |                  |                  |
        v                  v                  v                  v                  v
 AUTH_SECURITY      CONNECTION_PROVIDER  STORAGE_ARTIFACT   EXECUTION_ORCH.   OBSERVABILITY
 JWT/RBAC/MFA       DB providers         resources/results  canvas/task DAG   health/ready
 ownership          datasources          blob/temp/path     Celery bridge     diagnostics
 tenant policy      ArcGIS/ESRI          object storage     schedule flow     logs
                                               |
                                               v
                                      persistence / runtime
                                               |
        +------------------+------------------+------------------+------------------+
        |                  |                  |                  |                  |
        v                  v                  v                  v                  v
    PostgreSQL           Redis          object storage       filesystem       external systems
    config/tables        Celery broker  S3/MinIO/local      uploads/results  DBs/ArcGIS/HTTP
    results/logs         result backend blobs/artifacts     temp files       SFTP/SharePoint
```

Reading rule:

```text
routers should stay thin
vertical modules own product behavior
horizontal engines own reusable technical rules
shared/db/runtime are the stable cross-module surfaces
external systems sit behind provider/resource/task boundaries
```

## 1. Vista a Strati

```text
Frontend / clients
    |
    v
FastAPI app.main
    |
    +-- bootstrap
    |     +-- validate startup
    |     +-- validate storage config
    |     +-- init DB facade
    |     +-- repair psycopg2 SSL env
    |
    +-- middleware
    |     +-- request id
    |     +-- DB cleanup
    |     +-- JWT/auth
    |     +-- CORS
    |
    +-- RuntimeServices
    |     +-- ProjectService
    |     +-- ConnectionService
    |     +-- DatabaseInspector
    |     +-- QueryGenerator
    |     +-- QueryExecutor
    |     +-- FeaturesService
    |     +-- OrchestratorService
    |     +-- ApiConnectionService
    |     +-- DataSourceService
    |
    +-- router registry
          +-- app.core.<domain>.router
```

```text
app.core
    Product/domain modules. Owns API behavior and domain execution.

app.shared
    Technical contracts shared by multiple modules.

app.db
    PostgreSQL pool, bootstrap, migrations and shared DB primitives. Domain DB
    access lives in `app.core.<domain>.db` where the ownership is specific.

app.runtime
    Runtime service container and router binding.

app.observability
    Health/readiness and bounded diagnostic checks.

workers
    Celery app, worker boot parity, async task boundary.
```

## 2. Module Map

```text
backend/app/core
    admin_backups
    admin_system_status
    arricchisci
    artifacts
    auth
    carica
    connections
    datasources
    eseguibili
    estrai
    features
    files
    imdf
    intermediate
    logs
    manipola
    orchestrator
    projects
    resources
    results
    runtime
    scheduler
    tickets
```

The most important split is:

```text
Pipeline/canvas capabilities
    estrai -> manipola -> arricchisci -> carica
    features / imdf
    intermediate
    orchestrator
    scheduler

Runtime/support capabilities
    auth
    projects
    files
    datasources
    connections
    eseguibili
    logs
    tickets
    admin_backups
    admin_system_status

Horizontal resource/result support
    artifacts
    resources
    results
```

## 3. Horizontal Engines

### AUTH_SECURITY_ENGINE

```text
app/core/auth
    auth.py
    dependencies.py
    owner_dependencies.py
    ownership.py
    disclosure.py
    roles.py
    mfa.py
    auth_middleware.py
    users_router.py
    groups_router.py
    shares_router.py

app/shared
    jwt_token.py
    token_blacklist.py
    authz_helpers.py
    sharing.py
    login_rate_limiter.py
    password_hashing.py
    contracts/auth.py
```

Owns:

```text
JWT
MFA
RBAC
ownership checks
tenant/group visibility
disclosure policy
rate limit fallback
logout blacklist
```

Watch point: routers should not bypass this engine by constructing privileged
resource refs or passing literal `is_superadmin=True` outside allowlisted
boundaries.

### CONNECTION_PROVIDER_ENGINE

```text
app/core/connections
app/core/datasources
app/core/features
app/shared/connections
tests2/integration/provider_config.py
tests2/integration/harness/*
```

Owns:

```text
external DB connection registry
database inspection
datasource adapters
provider-neutral ArcGIS/Esri contracts
neutral ArcGIS provider factory and connection resolver
ArcGIS Feature Service discovery/read/write/admin provider boundary
typed REST write sink foundation for CARICA HTTP destinations
optional DB provider integration config
```

Important shape:

```text
app/shared/connections/esri_types.py
app/shared/connections/esri_system_fields.py
app/shared/connections/arcgis_connection.py
app/shared/connections/arcgis_provider.py
app/shared/connections/arcgis_provider_results.py
app/shared/connections/arcgis_sql.py
app/shared/connections/esri_json.py
app/core/connections/arcgis_provider_factory.py
app/core/connections/arcgis_connection_resolver.py
app/core/features/arcgis_client.py
app/core/features/arcgis_auth.py
app/core/features/arcgis_read.py
app/core/features/arcgis_service_discovery.py
app/core/features/arcgis_feature_write_methods.py
app/core/features/arcgis_rest_write.py
app/core/features/arcgis_feature_operations.py
app/core/features/arcgis_feature_writer.py
app/core/features/arcgis_write_mapping.py
app/core/features/rest_sink.py
app/core/carica/arcgis_write_boundary.py
app/core/carica/rest_config_parser.py
app/core/carica/rest_write_boundary.py
```

ArcGIS/Esri boundary:

```text
app/shared/connections
    ArcGISConnectionInfo
    ArcGISProviderProtocol
    typed provider result objects
    narrower capability protocols:
        discovery
        read
        write
        service admin
    Esri type/system-field helpers
    Esri JSON conversion helpers
    ArcGIS SQL literal/chunk helpers

app/core/connections/arcgis_provider_factory.py
    neutral provider factory:
        create_arcgis_provider()

app/core/connections/arcgis_connection_resolver.py
    DB-backed ArcGIS connection lookup:
        returns ArcGISConnectionInfo + ArcGISProviderProtocol
        used by IMDF without importing app.core.features.*

app/core/features/arcgis_client.py
    concrete ArcGIS provider composition root:
        ArcGISClient public provider type
        mixin assembly for auth, read/query, discovery, and feature writes
        compatibility exports for selected legacy monkeypatch paths

app/core/features/arcgis_auth.py
    ArcGIS authentication and token-aware HTTP helpers:
        token cache
        token refresh
        injected HTTP client protocol
        secret masking

app/core/features/arcgis_read.py
    ArcGIS Feature Service read/query helpers:
        layer query
        schema/read conversion
        ArcGIS SQL literal and chunk helpers

app/core/features/arcgis_service_discovery.py
    ArcGIS portal/service discovery helpers:
        folder discovery/create
        portal item listing/search
        service-name availability
        Feature Service layer listing/schema/query

app/core/features/arcgis_feature_write_methods.py
    ArcGIS layer mutation and add-layer methods:
        add/update/delete features
        applyEdits
        add layer to service

app/core/features/arcgis_client.py
    ArcGIS service admin methods retained on the composed provider:
        create service
        renderer update
        permanent delete / zombie cleanup

app/core/features/arcgis_rest_write.py
    typed ArcGIS REST write/admin contracts:
        create service request
        add/update definition request
        feature add/update batch request
        delete features request
        applyEdits request
        parser functions for edit/delete/applyEdits responses

app/core/features/arcgis_write_mapping.py
    typed write-boundary column mapping for ArcGIS writes

app/core/features/arcgis_feature_operations.py
    feature-domain read/discovery orchestration over ArcGISProviderProtocol:
        test/list/query/read-oriented operations

app/core/features/arcgis_feature_writer.py
    feature-domain write orchestration over ArcGIS provider capabilities:
        delete features
        create service from DataFrame
        add/update/upsert/delete feature rows
        object id matching for update/upsert/delete

app/core/carica/arcgis_write_boundary.py
    CARICA-owned ArcGIS write boundary:
        CaricaArcGISWriteRequest
        CaricaArcGISWriteOutcome
        execute_carica_arcgis_write()
        converts CARICA task inputs/results without exposing provider internals

app/core/imdf/arcgis/*
    IMDF compatibility/policy wrappers over the provider boundary

app/core/features/rest_sink.py
    provider-neutral REST write sink foundation:
        POST / PUT / PATCH
        batch or per-record request mode
        body field mapping
        secret header redaction
        injected HTTP client contract

app/core/carica/rest_config_parser.py
app/core/carica/rest_write_boundary.py
    CARICA-owned REST destination boundary:
        validates CARICA node config
        converts DataFrame rows to REST records
        returns CARICA task result shape
```

ArcGIS boundary status:

```text
closed boundaries:
    IMDF uses shared/core.connections provider contracts
    app.core.features.arcgis_connection_adapter is absent
    resolver lives under app.core.connections
    connection-oriented consumers use create_arcgis_provider()
```

Boundary rule: consumers use `app.core.connections` and `app.shared.connections`.
Concrete ArcGIS provider execution stays under `app.core.features.arcgis_*`.
Direct ArcGIS REST mechanics are provider internals, not router/service
application logic.

External DB driver install expectations:

```text
pymysql
oracledb
pyodbc
```

But SQL Server also needs system ODBC drivers, for example Microsoft ODBC
Driver 18. Python package installation alone is not enough.

Provider integration tests must distinguish:

```text
driver Python installed
system driver installed
provider enabled in provider_config
provider server reachable
```

### STORAGE_ARTIFACT_RESOURCE_ENGINE

```text
app/core/artifacts
    resolver.py
    source_loader.py
    types.py

app/core/resources
    bounded_reader.py
    file_source_blob_access.py
    preview_caps.py
    registry.py
    resource_caps.py
    types.py
    loaders/*

app/core/results
    blob.py
    cleanup.py
    db.py
    orphan_cleanup.py

app/shared/resources
    column_types.py
    external_artifact_processor.py
    file_cleanup.py
    json_path.py
    metadata.py
    path_policy.py
    storage_dispatch.py
    storage_layout.py
    task_scoped_blob_store.py

app/shared
    storage_dispatch.py
    storage_compensation.py
    storage_reconciliation.py
```

Owns:

```text
bounded blob reads
artifact resolution
resource refs
resource metadata
result blobs
file source blobs
object storage dispatch
storage reconciliation
temp materialization policy
preview/resource caps
shared resource path/layout/metadata helpers
JSON path extraction for resource/API payload handling
```

Resource helper status:

```text
app.shared.resources.metadata
    resource metadata value helpers

app.shared.resources.path_policy
    path validation and trusted-root policy helpers

app.shared.resources.storage_dispatch
app.shared.resources.storage_layout
app.shared.resources.task_scoped_blob_store
    shared storage/layout/blob helper surface

app.shared.resources.external_artifact_processor
app.shared.resources.file_cleanup
    shared artifact/file cleanup helpers

app.shared.resources.json_path
    JSON path extraction plus optional GeoJSON-to-WKT conversion

app.shared.resources.column_types
    column type helper bridge surface used by execution/resource code
```

This is one of the strongest horizontalization areas. It is also one of the
most sensitive: inventories for blob consumers, temp materialization and
storage reconciliation must match the code or the guard tests correctly go red.

### EXECUTION_ORCHESTRATION_ENGINE

```text
app/shared/execution
    context.py
    executor_protocol.py
    canvas_graph.py
    canvas_node.py
    canvas_columns.py
    canvas_types_enricher.py
    graph.py
    payloads.py

app/core/orchestrator
app/core/scheduler
app/core/intermediate
workers/tasks
```

Owns:

```text
canvas execution context
typed executor protocol
ExecutionResult boundary
canvas graph/node accessors
pipeline execution order
Celery task handoff
scheduler lock/retry/log policy
intermediate snapshots/artifacts
```

Execution direction:

```text
orchestrator
    dispatches to typed executor protocol

estrai / manipola / arricchisci / carica
    remain vertical executors

shared.execution
    owns common execution language
```

This is the core of future Go portability: fewer dynamic dict contracts,
more explicit value objects and executor boundaries.

### OBSERVABILITY_ENGINE

```text
app/observability/health.py
app/core/admin_system_status
app/core/logs
app/db/log_runs_db_service.py
app/services/log_archive.py
```

Owns:

```text
/health
/ready
bounded Postgres check
bounded Redis check
object storage capability check
pool headroom
migration readiness
system status
run logs
archive visibility
```

Watch point: readiness must remain cheap and bounded. No heavy top-level
imports or unbounded network work in health paths.

### DEPLOYMENT_RUNTIME_ENGINE

```text
app/bootstrap.py
app/main.py
app/runtime/services.py
app/runtime/router_registry.py
app/core/runtime/db_init.py
workers/celery_app.py
docker-compose.yml
backend/Dockerfile
scripts/install.sh
scripts/docker-validate.sh
```

Owns:

```text
startup validation
runtime service container
router binding
worker/beat bootstrap parity
Docker scaffold
install/runbook contracts
optional provider install expectations
```

The Docker test work clarified an important install distinction:

```text
runtime Python packages
    installed by pip

native/system packages
    installed by apt/yum/etc.

external provider services
    configured/enabled separately
```

## 4. Pipeline Flow

```text
User / schedule / API
    |
    +-- HTTP router
    |       |
    |       v
    |   OrchestratorService
    |
    +-- Celery task
            |
            v
        worker-built OrchestratorService

OrchestratorService
    |
    +-- load project canvas
    +-- normalize CanvasGraph
    +-- sort graph
    +-- dispatch by node type
    |
    +-- estrai
    |       +-- query generation / inspection / query execution
    |
    +-- manipola
    |       +-- dataframe transforms / spatial ops / source loading
    |
    +-- arricchisci
    |       +-- API generator/enricher / source loading / merge
    |
    +-- features
    |       +-- concrete ArcGIS provider/client
    |       +-- ArcGIS write mapping
    |       +-- typed REST sink foundation
    |
    +-- imdf
    |       +-- bundle validation / persistence / publish policy
    |       +-- compatibility wrappers over ArcGIS provider operations
    |
    +-- carica
            +-- DB writer / DDL / stage swap / append / upsert
            +-- datasource / ArcGIS / REST destination writes

Intermediate/resource/result layers
    |
    +-- snapshots
    +-- blob/result ids
    +-- bounded materialization
    +-- object storage dispatch
```

## 5. Worker and Beat Flow

```text
FastAPI request
    |
    v
Celery send_task/apply_async
    |
    v
Redis broker/result backend
    |
    v
workers.celery_app
    |
    +-- bootstrap parity with web process
    +-- include task modules
    +-- task execution

workers/tasks
    |
    +-- pipeline_task
    +-- enrichment_task
    +-- carica_task
    +-- scheduler_task
    +-- imdf task
    +-- cleanroom smoke task

Celery beat
    |
    +-- PlenoraBeatScheduler
    +-- Redis leader lock
    +-- schedule execution manager
    +-- cleanup/archive/reconciliation hooks
```

## 6. Domain Modules

| Module | Role | Main files |
|---|---|---|
| `auth` | JWT, MFA, RBAC, ownership, users/groups/shares | `auth.py`, `ownership.py`, routers |
| `projects` | Project CRUD, canvas, schema, lineage | `router.py`, `service.py`, `repository.py` |
| `files` | Upload, preview, raw/multilayer, source read | `router.py`, `service.py`, `upload.py` |
| `datasources` | Local/FTP/SFTP/SharePoint sources | `factory.py`, `service.py`, adapters |
| `connections` | External DB connection CRUD/service | `service.py`, `db.py`, `router.py` |
| `estrai` | SQL generation, inspection, query execution | `query_generator.py`, `executor.py` |
| `manipola` | Transform execution and source loading | `executor.py`, `transforms/`, `source_loading.py` |
| `arricchisci` | API enrichment/generation, response parsing, static parameters and merge flows | `api_connector.py`, `response_parser.py`, `execution_context.py`, split helpers |
| `carica` | DB/datasource/ArcGIS/REST writes and dialect-specific DDL/write modes | `database_writer.py`, `arcgis_write_boundary.py`, `rest_write_boundary.py`, writer modules |
| `features` | ArcGIS feature-service API facade, provider mixins, feature-domain operations, typed ArcGIS REST contracts, REST sink foundation | `features_service.py`, `arcgis_client.py`, `arcgis_auth.py`, `arcgis_read.py`, `arcgis_service_discovery.py`, `arcgis_feature_write_methods.py`, `arcgis_rest_write.py`, `arcgis_feature_operations.py`, `arcgis_feature_writer.py`, `arcgis_write_mapping.py`, `rest_sink.py` |
| `imdf` | IMDF bundle validation/persist/publish policy | `validator.py`, `persist.py`, `arcgis_uploader.py`, `arcgis/*` wrappers |
| `intermediate` | Execution snapshots and artifacts | `artifact_reader.py`, `artifact_writer.py`, `snapshot.py` |
| `orchestrator` | Pipeline execute/preview/download boundary | `service.py`, `router.py`, `task_service.py` |
| `scheduler` | Schedule CRUD, locks, retries, logs, archive | `execution_manager.py`, `policy_engine.py` |
| `eseguibili` | Executable chains and artifact storage | `service.py`, `scanner.py`, `storage.py` |
| `logs` | Run logs and run history APIs | `router.py`, `runs_router.py` |
| `tickets` | Support/debt tickets and attachments | `router.py`, `service.py` |
| `admin_backups` | Backup admin surface | `router.py`, `service.py` |
| `admin_system_status` | Admin system status dashboard | `router.py`, `service.py` |
| `artifacts` | Produced artifact resolution | `resolver.py`, `source_loader.py` |
| `resources` | Resource registry/caps/loaders | `registry.py`, `types.py`, `loaders/` |
| `results` | Result blob facade and cleanup | `blob.py`, `cleanup.py`, `db.py` |

## 7. Where To Change Things

| Change | First place to inspect |
|---|---|
| New endpoint | `app/core/<module>/router.py` |
| New request/response model | `app/core/<module>/models.py` |
| New canvas execution contract | `app/shared/execution/` |
| New node accessor/graph behavior | `app/shared/execution/canvas_node.py`, `canvas_graph.py` |
| New Manipola transform | `app/core/manipola/transforms/` |
| New Arricchisci API behavior | `app/core/arricchisci/api_connector.py`, `app/core/arricchisci/response_parser.py` and split helpers |
| New static API parameter behavior | `app/core/artifacts/static_parameters.py` |
| New file-source path loading behavior | `app/core/files/source_loading.py` |
| New Carica write mode/dialect behavior | `app/core/carica/database_writer.py`, writer modules |
| New CARICA ArcGIS write behavior | `app/core/carica/arcgis_write_boundary.py` |
| New CARICA REST write behavior | `app/core/carica/rest_config_parser.py`, `app/core/carica/rest_write_boundary.py`, `app/core/features/rest_sink.py` |
| New provider-neutral ArcGIS behavior | `app/shared/connections/` |
| New ArcGIS provider factory/resolver behavior | `app/core/connections/arcgis_provider_factory.py`, `app/core/connections/arcgis_connection_resolver.py` |
| New concrete ArcGIS provider operation | `app/core/features/arcgis_client.py` composition root plus the focused provider modules `arcgis_auth.py`, `arcgis_read.py`, `arcgis_service_discovery.py`, `arcgis_feature_write_methods.py`; typed REST contracts live in `app/core/features/arcgis_rest_write.py` |
| New feature-domain ArcGIS orchestration | `app/core/features/arcgis_feature_operations.py` |
| New feature-domain ArcGIS write orchestration | `app/core/features/arcgis_feature_writer.py` |
| New ArcGIS write column mapping behavior | `app/core/features/arcgis_write_mapping.py` |
| New IMDF publish policy | `app/core/imdf/arcgis_uploader.py`, `app/core/imdf/validator.py` |
| New external datasource | `app/core/datasources/` |
| New resource/artifact loader | `app/core/resources/loaders/` and `app/core/artifacts/` |
| New storage reconciliation rule | `app/shared/storage_reconciliation.py` |
| New health/readiness check | `app/observability/health.py` |
| New runtime singleton | `app/runtime/services.py`, `router_registry.py` |
| New async task | `workers/tasks/`, `workers/celery_app.py` |
| New DB table/migration | `app/db/migrations_runner.py`, SQL/migration files |
| New optional provider integration | `tests2/integration/provider_config.py`, `harness/*` |

## 8. Architecture Direction

The horizontalization is visible.

Strong signals:

```text
shared.execution owns canvas execution vocabulary
shared.resources owns metadata/path/storage/JSON resource helper surfaces
shared.connections owns provider-neutral ArcGIS/Esri contracts and SQL helpers
core.connections owns the ArcGIS factory and connection resolver
features owns concrete ArcGIS provider mechanics behind protocol contracts
features splits ArcGIS auth, read/query, service discovery and write methods
features separates feature-domain read orchestration from write orchestration
carica owns its ArcGIS task/request/outcome boundary, not provider internals
carica owns a typed REST destination boundary over the shared REST sink
arricchisci owns response parsing that used to live in generic utils
IMDF owns publish policy and compatibility wrappers, not raw ArcGIS HTTP work
core.resources/core.artifacts/results separate resource/result concerns
runtime/services.py centralizes service construction
router_registry.py centralizes service injection into routers
workers use bootstrap parity with web process
```

Remaining risk areas:

```text
arricchisci
    Still a complex domain. It has been split into helpers, but remains a
    high-density area because source loading, API calls, grouping, retry,
    response parsing, merge and schema inference all meet there.

security / ownership
    Must stay centralized. Literal privileged bypasses or cross-tenant
    special cases should remain heavily guarded.

resource/storage inventories
    Blob/temp/materialization inventories must match the code. These guard
    tests are noisy when stale, but valuable.

documentation guard lane
    Doc guards are opt-in. They should protect true
    operator contracts, not incidental wording.

optional provider integrations
    Drivers and system dependencies must be installable, but provider tests
    must skip cleanly when the provider is disabled or unreachable.

ArcGIS concrete-client ownership
    Provider contracts live in app.shared.connections and provider creation /
    connection lookup live in app.core.connections. Concrete ArcGIS provider
    execution lives in app.core.features.arcgis_client plus focused provider
    modules for auth, read/query, service discovery, and feature write methods.
    Typed write/admin request contracts live in
    app.core.features.arcgis_rest_write.

shared engine leaf boundaries
    app.shared.execution and app.shared.resources are shared engine surfaces.
    They should expose contracts and helpers without depending on vertical
    app.core modules or heavy optional provider libraries.

compatibility surface growth
    Tombstone tests guard non-runtime import paths. Runtime code should not build
    broad compatibility facades around app.services, broad app.db facades, or
    obsolete broad utility modules.
```

## 9. Go Migration Readiness

The architecture is moving in a Go-compatible direction when it:

```text
uses typed value objects instead of loose dicts
uses typed DB row contracts instead of leaking raw dict rows across core code
keeps provider protocols small
centralizes storage/resource access
keeps execution context explicit
keeps DB access behind domain repositories and domain db modules
keeps routers thin
keeps worker task boundaries explicit
```

The main blockers for a future Go migration are not the vertical modules
themselves. The blockers are:

```text
implicit runtime globals
wide dynamic dict payloads
DataFrame-heavy in-memory contracts leaking across boundaries
stringly typed node access
provider-specific logic inside product modules
raw SQL/query logic outside domain DB/repository modules
raw DB dict rows crossing deep into core services
test contracts that pin implementation details instead of behavior
```

The architecture reduces migration risk where execution contracts, resource
metadata, provider contracts and typed DB rows sit behind explicit boundaries.
The ArcGIS provider boundary keeps Feature Service discovery/read/write/admin
operations behind capability protocols.

## 10. Practical Reading Rule

When trying to understand a feature, read in this order:

```text
1. app/core/<module>/router.py
2. app/core/<module>/service.py or executor.py
3. app/shared/* contracts it uses
4. app/core/<module>/db.py if persistence is domain-owned
5. app/db/* only for shared pool/bootstrap/migration primitives
6. workers/tasks/* if async execution is involved
7. tests2/<module> and tests2/shared guards
```

When trying to understand an architectural rule, start from:

```text
docs/architecture/horizontal-engines.md
docs/development/code-map.md
tests2/shared/*guard*.py
tests2/security/unit/*
tests2/integration/provider_config.py
```

## 11. Real Import Dependency Graph

This is the high-level package/import shape. It is intentionally
package-level: the detailed file graph should be generated by tooling, but this
view captures the real dependency direction visible in `backend/app` and
`backend/workers`.

```text
                         app.main / workers.celery_app
                                      |
                                      v
                    +-----------------+-----------------+
                    | bootstrap / runtime / router      |
                    | registry / RuntimeServices        |
                    +-----------------+-----------------+
                                      |
        +-----------------------------+-----------------------------+
        |                             |                             |
        v                             v                             v
 app.core.<domains>              workers/tasks                 app.observability
        |                             |                             |
        |                             v                             |
        |                  orchestrator / task_service              |
        |                             |                             |
        +-------------+---------------+---------------+-------------+
                      |                               |
                      v                               v
              app.shared contracts              domain db modules
                      |                               |
                      v                               v
              storage/auth/exec/               PostgreSQL pool,
              provider contracts               shared app.db primitives,
                                               typed row contracts
                      |
                      v
          external systems and providers
```

Important import rules:

```text
core domains -> shared contracts
core domains -> own db/repository modules
runtime -> core service constructors
workers -> task entrypoints + orchestrator/service facades
features -> shared.connections provider contracts
features -> arcgis_feature_operations / arcgis_feature_writer split
features -> arcgis_rest_write typed ArcGIS REST contracts
features -> rest_sink typed HTTP write primitive
imdf -> shared.connections provider contracts
imdf -> core.connections.arcgis_connection_resolver
features -> core.connections.arcgis_provider_factory
carica -> arcgis_write_boundary -> ArcGISFeatureWriter protocol
carica -> rest_write_boundary -> RestFeatureSink
arricchisci -> core.arricchisci.response_parser
arricchisci -> core.artifacts.static_parameters
arricchisci -> core.files.source_loading
```

ArcGIS dependency zoom:

```text
shared.connections
    arcgis_connection.py
    arcgis_provider.py
    arcgis_provider_results.py
    arcgis_sql.py
    esri_json.py
    esri_types.py
    esri_system_fields.py
          ^
          |
core.connections
    arcgis_provider_factory.py
    arcgis_connection_resolver.py
          ^
          |
features.arcgis_client
    concrete provider composition root
          ^
          |
features.arcgis_auth
features.arcgis_read
features.arcgis_service_discovery
features.arcgis_feature_write_methods
    focused provider internals for auth, read/query, discovery and writes
          ^
          |
features.arcgis_rest_write
    typed REST request/result contracts for ArcGIS writes/admin ops
          ^
          |
features.arcgis_feature_operations
    read/discovery orchestration
          ^
          |
features.arcgis_feature_writer
    write/service-creation orchestration
          ^
          |
features.features_service / routes

carica_task
          |
          +------------------------------+
          |                              |
          v                              v
carica.arcgis_write_boundary
    typed CARICA request/outcome boundary
          |
          v
ArcGISFeatureWriter protocol

carica.rest_config_parser / rest_write_boundary
    typed CARICA REST request/outcome boundary
          |
          v
features.rest_sink.RestFeatureSink

imdf.validator / imdf.arcgis_uploader
          |
          +-- imdf.arcgis.* compatibility/policy wrappers
          |
          +-- core.connections.arcgis_connection_resolver
```

The import graph is improving when:

```text
product modules import fewer product-module internals
shared modules stay leaf-like and do not import core domains
    compatibility modules stay as wrappers instead of becoming owners
adapter residuals are guarded and named explicitly
DB row dict compatibility stays explicit at public edges
```

DB boundary typing zoom:

```text
app.db.config.* / app.core.results.db / app.core.connections.db
          |
          v
immutable row contracts
    ProjectRow / ProjectDetailRow / ProjectDependencyRow
    ResultMetadataRow
    ScheduleRow
    ConnectionConfigRow / ArcGISConnectionRow
    TicketRow / TicketAttachmentRow
    EseguibileRow / EseguibileSummaryRow / EseguibileOutputRow
    AdminSystemStatus domain models
          |
          v
core services and resolvers consume attributes, not raw mapping lookups
          |
          v
legacy dict wrappers remain only as explicit public compatibility edges
```

## 12. Temporal ETL Run Sequence

This view shows time/order, not imports.

```text
HTTP execute request OR scheduled run OR worker retry
    |
    v
auth dependency / CurrentUser
    |
    v
project visibility and canvas resource checks
    |
    v
task creation or direct orchestrator call
    |
    +-- HTTP path
    |       router -> OrchestratorService.execute_pipeline
    |
    +-- Celery path
            workers.tasks.pipeline_task
                -> build OrchestratorService with worker-local services
                -> execute_pipeline

OrchestratorService.execute_pipeline
    |
    +-- load project metadata/canvas
    +-- select executor by project_type
    |       estrai
    |       manipola
    |       arricchisci
    |
    +-- executor.execute(project_id, canvas, ...)
    |       |
    |       +-- read upstream resources
    |       +-- execute node/domain logic
    |       +-- write intermediate snapshots/artifacts
    |       +-- save final result blob/metadata
    |
    +-- normalize raw dict -> ExecutionResult
    +-- create PipelineArtifact
    |
    +-- if success and carica node exists
            create carica task(s)
                |
                v
            workers.tasks.carica_task
                +-- materialize upstream artifact/result
                +-- dispatch:
                |       datasource write
                |       DB write
                |       ArcGIS write
                |       REST write
                +-- write logs/progress/result
```

Arricchisci has an extra async-style subflow:

```text
execute_enrichment_task
    |
    +-- initialize ArricchisciExecutionContext
    +-- compute source/result/static/API node order
    +-- load project_source / file_source / staticValues
    +-- run api_enrich nodes, including parallel groups
    +-- persist output result blob
    +-- if output is IMDF zip:
    |       extract bundle metadata
    |       persist IMDF bundle
    |       optionally save bundle to IMDF storage
    |
    +-- if carica nodes depend on output:
            create downstream carica tasks
```

## 13. Storage And Blob Lifecycle

The storage plane has three main kinds of payload:

```text
file source blobs
result blobs
intermediate artifacts / snapshots
```

Write path for result blobs:

```text
executor produces DataFrame/file/bytes
    |
    v
app.utils.dataframe serialization helpers
    |
    v
app.utils.result_store.save_result_blob*
    |
    +-- policy=db
    |       enforce DB hard cap
    |       insert BYTEA into results_blobs
    |
    +-- policy=object_storage
    |       upload bytes/file to runtime object storage
    |       insert DB metadata with storage_key
    |       if DB insert fails:
    |           compensating_delete(storage_key)
    |
    +-- policy=hybrid
            choose db vs object_storage by size threshold
```

Read path:

```text
consumer asks for result/resource
    |
    v
ResourceRef / ArtifactResolver / result blob loader
    |
    +-- DB-backed blob
    |       read BYTEA through bounded reader/caps
    |
    +-- object-storage-backed blob
            resolve storage_key
            download through runtime object storage provider
            return bytes or bounded temp materialization
```

Cleanup and reconciliation:

```text
normal delete path
    |
    +-- delete DB metadata
    +-- delete object storage key when present
    +-- on uncertain failure:
            record storage_reconciliation row

Celery beat
    |
    +-- fetch pending reconciliation rows
    +-- check whether storage_key is still referenced
    +-- if unreferenced:
            compensating_delete(storage_key)
            mark orphan_deleted/done
    +-- if uncertain:
            retry / mark failed / eventually lost
```

Critical invariants:

```text
DB row is the source of truth for result metadata
storage_key means payload lives outside DB
large reads must go through bounded readers/caps
temp materialization is explicit and inventoried
object-storage deletes must be compensating and observable
```

## 14. Multi-Tenant Security Flow

The security path starts before the domain code.

```text
request
    |
    v
JWT/auth middleware or FastAPI dependency
    |
    v
CurrentUser
    |-- id
    |-- role
    |-- group_id
    |-- is_active / permissions
    |
    v
role dependency
    |-- require_user_or_above
    |-- require_not_esecutore
    |-- require_admin
    |-- require_superadmin
    |
    v
domain router
    |
    +-- load resource metadata
    +-- assert ownership or visibility
    +-- fail closed on lookup/access errors
```

Visibility model:

```text
owned resource
    resource.group_id == current_user.group_id

shared resource
    row in sharing table for current_user.group_id or magic all group

superadmin
    may bypass visibility only through allowed guard paths

projects
    stricter than generic sharing in several paths:
    ownership/visible-project checks are explicit
```

Canvas save/execute security:

```text
project router / orchestrator router
    |
    +-- assert project access/visibility
    +-- parse CanvasGraph
    +-- collect referenced resources:
    |       file_sources
    |       project_source projects
    |       ArcGIS connections
    |       API connections
    |
    +-- compare refs against visible_resource_ids / strict project ids
    +-- reject cross-tenant references before execution
```

Async task security:

```text
HTTP caller creates task with user/run context
    |
    v
worker task
    |
    +-- recheck_user_authorized when user context is present
    +-- propagate expected_group_id into source loaders/execution context
    +-- fail closed if group lookup/context init fails
```

Watch points:

```text
no literal is_superadmin=True in product code outside allowlists
no router should construct privileged ResourceRef casually
cross-tenant failures should be 403/404 style, not leaked 500 details
async tasks must not trust stale group context blindly
```

## 15. Canvas Execution DAG

There are two related DAGs:

```text
project canvas DAG
    frontend/domain nodes and edges persisted with the project

runtime execution DAG
    normalized graph/order consumed by executors and worker tasks
```

Common canvas node families:

```text
source nodes
    project_source
    file_source
    staticValues
    datasource_node

compute nodes
    estrai query/input nodes
    manipola transform nodes
    api_enrich

publish/write nodes
    result
    carica
    carica_arcgis
    carica rest destination
    imdf_publisher
```

Generic DAG shape:

```text
source nodes
    |
    v
compute/enrich/transform nodes
    |
    v
result node
    |
    +-- downloadable result blob
    |
    +-- carica / datasource write / ArcGIS write
    |
    +-- IMDF bundle publish
```

Orchestrator-level dispatch:

```text
project_type
    |
    +-- estrai      -> EstraiExecutor
    +-- manipola    -> ManipolaExecutor
    +-- arricchisci -> EnrichExecutor
```

Arricchisci runtime order:

```text
project_source / file_source / staticValues
    |
    v
api_enrich node(s)
    |
    +-- sequential path when dependent
    +-- parallel groups when independent
    |
    v
result
    |
    +-- result blob
    +-- optional IMDF bundle extraction
    +-- optional downstream carica task
```

Carica runtime branch:

```text
upstream artifact/result
    |
    v
materialize DataFrame
    |
    +-- datasource_node
    |       DataSourceService write
    |
    +-- carica with dest_type=db
    |       CaricaExecutor -> database writer
    |
    +-- carica_arcgis or dest_type=arcgis
            ArcGISWriteMappingContract
            FeaturesService / ArcGIS provider
```

What a fully generated DAG should include:

```text
node id
node type
module owner
input edges
output edges
resource refs
result/artifact ids
async task boundaries
tenant/security checks applied before execution
```

## 16. Runtime Inventory

This section is the concrete runtime inventory. It answers: what is active,
which node/source/destination types are real, and where execution
artifacts are written.

ROUTERS:

```text
Composition root:
    backend/app/main.py

Active FastAPI routers mounted with prefix /api:
    auth_router                  -> app.core.auth.auth                /auth
    users_router                 -> app.core.auth.users_router        /auth/users
    groups_router                -> app.core.auth.groups_router       /auth/groups
    shares_router                -> app.core.auth.shares_router       /shares
    connections_router           -> app.core.connections.router       connections API
    projects_router              -> app.core.projects.router          /projects
    files_router                 -> app.core.files.router             /files
    functions_router             -> app.core.estrai.router            /functions
    transforms_router            -> app.core.manipola.api.transforms  /transforms
    enrichment_router            -> app.core.arricchisci.router       /enrichment
    api_connections_router       -> app.core.arricchisci.router       api-connections
    templates_router             -> app.core.arricchisci.router       /templates
    enrich_router                -> app.core.arricchisci.router       /projects
    carica_router                -> app.core.carica.router            /carica
    features_router              -> app.core.features.routes          /features
    schedules_router             -> app.core.scheduler.router         /schedules
    pipeline_router              -> app.core.orchestrator.router      /projects
    tasks_router                 -> app.core.orchestrator.router      /tasks
    logs_router                  -> app.core.logs.router              /logs
    runs_router                  -> app.core.logs.runs_router         /pipeline-runs
    datasources_router           -> app.core.datasources.router       /datasources
    intermediate_router          -> app.core.intermediate.router      /runs
    eseguibili_router            -> app.core.eseguibili.router        /eseguibili
    imdf_router                  -> app.core.imdf.router              /imdf
    admin_backups_router         -> app.core.admin_backups.router     /admin/backups
    admin_system_status_router   -> app.core.admin_system_status      /admin/system-status
    tickets_router               -> app.core.tickets.router           /tickets

Root/health endpoints live directly in app.main:
    /
    /health
    /ready
```

TASKS:

```text
Celery app:
    backend/workers/celery_app.py
    app name: dataflow_tasks
    broker/backend: Redis

Included task modules:
    workers.tasks.enrichment_task
    workers.tasks.carica_task
    workers.tasks.schedule_task
    workers.tasks.pipeline_task
    workers.tasks.imdf_publish_task
    workers.tasks.cleanroom_task
    workers.tasks.cleanroom_beat_task

Active task names:
    tasks.execute_enrichment
        Executes Arricchisci API enrichment canvas.

    carica.execute
        Executes CARICA writes to DB, datasource, ArcGIS, or REST destination.

    pipeline.execute
        Executes full project pipeline asynchronously.

    pipeline.preview
        Executes pipeline preview asynchronously.

    workers.tasks.schedule_task.schedule_pipeline_task
        Executes one scheduler tick/manual scheduled run.

    workers.tasks.schedule_task.beat_heartbeat
        Writes Beat heartbeat into PostgreSQL.

    tasks.execute_imdf_publish
        Publishes an IMDF bundle to ArcGIS.

    cleanroom.ping
        Deterministic worker enqueue/dequeue smoke task.

    cleanroom.beat_ping
        Optional Beat smoke task, registered only when
        PLENORA_CLEANROOM_BEAT_PING=1.
```

RUNTIME SERVICES:

```text
RuntimeServices fields:
    project_service          -> ProjectService
    connection_service       -> ConnectionService
    database_inspector       -> DatabaseInspector
    query_generator          -> QueryGenerator
    query_executor           -> QueryExecutor
    features_service         -> FeaturesService
    orchestrator             -> OrchestratorService
    api_connection_service   -> ApiConnectionService singleton
    datasource_service       -> DataSourceService singleton

Construction order:
    ProjectService
    ConnectionService
    DatabaseInspector
    QueryGenerator
    QueryExecutor
    FeaturesService
    OrchestratorService
    ApiConnectionService compatibility singleton
    DataSourceService compatibility singleton
```

EXECUTORS:

```text
Project module dispatch:
    project_type=estrai       -> EstraiExecutor
    project_type=manipola     -> ManipolaExecutor
    project_type=arricchisci  -> EnrichExecutor

Pipeline entry:
    OrchestratorService.execute_pipeline(project_id)
        -> project metadata decides executor
        -> executor returns ExecutionResult-like dict
        -> PipelineArtifact wraps result
        -> optional CARICA task launch if carica node exists

Executor responsibilities:
    EstraiExecutor
        DB/ArcGIS source query, SQL generation, feature_source handling,
        result node save.

    ManipolaExecutor
        project/file/static source loading, transform nodes, multi_join,
        intermediate snapshots, final result save.

    EnrichExecutor / ApiConnectionService
        project/file/static source loading, api_enrich nodes,
        generator/enricher dispatch, Arricchisci-owned response parsing,
        result save.

    CARICA task
        post-pipeline destination write; not part of the synchronous
        OrchestratorService executor dispatch table. ArcGIS writes go through
        app.core.carica.arcgis_write_boundary and the ArcGISFeatureWriter
        protocol. REST writes go through app.core.carica.rest_write_boundary
        and the typed RestFeatureSink.

    IMDF publish task
        IMDF bundle publish lifecycle; separate Celery task path.
```

CANVAS NODE TYPES:

```text
Core source nodes:
    project_source
    file_source
    staticValues
    static_values       compatibility spelling in SourceRef factory

EstrAI nodes:
    datasource_node     DB query source used by query generator/CARICA
    feature_source      ArcGIS feature source used by EstraiExecutor
    result

Manipola nodes:
    transform
    multi_join
    result

Arricchisci nodes:
    api_enrich
    result
    imdf_publisher      handled in enrichment task path

Output/destination nodes:
    result
    datasource_node     used by CARICA datasource write branch
    carica_arcgis       used by CARICA ArcGIS write branch
    carica_node/rest    CARICA node with dest_type=rest, POST/PUT/PATCH sink

Project types using canvas:
    estrai
    manipola
    arricchisci
```

DATA SOURCES:

```text
Canvas/source-level data sources:
    project_source      latest artifact from another project
    file_source         uploaded File Source blob
    staticValues        inline static rows/fields
    datasource_node     external DB query source
    feature_source      ArcGIS Feature Service source
    api_enrich          HTTP/API generated or enriched source/output

External DB connection types:
    postgres
    postgresql          accepted by QueryExecutor
    mysql
    mssql
    oracle
    sqlite              model/query-executor support; not every writer path uses it

Datasource backends:
    local
    ftp
    sftp
    sharepoint

File/blob sources:
    uploaded file sources
    multilayer file sources
    raw file sources
    result_blob resources
    file_source_blob resources

Provider/API sources:
    ArcGIS Online / Enterprise / Server
    REST API connections
    REST file API connections
    executable API connections
```

DESTINATIONS:

```text
Pipeline result destinations:
    result_blob table/object-storage dispatch
    filesystem temp path only as transient staging
    latest project result metadata

Intermediate destinations:
    intermediate run table
    node metadata
    optional node artifact snapshots
    full_artifact / preview_only / metadata_only policy modes

CARICA DB destinations:
    postgres/postgresql
    mysql/mariadb
    mssql
    oracle

CARICA write modes:
    create
    append
    replace
    update
    upsert
    truncate_insert

CARICA datasource destinations:
    local
    ftp
    sftp
    sharepoint

CARICA REST destinations:
    absolute http/https endpoint
    methods: POST, PUT, PATCH
    batch modes: batch, per_record
    typed headers with secret redaction
    body mapping from DataFrame columns to JSON fields
    success status configuration

ArcGIS destinations:
    Feature Service read/discovery through FeaturesService
    Feature Service writes through ArcGISFeatureWriteService
    CARICA ArcGIS write boundary -> ArcGISFeatureWriter protocol
    IMDF publish to ArcGIS Feature Service

Other persisted outputs:
    IMDF bundle storage
    eseguibili output storage
    ticket attachments
    backup artifacts
```

EXECUTION FLOW:

```text
source -> transform -> artifact -> output

project_source
    -> ArtifactResolver.materialize_latest
    -> DataFrame
    -> transform/api_enrich/result

file_source
    -> ResourceRef(kind=file_source_blob)
    -> FileSourceBlobLoader / bounded reader
    -> DataFrame
    -> transform/api_enrich/result

staticValues
    -> SourceRef(static_fields)
    -> DataFrame
    -> transform/api_enrich/result

datasource_node
    -> ConnectionService / QueryGenerator / QueryExecutor
    -> SQL query result
    -> save_result_blob / save_pipeline_result_outcome
    -> result_blob metadata + payload

feature_source
    -> FeaturesService / ArcGIS provider
    -> Feature Service query result
    -> save_pipeline_result_outcome
    -> result_blob metadata + payload

transform / multi_join
    -> ManipolaExecutor
    -> DataFrame
    -> optional intermediate writer/snapshot
    -> save_pipeline_result_outcome at final result

api_enrich
    -> ApiConnectionService / EnrichmentExecutor
    -> app.core.arricchisci.response_parser for configurable JSON parsing
    -> app.core.artifacts.static_parameters for typed static API parameters
    -> app.core.files.source_loading for reusable file_source_id resolution
    -> DataFrame or file output
    -> save_result_blob or save_pipeline_result_outcome
    -> result_blob metadata + payload

result
    -> final node selection
    -> format resolution
    -> result_blob save
    -> optional CARICA dispatch

carica destination
    -> execute_carica_task
    -> DB writer OR datasource backend OR CARICA ArcGIS write boundary OR REST write boundary
    -> ArcGISFeatureWriter protocol for ArcGIS Feature Service writes
    -> RestFeatureSink for HTTP POST/PUT/PATCH writes
    -> task result + run log

imdf_publisher
    -> execute_imdf_publish_task
    -> IMDF validator/uploader
    -> ArcGIS Feature Service output
```

WRITERS:

```text
Result blob writers:
    app.utils.result_store
        save_result_blob
        save_result_blob_from_bytes
        save_pipeline_result
        save_pipeline_result_outcome

    app.db.query_executor
        query output -> save_result_blob / save_pipeline_result_outcome

    app.core.features.features_service
        ArcGIS query-derived outputs -> save_pipeline_result_outcome

    app.core.features.arcgis_client
    app.core.features.arcgis_auth
    app.core.features.arcgis_read
    app.core.features.arcgis_service_discovery
    app.core.features.arcgis_feature_write_methods
        concrete ArcGIS provider execution and provider-internal HTTP calls.

    app.core.features.arcgis_feature_writer
        ArcGIS feature write/service-create orchestration.

    app.core.features.arcgis_rest_write
        typed ArcGIS REST write/admin request and response contracts used by
        the concrete ArcGIS provider.

    app.core.carica.arcgis_write_boundary
        CARICA ArcGIS task request/outcome conversion; writes through
        ArcGISFeatureWriter protocol.

    app.core.features.rest_sink
        typed REST write sink foundation; no owned environment reads and no
        owned network stack, uses injected HTTP client.

    app.core.carica.rest_config_parser
    app.core.carica.rest_write_boundary
        CARICA REST destination config parsing and task result conversion.

    app.core.manipola.executor
        final transform output -> save_pipeline_result_outcome

    app.core.arricchisci.executor
    app.core.arricchisci.api_connection_support
    workers.tasks.enrichment_task
        enrichment/generator output -> save_result_blob or
        save_pipeline_result_outcome

    app.core.projects.service
        legacy project result save wrapper.

Domain typed-row writers/bridges:
    app.db.tickets_db_service
        TicketRow / TicketAttachmentRow public compatibility wrappers.

    app.core.eseguibili.db
        EseguibileRow / summary/output rows with explicit legacy conversion.

    app.core.admin_system_status.models
        typed admin status/readiness/resource payloads.

Broad facades outside runtime:
    app.services, app.utils.response_parser and the broad app.db service
    facades are not runtime architecture surfaces. Tombstone tests guard these
    paths so they do not return.

Artifact/resource loaders:
    app.core.artifacts.resolver
    app.core.artifacts.static_parameters
    app.core.files.source_loading
    app.core.resources.registry
    app.core.resources.loaders.result_blob
    app.core.resources.loaders.file_source_blob

Intermediate writers:
    app.core.intermediate.db
        run rows and node metadata.

    app.core.intermediate.writer
        write_node and close_run.

    app.core.intermediate.snapshot
        checkpoint/snapshot decisions.

    app.core.intermediate.artifact_writer
        persisted node artifacts.

    workers.tasks.pipeline_task
    workers.tasks.enrichment_task
    workers.tasks.carica_task
        open/close run logs and intermediate run lifecycle.

Other artifact writers:
    app.core.imdf.storage
        IMDF bundle zip/meta storage.

    app.core.eseguibili.storage
        executable task output files.

    app.core.tickets.router/service
        ticket attachments.
```

SECURITY BEFORE EXECUTION:

```text
HTTP router boundary:
    get_current_user / CurrentUser dependencies
    require_not_esecutore for pipeline execute/preview
    role checks for admin/superadmin-only routers
    project group visibility checks before execute/preview/download
    share/ownership checks before exposing shared resources

Canvas/project boundary:
    projects router validates project_source/file_source/arcgis_connection
    accessibility for canvas resources.

Async worker pickup:
    app.shared.async_user_recheck.recheck_user_authorized
        pipeline.execute
        pipeline.preview
        carica.execute
        tasks.execute_enrichment

Resource boundary:
    ArtifactResolver validates expected project/group binding.
    ResourceRef carries expected_group_id / expected_project_id.
    file_source_blob and result_blob loaders enforce tenant binding.

Provider boundary:
    ConnectionService checks external DB connection ownership.
    ArcGIS resolver loads stored connection through core.connections.
    Features/IMDF use ArcGISProviderProtocol instead of raw provider details.

CARICA boundary:
    write preflight validates table/mode/match columns/conflicts.
    scheduler destination pre-check verifies destination connection when enabled.

Operational guards:
    memory guard before heavy pipeline execution
    bounded readers for blob/file materialization
    idempotency checks for stable run_id/task reruns
    MIME/executable validation for file uploads and previews
```

## 17. Integration And Sync Runtime Reality

This section answers a specific architectural question: is the backend a
bidirectional integration/synchronization platform, or mainly an ETL
read-and-publish platform?

Short answer:

```text
Classification:
    D) hybrid architecture

Practical reading:
    much closer to
        A) ETL/read-and-publish platform
    than to
        B) bidirectional integration platform
        C) operational sync runtime
```

The backend has real remote write capabilities: CARICA destinations, ArcGIS and
the typed REST sink. It does not have a general external
synchronization runtime with durable cursors, external operation logs,
dead-letter/replay queues, or provider-neutral sync contracts.

### 17.1 Existing Capabilities

```text
READ / EXTRACT
    external DB query sources:
        postgres/postgresql, mysql, mssql, oracle, sqlite

    API read/enrichment:
        Arricchisci API generator/enricher
        GET/POST outbound calls
        JSON/GeoJSON response parsing
        pagination: none, offset, cursor, link

    ArcGIS read:
        Feature Service discovery
        layer schema
        query_features
        query_oid_by_key

WRITE / PUBLISH
    CARICA DB destinations:
        create, append, replace, update, upsert, truncate_insert

    CARICA datasource destinations:
        local, ftp, sftp, sharepoint file write/delete paths

    CARICA REST destinations:
        POST, PUT, PATCH to absolute http/https endpoints
        batch or per-record request emission
        body mapping from DataFrame rows to JSON payloads
        redacted secret headers
        injected HTTP client boundary

    ArcGIS Feature Service writes:
        addFeatures
        updateFeatures
        deleteFeatures
        create service
        add layer
        update renderer

    IMDF publish:
        create/populate Feature Service
        append existing Feature Service
        upsert via deleteFeatures + addFeatures
        update via OBJECTID resolution + updateFeatures

RUNTIME STATE
    Celery task state
    intermediate runs/node snapshots
    result blobs
    scheduler state/logs/catchup
    storage reconciliation log for internal blob/object cleanup
```

### 17.2 API Writeback Boundary

```text
Concrete writeback exists here:

    app.core.features.routes
        POST /features/write
            operation: add | update | upsert | delete
            source: input_path or latest project artifact
            security: ArcGIS connection access + project visibility

        POST /features/create-service
            creates ArcGIS Feature Service from pipeline/file artifact

    app.core.features.arcgis_client
    app.core.features.arcgis_auth
    app.core.features.arcgis_read
    app.core.features.arcgis_service_discovery
    app.core.features.arcgis_feature_write_methods
        add_features      -> POST layer/addFeatures
        update_features   -> POST layer/updateFeatures
        delete_features   -> POST layer/deleteFeatures
        apply_edits       -> POST layer/applyEdits
        create/list/find service and folder operations

    app.core.carica.arcgis_write_boundary
        converts CARICA task request/outcome to ArcGIS write service calls

    app.core.features.rest_sink
        typed REST write sink foundation for row/feature payloads

    app.core.carica.rest_config_parser
    app.core.carica.rest_write_boundary
        parses CARICA REST node config and writes DataFrame rows through
        RestFeatureSink

    app.core.imdf.arcgis.publish_ops
        append/update/upsert IMDF layers into ArcGIS Feature Services
```

Important boundary:

```text
Arricchisci outbound HTTP is not the general writeback boundary.

Arricchisci can call GET/POST APIs and parse responses, but the owned runtime
behavior is:

    request external API
        -> parse response
        -> produce/enrich DataFrame
        -> save artifact/result

It does not own:
    remote record mutation lifecycle
    durable remote operation state
    update/delete orchestration for arbitrary APIs
    provider-neutral sync semantics

Generic-ish REST writeback lives in CARICA:
    dest_type=rest
        -> rest_config_parser
        -> rest_write_boundary
        -> RestFeatureSink

This is still destination write/publish, not stateful synchronization.
```

### 17.3 Stateful Synchronization Status

```text
Exists, but internal:
    scheduler last_execution / next_execution / catchup
    intermediate checkpoint snapshots
    Celery task state
    storage_reconciliation_log for internal object-storage compensation

Exists, but runtime-only:
    Arricchisci cursor pagination during one API generator execution
    ArcGIS OBJECTID lookup during one update/upsert operation

Missing as durable external sync state:
    sync_state table
    provider cursor/checkpoint table
    last_seen / watermark persistence
    external sync token persistence
    CDC/event offset persistence
    source-target reconciliation queue
    external operation ledger
```

Interpretation:

```text
The system remembers ETL runs and artifacts.
It does not remember the external system's data-change position.
```

### 17.4 Integration Engine Capabilities

```text
Capability                      Status
------------------------------  ---------------------------------------------
Run idempotency                 Present for stable run_id task reruns.
Destination write modes         Present in CARICA DB, REST, and ArcGIS publish paths.
Conflict policy                 Present in CARICA DB/write preflight areas.
ArcGIS update/upsert            Present, provider-specific.
REST POST/PUT/PATCH sink        Present, CARICA-owned, stateless.
Retry for sync writes           Mostly absent; CARICA intentionally fail-fast.
Dead-letter queue               Not present as application sync primitive.
Replay queue                    Not present for external operations.
Compensating external ops       Not present as provider-neutral capability.
Reconciliation                  Internal storage cleanup only.
Conflict resolution runtime     Not present beyond local write-mode policies.
External state tracking         Not present.
```

The clearest design signal is CARICA's task policy:

```text
carica.execute
    max_retries = 0

Reason:
    append + per_batch writes can duplicate already-written rows after a
    partial failure. The task fails visibly instead of retrying silently.
```

That is correct for ETL safety, and it also marks the runtime boundary: this is
not a sync engine. A sync runtime needs durable operation ids,
idempotent provider-level writes, retry state, replay, and reconciliation.

### 17.5 Event And Webhook Support

```text
Exists:
    Celery asynchronous task execution
    Celery Beat schedule ticks
    Redis broker/result backend
    task status polling
    internal audit/log events

Not found as runtime capability:
    webhook ingestion endpoint family
    external event consumer
    provider event subscriptions
    event deduplication table
    event replay queue
    event-driven orchestration DAG
```

Interpretation:

```text
The backend is async internally.
It is not event-driven by external systems.
```

### 17.6 Connector Model Reality

```text
DatasourceInterface:
    list_files
    file_info
    read_file
    write_file
    delete_file
    file_exists

Supported datasource backends:
    local
    ftp
    sftp
    sharepoint

ArcGIS provider protocols:
    ArcGISDiscoveryProviderProtocol
    ArcGISReadProviderProtocol
    ArcGISWriteProviderProtocol
    ArcGISServiceAdminProviderProtocol

REST write sink:
    RestSinkMethod: POST, PUT, PATCH
    RestSinkBatchMode: batch, per_record
    RestSinkHttpClient protocol
    RestFeatureSink.write_records(records)
```

This means connectors are not purely read-only. There are concrete write paths.

But the model is not yet a full provider-neutral sync connector system:

```text
Missing generic capability contract:
    can_read
    can_write
    can_update
    can_delete
    supports_delta
    supports_cursor
    supports_webhook
    supports_replay
    supports_idempotent_mutation
```

ArcGIS is the most advanced provider boundary. The REST sink is a horizontal
write primitive, but it is intentionally stateless and injected:
it does not own a provider registry, auth negotiation, cursor persistence, or
operation replay. Datasources and DB connections are more static: their
capabilities are mostly implied by type and code path, not negotiated through a
shared integration contract.

### 17.7 ARRICCHISCI Ownership

```text
ARRICCHISCI owns:
    API connection CRUD/test
    API generator
    API enricher
    batch enrichment
    REST file handling
    response parsing
    JSON path mapping
    pagination within one execution
    DataFrame/result production

ARRICCHISCI does not own:
    outbound mutation lifecycle
    remote update/delete orchestration
    sync checkpoint persistence
    provider operation replay
    conflict resolution against remote state
```

Operational classification:

```text
ARRICCHISCI = read/enrichment/parsing domain
ARRICCHISCI != operational sync domain
```

### 17.8 CARICA Ownership

```text
CARICA owns:
    post-pipeline destination writes
    DB DDL/write modes
    datasource file writes
    ArcGIS destination write boundary
    REST destination write boundary
    REST body mapping / batch mode / method validation
    write preflight and conflict policy
    idempotent run guard when run_id is stable

CARICA does not own:
    persistent source-target sync state
    provider cursor/watermark state
    external operation ledger
    external dead-letter/replay state
    general reconciliation loop
    bidirectional consistency guarantees
```

Operational classification:

```text
CARICA = destination write/publish engine, including REST sinks
CARICA != full sync/reconciliation engine
```

### 17.9 Storage And Runtime State Gap

```text
Tables/state that exist:
    projects
    connections
    api_connections
    arcgis_connections
    datasources
    file_sources
    runs
    results
    log_runs
    logs
    schedules
    schedule_logs
    scheduler_locks
    beat_heartbeat
    node_meta
    node_rows
    storage_reconciliation_log

Tables/state not present as first-class sync primitives:
    sync_states
    connector_checkpoints
    external_operation_logs
    webhook_events
    dead_letters
    remote_conflicts
    replay_queue
    provider_watermarks
```

The existing `storage_reconciliation_log` is important, but its scope is
internal storage compensation:

```text
DB row/blob/object storage cleanup
    yes

external API/database/source synchronization
    no
```

### 17.10 Final Classification Matrix

```text
A) ETL/read-and-publish platform
    Mostly true.
    Strong evidence: extract -> transform/enrich -> result blob -> CARICA DB/
    datasource/REST/ArcGIS publish/write paths.

B) bidirectional integration platform
    Partially true only for specific providers and operations.
    Strongest cases: ArcGIS add/update/delete/create-service, IMDF publish,
    and CARICA REST POST/PUT/PATCH sink.
    Missing: generic connector sync contracts.

C) operational sync runtime
    Not true.
    Missing: durable remote checkpoints, external operation ledger, event intake,
    dead-letter/replay, reconciliation and conflict resolution runtime.

D) hybrid architecture
    Best classification.
    The architecture has real write/publish capability and some horizontal
    provider/resource boundaries, but the runtime behaves primarily as
    ETL/read-and-publish.
```

Practical rule for future architecture reviews:

```text
If a feature only writes a destination once per run, classify it as CARICA/
publish behavior.

If a feature persists external position, external operation ids, replayable
mutation attempts, and conflict/reconciliation state, classify it as sync
runtime behavior.

Today most backend features are in the first category.
```

## 18. Visual Summary

```text
                           +--------------------+
                           | FastAPI / Celery   |
                           +----------+---------+
                                      |
                                      v
                    +-----------------+-----------------+
                    | RuntimeServices / router registry |
                    +-----------------+-----------------+
                                      |
        +-----------------------------+-----------------------------+
        |                             |                             |
        v                             v                             v
+---------------+             +---------------+             +---------------+
| product APIs  |             | pipeline core |             | admin/support |
| auth/projects |             | orchestration |             | logs/tickets  |
| files/sources |             | scheduler     |             | backups/status|
+-------+-------+             +-------+-------+             +-------+-------+
        |                             |                             |
        +-----------------------------+-----------------------------+
                                      |
                                      v
          +---------------------------+---------------------------+
          |              horizontal engines                      |
          | auth/security, execution, resources, connections,    |
          | storage/reconciliation, observability, deployment    |
          +---------------------------+---------------------------+
                                      |
                                      v
          +---------------------------+---------------------------+
          | PostgreSQL | Redis | object storage | DB providers   |
          | ArcGIS     | REST APIs | filesystem  | ODBC/Oracle   |
          +-------------------------------------------------------+
```
