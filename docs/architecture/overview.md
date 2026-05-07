# Architecture — URBY-ETL

Descrizione cross-stack del prodotto: core platform, boundaries
backend/frontend, execution flow, persistence, auth/tenant model.

Per le decisioni puntuali con rationale + alternative considerate vedi
i singoli **ADR** in
[`docs/architecture/decisions/`](decisions/).

---

## 1. Core platform

URBY-ETL e' una piattaforma ETL no-code/low-code organizzata attorno a
un canvas visuale (ReactFlow) dove l'utente compone pipeline collegando
nodi tipizzati: source → transform → sink.

I 5 moduli verticali sono **canvas-based**: ognuno ha il suo editor FE
+ il suo executor BE + il suo router REST.

```
┌───────────────────────────────────────────────────────────┐
│                  Frontend (React + Vite)                   │
│  ┌─────────┬──────────┬───────────────┬────────┬────────┐ │
│  │ Estrai  │ Manipola │  Arricchisci  │ Carica │  IMDF  │ │
│  └─────────┴──────────┴───────────────┴────────┴────────┘ │
│         Canvas + Sidebar + Run history + Modals            │
└────────────────────────┬──────────────────────────────────┘
                         │ REST (JWT bearer)
                         │
┌────────────────────────▼──────────────────────────────────┐
│              Backend (FastAPI + Celery)                   │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ app/core/<modulo>/{router, executor, service}        │  │
│  │ workers/tasks/{pipeline, carica, enrichment}_task    │  │
│  │ workers/beat_scheduler  (Celery beat + leader elect) │  │
│  └─────────────────────────────────────────────────────┘  │
└─┬────────────┬───────────────┬─────────────┬─────────────┘
  │            │               │             │
  ▼            ▼               ▼             ▼
┌─────────┐ ┌──────┐ ┌───────────────┐ ┌──────────────────┐
│PostgreSQL│ │Redis │ │ Object storage│ │ External systems │
│ + plenora│ │      │ │ (S3 / MinIO)  │ │ (DB, ArcGIS, API)│
│ schema   │ │      │ │               │ │                  │
└──────────┘ └──────┘ └───────────────┘ └──────────────────┘
```

---

## 2. Backend / Frontend boundaries

### Backend (FastAPI)

Stack: **FastAPI** + **Celery** + **PostgreSQL** + **Redis** + opzionale
**S3/MinIO**.

Architettura per modulo: `backend/app/core/<modulo>/`:

```
app/core/<modulo>/
├── router.py      # FastAPI routes (auth + validazione + dispatch)
├── models.py      # Pydantic schemas
├── executor.py    # business logic principale (orchestration)
├── service.py     # data access + DB queries
└── ...            # transforms/, helpers/, plugin/
```

**Invariante autocontenimento**: ogni modulo importa solo da
`app.shared`, `app.db`, `app.utils`. Cross-module via `app.services`
(es. `task_service`, `project_service`). Vedi
[`docs/architecture/invariants.md`](invariants.md).

### Frontend (React + Vite)

Stack: **React 18** + **Vite** + **ReactFlow** + **Zustand**.

Folder structure:

```
frontend/src/
├── pages/         # 1 page per editor + admin pages
├── components/    # nodes/, modals/, panels/, transforms/, common/
├── hooks/         # useCanvasCore, useTaskPolling, useEditorData, ...
├── store/         # Zustand stores (canvas, auth, theme, ...)
├── api/           # HTTP client + endpoint per dominio (axios-based)
├── context/       # React Context provider (Auth, Toast, Confirm)
├── utils/         # helpers (validation, permissions, columnUtils, ...)
└── styles/        # CSS modules + global tokens
```

Vedi [`docs/development/frontend.md`](../development/frontend.md) per dettagli +
convenzioni API client.

### Boundary contract

- **Auth**: JWT bearer in `Authorization: Bearer <token>`.
- **Body content type**: JSON, eccetto upload (`multipart/form-data`).
- **Error response**: HTTPException FastAPI standard `{detail: ...}` +
  status code semantico (401/403/404/422/500).
- **Async tasks**: handler endpoint ritorna `task_id`, FE polla
  `GET /api/tasks/<task_id>` via `useTaskPolling`.
- **Streaming**: download file via `GET /api/tasks/<task_id>/download`
  con `Content-Type` opportuno per il formato.

---

## 3. Execution / task flow

Pipeline esecuzione asincrona via Celery worker. Esempio flusso
**Estrai/Manipola/Arricchisci** (`pipeline_task`):

```
HTTP POST /api/projects/{id}/execute
  └─ orchestrator.router.execute_project_pipeline
     ├─ require_not_esecutore (auth gate)
     ├─ assert_project_group (cross-tenant guard)
     └─ task_service.create_pipeline_task(user_id=...)
        └─ execute_pipeline_task.delay(...)         [Celery dispatch]

Worker pickup
  └─ workers.tasks.pipeline_task.execute_pipeline_task
     ├─ idempotency check (intermediate.runs)
     ├─ recheck_user_authorized (N9: user disabled? still in group?)
     ├─ open_run + intermediate.create_run
     ├─ orchestrator.execute_pipeline
     │  ├─ project_type=estrai      → estrai_executor.execute
     │  ├─ project_type=manipola    → manipola_executor.execute
     │  └─ project_type=arricchisci → enrich_executor.execute
     │     └─ ApiConnectionService.execute_enrichment
     └─ close_run + (eventuale) carica chain
```

Snapshot intermedi (Phase 6 Batch 1+) registrati in
`intermediate.node_meta` + `intermediate.node_rows` o object storage
(`full_artifact` mode). Vedi
[ADR-0019](decisions/0019-2026-05-01-intermediate-mode-per-call-override.md).

### Scheduler

`workers.beat_scheduler.PlenoraBeatScheduler`:

- Leader election via Redis SETNX (TTL 120s) per multi-instance future.
- Schedule sync da DB ogni ~30s.
- Heartbeat per tenere viva la lock.
- Catchup tracking: registra `scheduled_for` nei log per distinguere
  tick recuperati da live.
- Log archive pipeline (writer isolato PG-agnostic, vedi
  [ADR-0014](decisions/0014-2026-04-21-log-archive-pipeline.md)).

---

## 4. Artifact / storage / intermediate persistence

### Output finale (per pipeline)

Salvato come **blob** nel DB `plenora.results` (path-based o BYTEA per
file piccoli) o come **path su filesystem** in `RESULTS_DIR` (legacy).
Filtro per `result_id` esposto via `GET /api/results/{id}/download`.

### Snapshot intermedi (per audit / diff / debug)

Mode globale `INTERMEDIATE_PERSISTENCE_MODE` (default `metadata_only`).
4 mode supportati end-to-end:

| Mode | Cosa scrive | Use case |
|---|---|---|
| `metadata_only` | `node_meta` + `node_hash`. Zero righe. | Default produzione. Massima velocita'. |
| `preview_only` | Meta + hash + prime N righe (default 500) in `node_rows`. | Audit leggero, UI preview. |
| `full_pg` | Tutte le righe in `node_rows`. | Legacy compat, audit forte. |
| `full_artifact` | Meta + hash + Parquet + manifest su object storage. | Audit forte cross-node, replay. |

Override per-call superadmin via `?intermediate_mode=...` su endpoint
execute (R-25,
[ADR-0019](decisions/0019-2026-05-01-intermediate-mode-per-call-override.md)).

### Object storage (runtime + backup)

Provider: **AWS S3** (default), **MinIO** (alternative,
[ADR-0015](decisions/0015-2026-04-22-minio-object-storage-provider.md)).

Bucket separati:

- `OBJECT_STORAGE_BUCKET` (runtime artifacts: file sources >50MB,
  intermediate `full_artifact`, IMDF bundles cross-node).
- `BACKUP_BUCKET` (DB dumps + verify-restore).

Capability check fail-fast a startup (`validate_storage_config` in
`app.shared.cloud_backends`).

### File sources (upload utente)

Strategia threshold-based: file <50MB in `plenora.file_sources` BYTEA,
file >=50MB su S3 con `storage_key` reference
([ADR-0011](decisions/0011-2026-04-21-file-sources-dispatch-s3-threshold.md)).

---

## 5. Auth / tenant / audit

### Auth model

- **JWT HS256** bearer token, **TTL 8h**, **no refresh token**
  ([ADR-0004](decisions/0004-2026-04-21-jwt-hs256-ttl-8h-no-refresh.md)).
- **MFA TOTP opt-in** (RFC 6238). Login ritorna `403 mfa_required` se
  attivo senza codice
  ([ADR-0018](decisions/0018-2026-04-30-mfa-required-disclosure-tradeoff.md)).
- **JWT blacklist Redis** per logout (jti-based), policy fail-open READ
  + fail-loud WRITE
  ([ADR-0009](decisions/0009-2026-04-21-jwt-blacklist-redis-fail-open-read-fail-loud-write.md)).
- **Trusted proxies allowlist** per anti-spoofing client IP
  ([ADR-0010](decisions/0010-2026-04-21-trusted-proxies-allowlist.md)).

### RBAC (5 ruoli)

| Ruolo | Capability |
|---|---|
| `superadmin` | Tutto. Cross-tenant access. Audit override. |
| `admin` | Group-scoped admin. Manage users/connections/projects nel proprio gruppo. |
| `user` | Crea/edita progetti del proprio gruppo. Esegue pipeline. |
| `viewer` | Read-only del gruppo. |
| `esecutore` | Kiosk-only: lista + esegue **eseguibili pubblicati**, niente accesso al canvas/admin. |

### Tenant isolation

Group-based via `group_id` su tutte le entita' principali (project,
connection, file_source, eseguibile, ticket). `assert_project_access`
helper (`app.shared.authz_helpers`) e' la **single source of truth**:
chiamato sia dall'HTTP gate (`require_not_esecutore` + `assert_*`) sia
dal worker re-check (`recheck_user_authorized`,
[ADR-0005](decisions/0005-2026-04-21-async-user-recheck-helper.md)).

### N9 — async permission revoke

Tra submit task e pickup worker puo' passare tempo: se admin revoca
permessi nel frattempo, il task **non procede**. Helper centralizzato
`recheck_user_authorized` re-verifica al pickup
([N9 fix 2026-04-17](../../backend/tests2/security/unit/test_n9_async_permission_revoked_contract.py)).

### Audit

- `plenora.log_runs` — run lifecycle (start/end/status/error).
- `intermediate.runs` — run intermediate (canvas-aware, group-scoped).
- `plenora.audit_actions` — actions sensibili (create/delete/update su
  resource critiche) via `@audit_action` decorator.
- `plenora.logs` — application logs aggregati (PostgresLogHandler).

### Crypto at-rest

Credenziali (DB connections, API connections auth, datasource auth, MFA
secret) cifrate con **Fernet** (cryptography library) usando
`PLENORA_ENCRYPTION_KEY` come canonical
([ADR-0003](decisions/0003-2026-04-21-fernet-crypto-at-rest.md)).

Gli env alias storici `URBY_ENCRYPTION_KEY` / `CONN_ENCRYPT_KEY`
sono stati rimossi 2026-04-23 (vedi `compat-residue-runbook.md`).

---

## 6. Canvas / pipeline composition

### Tipi nodo

Comuni a tutti gli editor:
- **Source nodes**: `project_source` (output di altro progetto),
  `file_source` (CSV/JSON/Excel/GeoJSON/GPKG/SHP/Parquet),
  `staticValues` (dati inline con template variables).
- **Result node**: terminale, definisce formato output.
- **Carica nodes**: `carica` (DB writer), `carica_arcgis` (ArcGIS).

Specifici:
- **Estrai**: `tableNode` (DB table/view), `featureLayerNode` (ArcGIS).
- **Manipola**: `transform` (singolo input + 33+ ops), `multi_join`.
- **Arricchisci**: `api_enrich` (REST API enricher / generator),
  `imdf_publisher` (ArcGIS publish bundle IMDF).

### Edge / data flow

DAG dei nodi via edges. Topological sort per ordering esecuzione.
Cycle detection a save-time (`detect_project_lineage_cycle` su
`project_source` cross-progetto) + a runtime
(`_assert_no_lineage_cycle_on_chain` in `manipola_executor`).

Column propagation FE: `useColumnPropagation` hook calcola le colonne
output di ogni nodo per UI feedback (`applyTransformation` switch su
`transform_op`). Backend ricalcola dal canvas serializzato.

---

## 7. Test & contract

`backend/tests2/` — pytest (~2000+ test, ~85% behavior-bound dopo
modernizzazione R-04/R-04b).

`frontend/tests/` — vitest + React Testing Library + MSW per fixture
HTTP.

Pattern test: vedi
[`backend/tests2/README.md`](../../backend/tests2/README.md) e
[`frontend/tests/README.md`](../../frontend/tests/README.md).

Markers backend (vedi `backend/tests2/pytest.ini`):
`unit`, `functional`, `security`, `contract`, `regression`, `scheduler`,
`arricchisci`, `carica`, `features`, `manipola`, `orchestrator`,
`intermediate`, `eseguibili`, `estrai`, `shared`, `integration`.

ADR-0020 documenta i 7 `inspect.getsource` residui come **KEEP
intenzionali** in 3 categorie (policy enforcement / AST sentinel /
scheduler integration deferred).

---

## 8. Observability

### Logging

- **PostgresLogHandler** scrive `plenora.logs` (livello WARNING+).
- **PostgresLogHandler.open_run / close_run** scrive `plenora.log_runs`.
- **Bulk telemetry** filter: INFO records dei logger
  `app.db.bulk*` aggregati su `_bulk_telemetry_db_handler` (Batch 3.10).

### Metrics / health

- `GET /health` — liveness (sempre 200 se app risponde).
- `GET /ready` — readiness (DB + Redis check).
- `/api/admin/system-status` — dashboard superadmin (PG pool, Redis,
  Celery, queue depth, JWT blacklist size, schedules failing, ecc.).

### Backup observability

`plenora.backup_runs` + `plenora.verify_runs` (DB-side, vedi
[ADR-0008](decisions/0008-2026-04-21-backup-observability-db-side.md)).

---

## 9. Capability services / AI / extension layer

> **Status: ROADMAP, not current.** Non implementato. Documentato qui
> come **direzione architetturale** futura, non come capability live.

L'idea e' separare **core platform** (cosa e' adesso: ETL no-code) da
**capability services** (cosa potrebbe diventare: layer plug-in
specializzati per dominio).

Esempi di capability services pensabili (non implementati):

- **AI assist**: suggerimento trasformazioni dal contesto canvas,
  natural language → pipeline draft, anomaly detection sui run.
- **Satellitare / GIS avanzato**: ingestion raster, reproject batch,
  zonal stats, NDVI/NDWI builder come transform nodes.
- **Streaming**: ingestion event-based (Kafka / Pulsar) come source
  type, output streaming come sink type.
- **Data catalog**: discovery + lineage cross-progetto + column-level
  metadata enrichment.
- **Multi-tenant SaaS**: tenant isolation hardening, billing meter,
  usage analytics.

Trigger per aprire questi capability services: **richiesta business
concreta**. Senza, rimangono in roadmap.

Vedi [product roadmap](../product/roadmap.md) per il quadro Phase 2/3/4+.

---

## 10. Riferimenti tecnici

### ADR registry (decisioni formali)

[`docs/architecture/decisions/`](decisions/)

ADR notevoli:
- ADR-0003 Fernet crypto at-rest.
- ADR-0004 JWT HS256 8h no-refresh.
- ADR-0005 helper `recheck_user_authorized` (DRY su 4 task Celery).
- ADR-0007 Protocol + runtime_checkable vs ABC.
- ADR-0009 JWT blacklist Redis fail-open/fail-loud.
- ADR-0011 FileSourcesDispatch S3 threshold 50MB.
- ADR-0014 log archive pipeline.
- ADR-0015 MinIO object storage provider.
- ADR-0018 MFA disclosure tradeoff.
- ADR-0019 INTERMEDIATE_PERSISTENCE_MODE per-call override.
- ADR-0020 Source-bound test KEEP list.

### Invariants

[`docs/architecture/invariants.md`](invariants.md)
elenca le invarianti vincolanti del codebase (es. "tutti i write DB
passano per `DestinationWriter`", "ogni endpoint sensibile ha
`@audit_action`", "ogni Celery task accetta `user_id` per N9").

### Inventory report

[`inventory_report.md`](inventory_report.md)
— mappa endpoint REST + Celery tasks + tabelle DB + dependencies
esterne.

### CODE_MAP

[`development/code-map.md`](../development/code-map.md) — mappa puntuale moduli (~71KB).
High-density, non human-readable end-to-end ma utile come grep target.
