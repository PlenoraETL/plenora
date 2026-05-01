# Phase 0.5 Wide Audit - Provisional Snapshot

Date: 2026-04-28

Status: draft / provisional snapshot.

This document captures the broad Phase 0 analysis after the storage and
intermediate persistence batches. It is not the final Phase 0 closeout.

The active follow-up sub-stream is `0.5c compat residue evidence`; Claude is
working on it separately. Update this file after 0.5c/0.5e/0.5a/0.5d/0.5b/0.5f
and 0.5g produce evidence.

No runtime code, tests, migrations or deploy files are changed by this
snapshot.

## 1. Expanded System Map

### Backend

Large backend surfaces observed from code:

| Area | File | Size / note |
|---|---|---|
| ArcGIS | `backend/app/core/features/arcgis_client.py` | ~2476 lines; historical god-file; `ArcGISClient` dominates the file |
| Geospatial transforms | `backend/app/core/manipola/transforms/spatial.py` | ~1941 lines; 28 `op_geo_*` functions in one flat module |
| Scheduler | `backend/app/core/scheduler/execution_manager.py` | ~1125 lines; lock, execute, cleanup and retention orchestration |
| Manipola executor | `backend/app/core/manipola/executor.py` | ~1112 lines |
| REST file enrichment | `backend/app/core/arricchisci/api_connection_rest_file.py` | ~1149 lines |
| Auth | `backend/app/core/auth/auth.py` | ~1171 lines; dense boundary, but explicit and stable |
| File service | `backend/app/services/file_service.py` | ~1847 lines; 14 classes/protocols/builders in one module |
| Schema inference | `backend/app/services/schema_inference.py` | ~1465 lines |
| SQL functions | `backend/app/db/sql_functions.py` | ~1717 lines |
| Query executor | `backend/app/db/query_executor.py` | ~982 lines |
| Cloud backends | `backend/app/shared/cloud_backends.py` | ~917 lines; runtime/backup/log archive object storage factories |
| Dataframe utils | `backend/app/utils/dataframe.py` | ~967 lines |
| Logger | `backend/app/utils/logger.py` | ~718 lines; `PostgresLogHandler` + structured logging |
| Enrichment worker | `backend/workers/tasks/enrichment_task.py` | ~1430 lines; Celery orchestration hotspot |

Recently improved backend boundaries:

- `backend/app/core/intermediate/`: now a real product boundary with policy,
  snapshot, artifact writer/reader, router and DB functions.
- `backend/app/utils/result_store.py` + `backend/app/db/results_db_service.py`:
  result writer/reader split with object-storage writer and legacy DB reader.
- `backend/app/shared/cloud_backends.py`: explicit runtime object storage,
  backup object storage and log archive storage factories.

### Frontend

Top large frontend surfaces:

| Area | File | Size / note |
|---|---|---|
| API connections UI | `frontend/src/pages/ApiConnectionManager.jsx` | ~1183 lines |
| Response config UI | `frontend/src/components/panels/ResponseConfigBuilder.jsx` | ~978 lines |
| Transform parameter UI | `frontend/src/components/transforms/ParameterInput.jsx` | ~974 lines |
| API enrich node | `frontend/src/components/panels/ApiEnrichNode.jsx` | ~944 lines |
| File manager | `frontend/src/pages/FileManager.jsx` | ~887 lines |
| Result node view | `frontend/src/components/nodes/result/ResultNodeStandardView.jsx` | ~880 lines |
| Project list | `frontend/src/pages/ProjectList.jsx` | ~862 lines |
| Carica node | `frontend/src/components/nodes/CaricanNode.jsx` | ~797 lines |
| Preview modal | `frontend/src/components/modals/PreviewModal.jsx` | ~787 lines |

Good sign: the frontend already uses `React.lazy`, `Suspense` and dynamic
loading widely, so the issue is not simply bundle splitting. The remaining
frontend concern is component responsibility and local state density.

### Ops / Deploy

Current known shape:

- `deploy.sh` is a personal Windows-oriented runbook.
- It contains hardcoded operator paths/server details and automatic
  `git add -A` / commit behavior.
- systemd long-running services read `backend/.env`.
- object-storage runtime now makes env alignment more important:
  `STORAGE_POLICY=object_storage`, `OBJECT_STORAGE_ENABLED=true`,
  `OBJECT_STORAGE_BUCKET`, `PLENORA_ARTIFACT_BUCKET`.

### Tests

Observed test surface:

- `backend/tests2/`: hundreds of files and broad contract coverage.
- Significant patch/monkeypatch usage remains.
- Many `inspect.getsource` / `ast.parse` references exist.
- Category A logger-bound tests are modernizable independently.
- Category B source-bound tests should be modernized only when their target is
  actually refactored.
- Category C AST cross-codebase security tests are intentional guards and
  should not be weakened.

### Docs / Architecture

Strong current assets:

- `CODE_MAP.md`
- `backend/docs/architecture/invariants.md`
- 17 ADRs covering eval whitelist, crypto/JWT, URL validation, storage,
  backups, IMDF, logging and feature boundaries.

Known documentation note:

- ADR number `0013` is absent. Treat as numbering gap, not a product bug.

## 2. Top 10 Risks / Problems

| # | Risk | Severity | Evidence | Suggested unlock |
|---|---|---|---|---|
| 1 | `arcgis_client.py` remains the largest backend god-file | High structural | ~2476 lines, `ArcGISClient` dominates | Deep dive before any extraction; split auth/rest/layer/service/error concerns behind facade |
| 2 | `spatial.py` is a flat geospatial transform module | Medium structural | ~1941 lines, 28 `op_geo_*` functions | Split by category while preserving registry dispatch |
| 3 | `file_service.py` co-locates protocols, builders and orchestration | Medium structural | ~1847 lines, 14 classes | Extract protocols/builders; leave `FileSourceService` as orchestrator |
| 4 | SSRF validation still has two implementations | Medium security/structure | raise-mode shared helper + return-string REST_FILE helper | Design already exists; modernize lock tests before merge |
| 5 | Deploy flow is not product-grade | High ops | personal `deploy.sh`, hardcoded paths/server, auto-commit | Design tag/branch deploy flow, env checks, rollback and server alignment |
| 6 | Test source coupling slows future refactors | Medium process | many `inspect.getsource` / AST/path-sensitive contracts | Modernize only per target, before the corresponding refactor |
| 7 | `execution_manager.py` remains an orchestration hotspot | Medium structural | ~1125 lines | Extract step/finalize helpers after test modernization |
| 8 | `[COMPAT-RESIDUE]` paths lack runtime evidence | Low/medium recurring | markers exist, no current evidence window closed | Run 0.5c DB/log evidence queries before removal |
| 9 | `ApiConnectionManager.jsx` is a dense frontend page | Medium UX/structure | ~1183 lines | Extract form/status/filter pieces and possibly custom hooks |
| 10 | `enrichment_task.py` is a large worker orchestration module | Medium worker | ~1430 lines, nested task helpers | Extract group/finalize/cleanup helpers after test plan |

## 3. Top 10 Strengths

| # | Strength | Why it matters |
|---|---|---|
| 1 | Security boundaries are documented with ADRs and invariants | Reduces accidental breakage in high-risk areas |
| 2 | Result storage policy is fail-closed for object storage | Avoids silent BYTEA fallback in production object-storage policy |
| 3 | Intermediate persistence is now explicit and mode-based | `metadata_only`, `preview_only`, `full_pg`, `full_artifact` have clear semantics |
| 4 | Runtime object storage is separated from backup storage | Reduces IAM and retention ambiguity |
| 5 | Structured logging and `[PERF]` markers exist | Enables real performance diagnosis |
| 6 | Test breadth is strong | Contract tests cover many critical paths and failure modes |
| 7 | Cloud backend abstraction is mature | Runtime, backup and log archive storage are separate factories |
| 8 | Auth/JWT/MFA/RBAC boundaries are explicit | Security-sensitive code has clear contracts and ADRs |
| 9 | Migrations are tracked and idempotent | Supports repeatable schema setup and readiness checks |
| 10 | `CODE_MAP.md` is active and useful | Gives future agents a real navigation map |

## 4. Product-Grade Areas vs Below-8 Areas

### Product-Grade Or Close

| Area | Approx rating | Notes |
|---|---:|---|
| Storage policy / result store | 8.5-9 | Strong architecture; retention/migration legacy still future |
| Intermediate persistence | 8.5-9 | Strong semantics; avoid new refactor until runtime settles |
| Crypto-at-rest | 9 | Canonical env and tombstoned aliases |
| JWT/MFA/RBAC | 8 | Solid, still worth recurring security audit |
| Logging/observability | 8 | Good app-level observability; ops dashboards can improve |
| Migrations | 8 | Idempotent and tracked |
| ADR/invariants | 9 | Very useful for preserving design intent |
| Backend test breadth | 8 | Strong coverage, but source coupling pulls the average down |

### Below 8

| Area | Approx rating | Main reason |
|---|---:|---|
| Backend structural hotspots | 5 | Several 1.8k-2.5k-line files still concentrate concerns |
| SSRF unification | 6 | Dual implementation remains |
| Deploy/ops | 4 | Personal script, weak reproducibility/rollback story |
| Frontend top pages | 6 | Large pages/components with dense local state |
| Test source coupling | 5 | Refactors can be blocked by implementation-bound tests |
| Runtime env drift discipline | 6 | `.env.example` is long and production env alignment still needs formal checks |
| Compat residue evidence | 5 | Marker paths need log/DB evidence |
| Enrichment worker orchestration | 6 | Large task with many responsibilities |
| Scheduler execution manager | 6 | Large orchestration file |
| Auth file size | 7 | Dense but stable and security-boundary explicit |

## 5. Refactor Candidates

### Small

| ID | Candidate | Scope | Payoff | Rollback |
|---|---|---|---|---|
| S1 | Migration SQL comment drift | Applied migration comments only | Complete A0 cleanup | Trivial |
| S2 | Compat residue evidence queries | Read-only SQL/log queries | Turns guesses into keep/remove decisions | No code |
| S3 | Logger-bound test modernization | Four test files | Reduces future refactor friction | Per-file revert |
| S4 | `deploy.sh` fail-fast on dirty tree | Small deploy script edit | Prevents accidental commits | Trivial |
| S5 | `.env.example` runtime audit | Docs/config review | Reduces deploy surprises | Trivial |

### Medium

| ID | Candidate | Scope | Payoff | Test gate |
|---|---|---|---|---|
| M1 | SSRF single-source consolidation | `api_utils`, source loading, lock tests | One URL policy, two wrappers | URL security tests + REST_FILE lock tests |
| M2 | `spatial.py` split by category | Manipola transforms | Lower transform change risk | Manipola transform tests |
| M3 | `file_service.py` split | Protocols/builders/service | Smaller ownership areas | Services/file-service tests |
| M4 | `execution_manager.py` extract helpers | Scheduler | Smaller execution core | Scheduler tests + source-bound modernization |
| M5 | `enrichment_task.py` helper extraction | Workers/tasks | Smaller Celery task | Enrichment/API connector tests |
| M6 | `ApiConnectionManager.jsx` split | Frontend page/components | Better UI maintainability | Frontend tests + smoke |
| M7 | Async artifact writer | Intermediate | Only if full-artifact runtime numbers demand it | New contracts + opt-in |

### Large

| ID | Candidate | Scope | Payoff | Notes |
|---|---|---|---|---|
| L1 | `arcgis_client.py` decomposition | ArcGIS features | Tames biggest backend hotspot | Needs facade and careful test plan |
| L2 | CI/CD deploy path | Ops | Reproducible deploy and audit trail | Keep `deploy.sh` until cutover |
| L3 | Systematic frontend page split | Frontend | Product UX maintainability | Page-by-page, not one giant patch |
| L4 | L2 diff from artifact | Intermediate + router | Full row diff for `full_artifact` runs | Product feature, not bug |
| L5 | Live permission revoke propagation | Auth + frontend | Stronger real-time security UX | Needs product design |

## 6. What Not To Touch Now

| Component | Why |
|---|---|
| `backend/app/core/intermediate/` | Freshly reworked and runtime-validated; let it stabilize |
| `result_store` / storage policy / `cloud_backends` | Recently ratified and working; avoid churn |
| `crypto.py` + JWT/MFA chain | Security-sensitive and stable; no change without strong reason |
| Applied migrations | Treat as historical artifacts unless a comment truly misleads operators |
| `transforms_registry.json` and facade contracts | Stable Phase 5 boundary |
| ADRs already ratified | New decisions should be new ADRs, not edits to history |
| Category B `inspect.getsource` tests | Modernize only when touching the related target |
| Category C AST security tests | Intentional cross-codebase guards |
| Formula eval boundary | Keep as recurring audit; not an immediate fix |

## 7. Proposed Remaining Phase 0.5 Streams

| Stream | Output | Priority |
|---|---|---:|
| 0.5c Compat residue evidence | DB/log evidence table for E1-E5, A1, A4 | 1 |
| 0.5e Deploy/ops design | GitHub/server/env/deploy design and rollback path | 2 |
| 0.5a Hot-file deep dive | One doc per major backend hotspot with concern map and test gates | 3 |
| 0.5d Test coupling roadmap | Which source-bound tests block which refactors | 4 |
| 0.5b Frontend wide audit | Map top frontend pages by state/data/view responsibility | 5 |
| 0.5f Performance baseline | Harvest `[PERF]` logs and existing scripts into a current baseline | 6 |
| 0.5g UX/product readiness sample | Review real user flows for friction, not just code shape | 7 |

Recommended execution order:

1. 0.5c, because it is fast and turns compat residue guesses into evidence.
2. 0.5e, because deploy/ops is the biggest product-grade gap.
3. 0.5a, because large refactors should not be chosen without concern maps.
4. 0.5d, because source-bound tests must be planned before refactors.
5. 0.5b, because frontend is the next mass surface.
6. 0.5f, if more runtime numbers are needed.
7. 0.5g, after the technical map is clearer.

## 8. Current Decision

Do not open Phase 1 yet.

Keep Phase 0 open as expanded analysis until at least 0.5c and 0.5e are
reviewed. Refactor candidates can be large, but implementation should start
only after explicit selection and test-gate planning.
