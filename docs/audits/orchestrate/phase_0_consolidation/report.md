# Phase 0.0 - Baseline Report

Date: 2026-04-27

Status: initial code audit, read-only.

Scope:
- repo `URBY-ETL` post-Phase-5;
- `CODE_MAP.md`;
- nuova orchestrazione `orchestrate/`;
- scansioni locali su backend, frontend, tests, deploy e marker residui.

No code/test changes in this pass.

## Verdict

Il codice e' molto migliorato rispetto al pre-Phase-5. I god-file principali
toccati dalla Phase 5 non sono piu' fuori controllo e la codebase e' ora
governabile.

Il giudizio onesto pero' e':

> buona base tecnica, non ancora prodotto industrializzato pulito.

Il sistema funziona, ha test estesi e confini security migliori, ma resta una
codebase grande, con massa storica, coupling operativo e alcuni confini
security/ops da semplificare.

## Current Repository State

- Git branch: `main`
- Remote clean: `origin/main` -> `https://github.com/PlenoraETL/plenora.git`
- Phase 5 tag: `phase-5-complete`
- Working tree at audit time: clean
- Root repo docs now intentionally small:
  - `README.md`
  - `CODE_MAP.md`

Warning observed:
- Git prints `unable to access C:\Users\Marco/.config/git/ignore:
  Permission denied`.
- This does not block repo operations, but should be cleaned up as a local
  environment fix.

## Size Baseline

Approximate source inventory:

| Area | Files | Lines |
| --- | ---: | ---: |
| `backend/app` | 211 | 66,584 |
| `backend/workers` | 9 | 3,888 |
| `backend/tests2` | 380 | 56,078 |
| `frontend/src` | 225 | 58,688 |
| `frontend/tests` | 52 | 6,916 |

Backend files still large:

| File | Approx. lines | Note |
| --- | ---: | --- |
| `backend/app/core/features/arcgis_client.py` | 2367 | biggest remaining backend hotspot |
| `backend/app/services/file_service.py` | 1633 | storage / file lifecycle hotspot |
| `backend/app/core/manipola/transforms/spatial.py` | 1606 | geospatial transform hotspot |
| `backend/app/db/sql_functions.py` | 1505 | SQL function catalog / translation |
| `backend/app/core/estrai/query_generator.py` | 1451 | query construction hotspot |
| `backend/app/services/schema_inference.py` | 1448 | inference complexity |
| `backend/workers/tasks/enrichment_task.py` | 1230 | worker orchestration hotspot |

Frontend files still large:

| File | Approx. lines | Note |
| --- | ---: | --- |
| `frontend/src/pages/ApiConnectionManager.jsx` | 1127 | major UI hotspot |
| `frontend/src/components/panels/ResponseConfigBuilder.jsx` | 922 | complex panel |
| `frontend/src/components/transforms/ParameterInput.jsx` | 917 | large reusable control |
| `frontend/src/components/panels/ApiEnrichNode.jsx` | 854 | enrichment UI hotspot |
| `frontend/src/pages/FileManager.jsx` | 819 | file lifecycle UI |
| `frontend/src/components/nodes/result/ResultNodeStandardView.jsx` | 816 | result rendering complexity |

## What Looks Solid

- Phase 5 materially reduced the main god-files:
  - `api_connector.py` is now around 600 lines in local count;
  - `database_writer.py`, `enrichment_service.py`, `arcgis_uploader.py`,
    and `transforms/base.py` are no longer the dominant mass points.
- Backend test coverage is broad:
  - about 2260 backend test functions detected under `tests2`.
- Security posture improved:
  - crypto key handling fail-fast in production;
  - JWT secret fail-fast in production;
  - `validate_external_url` now covers IPv4 + IPv6/AAAA + unspecified
    addresses;
  - formula evaluation has AST whitelist and dedicated security tests.
- Root documentation is cleaner after archiving old Phase 0-4 reports.

## What Still Looks Weak

### 1. Deploy Is Not Yet Industrialized

`deploy.sh` is a personal imperative runbook:
- local Windows paths;
- hardcoded server;
- PEM path;
- direct SSH/scp workflow;
- backend and frontend deploy logic mixed with local commit/push behavior.

It is useful for the current operator, but not yet a reproducible deploy
pipeline. Server alignment must be handled deliberately from GitHub, not via
manual patching.

### 2. Frontend Is The Next Big Maintainability Surface

The backend Phase 5 paid down a lot of mass. The frontend still has many
large components and CSS files. The UI is probably the next place where
behavior and structure can drift because components mix data loading, view
state, validation, layout and workflow.

### 3. Tests Are Strong But Implementation-Coupled

Observed:
- about 1285 `patch` / `monkeypatch` sites in backend tests;
- many `inspect.getsource` / `ast.parse` contracts.

This is not automatically bad. These tests encode hard-won regressions. But
they are source-location-sensitive and will slow future refactors unless each
batch keeps doing mini-scan + modernization first.

### 4. SSRF Has Two Implementations

Current state:
- `app.shared.api_utils.validate_external_url` is raise-mode and now hardened
  for IPv6/AAAA.
- `ApiConnectionSourceLoadingMixin._validate_outbound_url` is return-string
  mode and used by REST_FILE.

They are not equivalent. This is not a merge-now task. It needs design:
- one canonical return-mode primitive;
- one assert/raise wrapper;
- explicit decision about loopback opt-in for REST_FILE.

### 5. Compatibility Residues Still Need Evidence

Marker scan found intentional `[COMPAT-RESIDUE]` sites:
- `api_connection_source_loading._filesystem_fallback_load_project`;
- `manipola.executor._carica_sorgenti_progetto`.

Do not remove these without log observation. They are candidates, not proven
dead code.

### 6. Formula Eval Is Defended, But Still A Security Boundary

`formula.py` still uses `eval(formula, {"__builtins__": {}, "pd": pd},
local_vars)` as fallback, but only after AST whitelist validation.

Current read: not an immediate bug, because the boundary is explicit and
tested. Still, it should remain a recurring audit target because user input
reaches an eval path.

## Residual Classification

| ID | Residual | Class | Priority |
| --- | --- | --- | --- |
| R0 | Git global ignore permission warning | A - local fix | low |
| R1 | Server/deploy alignment from GitHub | A/D - ops | high before production deploy |
| R2 | SSRF dual implementation | C - architecture/security | high |
| R3 | `[COMPAT-RESIDUE]` source fallbacks | E - needs log evidence | medium |
| R4 | Frontend large components | C - structural initiative | medium/high |
| R5 | `arcgis_client.py` / `file_service.py` / `spatial.py` / `query_generator.py` large files | C - structural initiative | medium |
| R6 | Test source-coupling via patch/getsource/AST | B/C - intentional but costly | medium |
| R7 | Formula eval security boundary | B/C - intentional, audit regularly | medium |
| R8 | Deploy script hardcoded local/server assumptions | D - deploy coordination | high before server rollout |

Legend:
- A: safe/local/testable fix-now candidate.
- B: intentional residual.
- C: architecture/design initiative.
- D: deploy/external coordination.
- E: needs evidence/log observation.

## Recommended Phase 0 Roadmap

### Phase 0.1 - Baseline Artifacts

Read-only deliverables:
- refresh `orchestrate/LEDGER.md` from this report;
- create a concise inventory of large files and ownership areas;
- identify which residuals are fix-now vs new initiatives.

### Phase 0.2 - Ops/Deploy Baseline

No production changes yet.

Tasks:
- document current server state;
- document intended GitHub -> server flow;
- run secret scan policy;
- define smoke checklist before deploy.

### Phase 0.3 - Security Boundary Baseline

Focus:
- SSRF dual implementation;
- formula eval sandbox;
- auth/JWT/crypto env handling;
- file upload / S3 / result store boundaries.

Output should be design recommendations, not immediate rewrites.

### Phase 0.4 - Test Coupling Audit

Inventory:
- source-location-bound logger tests;
- `inspect.getsource` contracts;
- facade patch sites;
- tests that assert historical implementation instead of user contract.

Output:
- modernization candidates before any future refactor batch.

### Phase 0.5 - Structural Candidate Selection

Pick one next structural stream based on risk/value:
- frontend UI components;
- `arcgis_client.py`;
- `file_service.py`;
- `spatial.py`;
- `query_generator.py`;
- worker task orchestration.

Do not start extraction until Phase 0 has chosen a bounded stream.

## Bottom Line

Phase 5 made the system governable. The next run should not immediately repeat
large mechanical extraction. The higher-value work now is:

1. make deploy/server state reproducible;
2. verify security boundaries;
3. reduce frontend and worker complexity;
4. modernize fragile tests only when they block real improvements;
5. remove compatibility residue only with evidence.

This is a good codebase to continue from, but not one to trust blindly.
