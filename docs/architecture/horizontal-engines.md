# Horizontal engines map

`BE-GO-READY-DOCS-001-CODEMAP-ARCHITECTURE-REFRESH`.

Concise navigation map for the six horizontal engine boundaries
the backend is converging onto. Companion to
[`overview.md`](overview.md) (full architecture) and
[`invariants.md`](invariants.md) (binding rules); this page is
the single-screen index that points at each boundary's canonical
import paths AND the imports that should NOT come back.

The horizontal classification is binding for new BE work
(`.agent-temp/BE_RULES.md` "Horizontal Engine Alignment"). New
code must strengthen the relevant boundary, not duplicate
vertical logic.

---

## 1. Auth / Ownership / Disclosure

`AUTH_SECURITY_ENGINE`.

Canonical entry points:

- JWT middleware: `backend/app/core/auth/middleware.py`.
- Role guards: `require_admin`, `require_user_or_above`,
  `require_not_esecutore`, `require_*_owner` in
  `backend/app/core/auth/dependencies.py`.
- Ownership helpers: `backend/app/shared/ownership.py`.
- Disclosure policy: ADR-0022
  (`docs/architecture/decisions/0022-2026-05-04-auth-disclosure-policy.md`).
- MFA required: ADR-0018.
- JWT blacklist: ADR-0009 (Redis fail-open read / fail-loud write).
- Async user re-check: ADR-0005.

Tenant binding:

- `expected_group_id` is mandatory at every cross-tenant
  boundary; see Resource Engine sec. 2 for the type-level
  enforcement.
- `is_superadmin=True` is privileged; see
  `tests2/shared/test_resource_superadmin_bypass_guardrails.py`
  for the source-bound allowlist.

## 2. Resource Engine + bounded reader + preview parser

`STORAGE_ARTIFACT_ENGINE`.

Canonical package: `backend/app/core/resources/`.

Public surface:

- `ResourceRef` / `Resource` / `ResourceLoader` /
  `ResourceRegistry` (frozen dataclasses + Protocols in
  `types.py`);
- `default_registry()` / `build_default_registry()` in
  `registry.py`;
- one loader today: `ResultBlobLoader`
  (`loaders/result_blob.py`).

Bounded-read helper (the part with real consumers today):

- `backend/app/core/resources/bounded_reader.py:read_stream_bounded(...)`.
- Stage cap: `RESOURCE_MAX_BYTES` (default 200 MB post
  BE-GO-READY-MEMORY-ENGINE-005; `<= 0` disables). Per-loader
  caps may be tighter (e.g. `ARTIFACT_PREVIEW_MAX_BYTES` 25 MB).
- Used in production by:
  `app/db/results_db_service.py`,
  `app/core/resources/loaders/result_blob.py`,
  `app/core/resources/loaders/file_source_blob.py`,
  `app/core/intermediate/artifact_reader.py`.

Preview parser:

- `app/core/intermediate/artifact_reader.py:load_preview_from_artifact`
  reads `data.parquet` via `read_stream_bounded` with the
  preview-specific cap; surfaces `MemoryLimitExceededError` to
  the FastAPI 413 handler.

Pilot exit criteria:

- `BE_RESOURCE_ENGINE_AUDIT.md` sec. 17. KEEP/KILL gate locked by
  `tests2/shared/test_resource_engine_pilot_exit_criteria_lock.py`.

## 3. Storage / reconciliation

`STORAGE_ARTIFACT_ENGINE`.

Canonical entry points (see invariants sec. 1.7):

- `app/core/*/result_store.py`  --  per-module result dispatch;
- `app/services/file_service.py`  --  file source dispatch (FS-4
  pattern);
- `app/db/results_db_service.py`  --  reader / S3 cleanup;
- `app/shared/cloud_backends.py`  --  provider factories
  (AWS / MinIO; Azure / GCP out of scope per ADR-0015).

Object storage policy: `STORAGE_POLICY = db | hybrid |
object_storage`, validated fail-fast by
`validate_storage_config()` at boot (ADR A1).

Backup / log archive:

- `LogArchiveWriter` (`app/services/log_archive.py`) with
  archive-before-purge sequence; see ADR-0014 + invariants sec. 1.4.
- `plenora-backup.timer` + weekly `plenora-backup-test.timer`
  (systemd); see `docs/ops/backup-restore.md`.

Reconciliation backlog: surfaced via the `/ready` check
`reconciliation_backlog`.

## 4. DB config facade strangler + canonical `app/db/config/*`

`CONNECTION_ENGINE` / `DEPLOYMENT_RUNTIME_ENGINE`.

Canonical modules (the strangler target):

- `backend/app/db/config/api_connections.py`
- `backend/app/db/config/comparison_keys.py`
- `backend/app/db/config/connections.py`
- `backend/app/db/config/datasources.py`
- `backend/app/db/config/file_sources.py`
- `backend/app/db/config/projects.py`
- `backend/app/db/config/schedules.py`
- `backend/app/db/config/users.py`

Facade being retired:

- `backend/app/db/config_db_service.py` (compatibility
  re-exports). Migration plan: see `BE_CODEBASE_AUDIT.md`
  C-DB-1 (death map + low-risk migrations).
- New imports outside the documented allowlist are blocked by
  the strangler test (when added).

Other DB seams:

- `app/db/db_service.py`  --  stable facade for the catalog
  PostgreSQL surface (see invariants sec. 1.3).
- `app/db/pg_connection.py`  --  internal pool / `transaction()`
  context manager.
- `app/db/query_executor.py`  --  user-DB queries with row caps.

## 5. Health / readiness

`OBSERVABILITY_ENGINE` / `DEPLOYMENT_RUNTIME_ENGINE`.

Canonical surface: `backend/app/observability/health.py`.

`/health` (HTTP 200 always reachable):

- `postgres`, `redis` checks.

`/ready` (HTTP 200 when all 8 checks pass; 503 otherwise):

- `postgres`, `redis`, `pool_headroom`, `migrations_aligned`,
  `object_storage`, `reconciliation_backlog`,
  `beat_heartbeat` (opt-in), `bootstrap_complete`
  (fail-CLOSED).

Cost boundedness: every check is bounded by an explicit
timeout (Redis `socket_connect_timeout=2`) or capability skip
(object storage). Locked by
`tests2/shared/test_readiness_cost_boundedness_lock.py`.

Boot gates that must pass before `/ready` reports green:

1. `Settings.validate_startup`;
2. `validate_storage_config`;
3. `init_all_dbs` + `apply_pending_migrations`;
4. `_repair_psycopg2_ssl_env`.

## 6. Install smoke path

`DEPLOYMENT_RUNTIME_ENGINE`.

Canonical operator pages:

- `docs/ops/install-non-docker.md`  --  full reference (single
  source of truth, sections 0-14).
- `docs/ops/install-marco-dry-run.md`  --  one-page copy/paste
  pack (`BE-GO-READY-INSTALLATION-024`); sec. 5 is the
  canonical 8-item smoke harness
  (`BE-GO-READY-INSTALL-007`).
- `docs/ops/install-docker.md`  --  Docker target shape
  (NOT IMPLEMENTED today).

Canonical script: `scripts/install.sh` (`--check` preflight +
guided non-interactive install). Idempotent merge of
`backend/.env`. Cleanroom CI verifier:
`.github/workflows/ci-cleanroom.yml`.

Memory caps surfaced at smoke time (see install-marco-dry-run
sec. 5.2): `LOAD_FILE_MAX_BYTES`, `WORKER_RSS_MAX_BYTES`,
`RESOURCE_MAX_BYTES`, `ARTIFACT_PREVIEW_MAX_BYTES`,
`CROSS_JOIN_MAX_OUTPUT_ROWS`, `MULTI_JOIN_MAX_OUTPUT_ROWS`.
Caps `<= 0` disable the corresponding guard.

---

## Do not import

These are the canonical anti-patterns that have caused real
regressions. Each is locked by a source-bound test; new code
must avoid them by construction.

- **Do not depend on `app.core.auth.auth` as a re-export
  surface.** The canonical auth helpers live in
  `app/core/auth/dependencies.py` /
  `app/core/auth/middleware.py`. Re-export shims under
  `app.core.auth.auth` were retired; new imports of
  `from app.core.auth.auth import ...` must NOT come back.
  Breakage path: AUTH-ENGINE re-export removal regression.

- **Do not import from `app.db.config_db_service` outside the
  documented allowlist.** The facade is being strangled in
  favor of `app.db.config.*` canonical modules. Adding a new
  caller perpetuates the facade and blocks the death map. If
  a symbol is missing from `app.db.config.*`, file a
  migration in `BE_CODEBASE_AUDIT.md` C-DB-1, do not add a
  new facade caller.

- **Do not materialize an unbounded stream into memory.** The
  pattern `b"".join(stream["body"])` and
  `list(stream["body"])` is forbidden for any iterator that
  is not pre-validated as small (a single SQL row + cap
  check). Use
  `app.core.resources.bounded_reader.read_stream_bounded(...,
  max_bytes=RESOURCE_MAX_BYTES, stage="...")` with a typed
  cap. Oversize must surface `MemoryLimitExceededError`,
  never a partial bytes object.

- **Do not introduce new `mode="path"` Resource Engine
  callsites without inventory.** Path mode is fallback for
  unavoidable legacy parsers. Every new path-mode caller must
  appear in the inventory in `BE_RESOURCE_ENGINE_AUDIT.md`
  with a classification (unavoidable parser / easy bytes /
  streaming candidate / dead-compat). The default
  is bytes-first; locked by
  `tests2/shared/test_temp_usage_bytes_first_policy.py`.

---

## See also

- [`overview.md`](overview.md)  --  full architecture (sec. 1-sec. 10).
- [`invariants.md`](invariants.md)  --  binding rules (sec. 1 core
  paths, sec. 2 fallback allowlist, sec. 3 feature flags, sec. 4 uniformity).
- [`decisions/`](decisions/)  --  ADRs.
- [`../development/code-map.md`](../development/code-map.md)  --
  per-vertical-module code map ("Contratti da non rompere"
  section).
- [`../ops/install-marco-dry-run.md`](../ops/install-marco-dry-run.md)
   --  operator smoke harness sec. 5.
- `.agent-temp/BE_RESOURCE_ENGINE_AUDIT.md`  --  Resource Engine
  pilot exit criteria sec. 17.
- `.agent-temp/BE_INSTALLATION_AUDIT.md`  --  install
  sequence + memory caps audit.
- `.agent-temp/BE_CODEBASE_AUDIT.md`  --  `config_db_service`
  facade death map (C-DB-1).
