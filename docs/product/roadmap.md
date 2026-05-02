# Roadmap — URBY-ETL

Residui audit aperti e direzioni future. Status as of **2026-05-02**.

For the shorter operational list of what is still open across FE, BE and
tests, see [Residual Register](residual-register.md).

> **Convenzione**:
> - **PARTIAL** - avanzato in modo sostanziale, con residuo piu' stretto.
> - **CLOSED** — chiuso, citato per tracciabilita'.
> - **NEXT** — candidato Phase 2 (post-launch immediato).
> - **LATER** — Phase 3-4+, quando emerge trigger business o tecnico.
> - **DROP** — non azionabile (false positive, intentional, fuori
>   scope).
>
> Le voci sotto **non sono commitment di implementazione**. Sono
> direzioni note, prioritizzabili al bisogno.

---

## 1. Sicurezza & runtime

| ID | Item | Status | Note |
|---|---|---|---|
| **R-01** | Rate-limiter cluster-safe (Redis-backed) | CLOSED | Modulo `app/shared/login_rate_limiter.py` (sliding window via sorted-set + pipeline `MULTI`, TTL = window+60s buffer). Wrapper in `auth.py` mantiene fallback in-memory legacy quando Redis non disponibile, comportamento single-node invariato. 19 test verdi (8 nuovi Redis-backed + 11 esistenti riverificati). |
| **R-02** | Doppio password hasher bcrypt+Argon2 — timeline migrazione | LATER | Funziona, manca solo policy formale. S effort doc + M migration. |
| MFA-001 | MFA opt-in (no enforcement globale) | DROP/LATER | Decisione di prodotto in ADR-0018. Cambio = nuova ADR + onboarding. |
| JWT-001 | JWT secret hardcoded fallback in dev | DROP | Comportamento corretto/voluto (ADR-0004). |
| **R-25** | `INTERMEDIATE_PERSISTENCE_MODE` per-call override | CLOSED | Chiuso 2026-05-01: B1 (#39) + B2 (#40) + B2.b/B3/B4 (#41) + B5 UI (#47). End-to-end via `?intermediate_mode=...` superadmin-gated. |
| **R-26** | ADR MFA disclosure tradeoff | CLOSED | ADR-0018 (#38). |

---

## 2. Ops / Compliance

| ID | Item | Status | Note |
|---|---|---|---|
| **R-28** | SBOM CI hook automation | NEXT Phase 3 | Workflow + cron mensile + PAT commit-back. S effort. |
| **R-29** | SAST/container scanner (Bandit / Trivy / Semgrep) | NEXT Phase 3 | Falsi positivi iniziali da gestire. M effort. |
| **R-35** | SBOM frontend (`package-lock.json`) | NEXT Phase 3 | Riusa workflow R-28. S effort. |
| **R-30** | DB snapshot pre-deploy hook | NEXT Phase 2 | Aggiungere `pg_dump`->S3 a `deploy.sh:pre_deploy_check`. M effort, va testato in staging. |
| **R-33** | DR drill execution (mai eseguito) | NEXT manuale | Playbook `dr_drill_playbook.md` esiste, mai eseguito. ~1h sessione SSH ops. |
| **R-31** | Rollback scriptato (oggi multi-step manuale) | LATER | Riduce tempo ripristino. M effort, blocco con R-30. |
| **R-32** | Bus factor 1 (ops) | LATER manuale | Continuity gap. L effort handoff sessions. |
| **R-34** | Retention policy artifacts buckets `plenora-runtime-artifacts-*` (TBD) | LATER | Costo S3 nel tempo. S effort policy + S3 lifecycle. |
| OBSERV-001 | Observability sink (Metabase vs alternative) | NEXT Phase 2 | Decisione documentata in `observability-sink-decision.md`, implementazione no. M effort. |
| CR-001 | Compat residue Q1-Q4 SQL queries | NEXT manuale ops | Solo SQL su DB prod. S effort sessione. |
| **R-21/22/23** | DeprecationWarning emit-once per alias legacy | CLOSED | #48. Removal target 2026-Q4. |

---

## 3. Structural debt

| ID | Item | Status | Note |
|---|---|---|---|
| **R-19** | `core/features/arcgis_client.py` 2476 LOC monolith | LATER Phase 5-7 | XL refactor multi-PR. Seam: auth handler / pagination / write batch. |
| **R-20** | `workers/tasks/enrichment_task.py` 1430 LOC | LATER Phase 5-7 | XL. `run_enrichment_task` 500+ LOC mix. |
| STRUCT-001 | `services/file_service.py` 1050 LOC | PARTIAL | Seam A/B/C/D chiusi: helper puri (`file_helpers.py`), Protocols/default impls (`file_service_deps.py`), storage dispatch (`file_storage_dispatch.py`), `preview_file_temp` standalone (`file_preview_temp.py`), parser dispatch condiviso (`file_load_dispatch.py`), cleanup condiviso (`file_cleanup.py`), read orchestration DB/security-bound (`file_source_read.py`), blob/temp materialization (`file_blob_materialization.py`) e raw upload (`file_raw_upload.py`) estratti con wrapper/re-export compatibili. Riduzione 1847 -> 1050 LOC. Restano `create_from_upload` e `create_multi_layer_from_upload` da affrontare un seam alla volta. |
| STRUCT-002 | `services/schema_inference.py` 1502 LOC | LATER | Tocca canvas types fallback. M-L effort. |
| STRUCT-003 | `core/manipola/transforms/spatial.py` 1941 LOC | WATCH/LATER | Cohesive (single concern), refactor non urgente. |
| STRUCT-004 | `core/scheduler/execution_manager.py` 1097 LOC | LATER | R-05 ha introdotto `app/shared/clock.py` e migrato i test heartbeat/lock-lost a `FakeClock`; il retry release-lock resta volutamente fuori scope e gia' mockabile. |
| STRUCT-005 | `core/auth/auth.py` 856 LOC | PARTIAL | Auth seams A-D chiusi: rate limiter (`login_rate_limiter.py`), password hashing, models, ownership helpers e JWT token helpers estratti con re-export compatibili. Resta solo massa endpoint/router auth, da trattare se si toccano quelle route. |
| STRUCT-006 | `db/sql_functions.py` 1717 LOC | WATCH | SQL builders, single-concern. |

---

## 4. Test reliability

| ID | Item | Status | Note |
|---|---|---|---|
| **R-04** | N9 source-bound `inspect.getsource` (11 occorrenze) | CLOSED | #44. Modernizzato a behavior-bound (bytecode probe + spy + SQL capture + TestClient + eager). |
| **R-04b** | Batch non-auth source-bound (9 file) | CLOSED | #46. Stesso pattern di R-04. |
| TR-001 | Source-bound `inspect.getsource` residui post TR-002 | DROP/KEEP | **5 occorrenze in 5 file** = 4 voci ADR-0020 ancora valide (Cat. 1: `test_database_writer_log_consistency_contract.py:299`; Cat. 2: `test_n13_alias_resolution_contract.py:193`, `test_type_dispatch_contract.py:153`, `test_persist_storage_decoupling.py:218`) **+ 1 fuori ADR** (`test_n15b_scheduled_for_always_set_contract.py:40` — fallback sentinel scheduler, non listato in ADR-0020). Cat. 3 chiusa da **TR-002** (vedi nota Superseded in ADR-0020). |
| **TR-002** | Scheduler test infrastructure (Cat. 3 ADR-0020) | CLOSED | I 3 casi Cat. 3 ADR-0020 migrati a behavior-bound (PR TR-002): `ExecutionManager.__new__` + MagicMock pattern senza harness condiviso (riusa pattern di `test_execution_manager_contract.py`). Vedi nota Superseded in ADR-0020. |
| TR-003 | `test_columns_metadata_save_retry_contract.py` lazy psycopg2 imports | DROP | Pattern intenzionale per optional dep. |
| TR-004 | Datetime non frozen in admin_system_status tests | DROP | Pattern relative-age legitimate. |
| **R-05** | `time.sleep` in scheduler heartbeat tests | CLOSED | Commit `cf00387`: `Clock`/`WallClock`/`FakeClock` seam, `LockHeartbeat` clock-injectable, heartbeat/lock-lost tests migrati off wall-clock sleeps. Affected subset ~50s -> sub-second; scheduler/unit 290 passed. |

---

## 5. Frontend residui

| ID | Item | Status | Note |
|---|---|---|---|
| **FE-22** | Polling single-flight | CLOSED | #42 (`useTaskPolling`) + #43 (-353 righe orphan + dedup keyframe) + #45 (12 CSS classi missing). |
| **R-25-B5** | UI ExecutionModal toggle staff-only | CLOSED | #47. R-25 al 100%. |
| **FE-CLN-001** | `ThemeToggle` dedup | CLOSED | Commit `d309e0d`. Estratti `useTheme` hook + `ThemeToggle` componente shared, applicati a sidebar + 7 page/editor; coperto da test hook (`useTheme`). |
| FE-CLN-002 | Inline styles vs design tokens | LATER | M effort. Richiede design system review. |
| FE-CLN-003 | Stray `local_*.log` working tree | DROP | Gitignored, non in repo. |
| FE-SPLIT-001 | `pages/ApiConnectionManager.jsx` 1193 LOC, 6 concerns | LATER | L effort. Modal extraction + context split. |
| FE-SPLIT-002 | `components/transforms/ParameterInput.jsx` 974 LOC | LATER | M-L structural split. Contract coverage added for main input types plus `PivotMappingBuilder`; split remains maintainability work, not coverage gap. |
| FE-SPLIT-003 | `components/panels/ResponseConfigBuilder.jsx` 978 LOC + tight coupling con `ApiEnrichNode` | LATER | M structural split. Contract coverage now covers structure, mappings, transforms, preview entrypoints and residual builder paths. |
| FE-SPLIT-004 | `EditorCore` extraction (Estrai/Manipola/Arricchisci ~85% duplicazione) | LATER Phase 3-4 | L effort. Riduce skew tra editor. |
| FE-SPLIT-005 | `components/nodes/result/ResultNodeStandardView.jsx` 880 LOC | LATER | M effort. |
| FE-SPLIT-006 | `pages/FileManager.jsx` 887 LOC | LATER | STRUCTURAL DEBT classificato. |
| FE-TEST-001 | Coverage hotspot pages/components | CLOSED | Major batches completed for manager pages, ScheduleList, LogViewer, ProjectList, ResultNode gating, PublishEseguibileModal, RunsTab, ParameterInput, ResponseConfigBuilder, PivotMappingBuilder, editor guards/save-failure and high-ROI residual branches (`19cc007`). Remaining items are low-ROI, fragile, or covered by FE-SPLIT/A11Y/design-token tracks. |
| FE-TEST-002 | `useColumnPropagation` hardcoded transform_op | DROP | Verificato `applyTransformation` ha switch completo. |
| FE-TEST-003 | `ApiEnrichNode` outputColumns loop | DROP | Mitigato lato code (normalize-then-compare guard). |
| FE-A11Y-001 | Accessibility audit (ARIA, focus mgmt, contrast) | CLOSED | Batch chiusi: accessible names su controlli icon/search/toggle, semantica/focus management per tutti i dialog identificati, policy globale `:focus-visible`, disclosure keyboard per LogViewer/RunsTab rows e contrast token bump. ProjectList cards restano mouse convenience safe perche' hanno bottoni interni keyboard-accessible. Edge cases contrast low-ROI accettati; riaprire solo su regressione concreta o nuovo custom modal. |
| FE-PERF-001 | Bundle size analysis + code splitting | LATER | vite-bundle-visualizer setup. M effort. |

---

## 6. Productization gaps

| Item | Status | Note |
|---|---|---|
| **README root** | CLOSED | Wave 1.5 (#50). Centralizzazione `docs/` Wave 1.6 (questa PR). |
| **docs/development/frontend.md** | CLOSED | Wave 1.5 (#50) come `frontend/README.md`, migrato in Wave 1.6. |
| **docs/development/onboarding.md** | CLOSED | Wave 1.5 (#50) come `docs/ONBOARDING.md`, migrato in Wave 1.6. |
| **docs/architecture/overview.md** | CLOSED | Wave 1.5 (#50) come `docs/ARCHITECTURE.md`, migrato in Wave 1.6. |
| **docs/product/roadmap.md** (questo file) | CLOSED | Wave 1.5 (#50) come `docs/ROADMAP.md`, migrato in Wave 1.6. |
| **docs/development/contributing.md** | CLOSED | Wave 1.5 (#50) come `CONTRIBUTING.md`, migrato in Wave 1.6. |
| **docs/development/changelog.md** | CLOSED | Wave 1.5 (#50) come `CHANGELOG.md`, migrato in Wave 1.6. |
| **docs centralization → root `docs/`** | CLOSED | Wave 1.6 (#51). `git mv` di tutti i docs sostanziali sotto `docs/{product,architecture,development,ops,compliance}/`. |
| **`orchestrate/` workspace import** | CLOSED | Wave 1.7 (questa PR). 58 file (55 md + 3 evidence non-md) importati in `docs/audits/orchestrate/` — Phase 0-6 snapshot read-only. Safety scan: zero credentials, IP/email/path già pubblici. |
| Setup checklist nuovo operatore ops | NEXT | `handoff.md` esiste ma incompleto. Collegato a R-32. |
| Demo dataset / quickstart end-to-end | LATER | M effort. Demo CSV + curl examples + screenshot. |
| API reference navigabile | LATER | OpenAPI esposto via FastAPI ma non linkato in modo discoverable. S effort. |
| `code-map.md` refresh | LATER | Stato ~Aprile 2026. S audit. |

---

## 7. Capability services / direzione futura

> **Status: ROADMAP, not current.** Direzione architetturale, NON
> commitment di implementazione. Trigger: richiesta business concreta.

Idea: separare **core platform** (oggi: ETL no-code) da **capability
services** (plug-in specializzati per dominio).

Esempi di capability service pensabili:

- **AI assist**: suggerimento transform da contesto canvas, NL ->
  pipeline draft, anomaly detection sui run.
- **Satellitare / GIS avanzato**: ingestion raster, reproject batch,
  zonal stats, NDVI/NDWI builder come transform nodes.
- **Streaming**: source/sink event-based (Kafka / Pulsar).
- **Data catalog**: lineage cross-progetto, column-level metadata.
- **Multi-tenant SaaS**: tenant isolation hardening, billing meter.

Vedi [architecture overview §9](../architecture/overview.md#9-capability-services--ai--extension-layer)
per dettaglio.

---

## 8. Phasing proposto

### Phase 2 (post-launch immediato, ~giugno 2026)

Focus: **chiudere gap operativi e debiti security ad alta priorita'**.

- ~~R-01 rate-limiter Redis-backed~~ → **CLOSED** (Redis-backed sliding-window + in-memory fallback)
- R-30 DB snapshot pre-deploy
- R-33 DR drill execution (manuale ops)
- ~~FE-TEST-001 hotspot coverage~~ -> **CLOSED** (high-ROI residuals covered; do not re-open without concrete regression)
- OBSERV-001 observability sink decision implementation
- Setup checklist nuovo operatore ops

### Phase 3 (Q3 2026)

Focus: **compliance automation + test reliability completion**.

- R-28 SBOM CI hook
- R-29 SAST scanner
- R-35 SBOM frontend
- ~~R-05 clock injection scheduler~~ -> **CLOSED** (clock seam + heartbeat tests migrated)
- FE-SPLIT/A11Y/design-token work only when it is structural or product-driven, not as FE-TEST-001 residue
- R-21/R-22/R-23 removal effettiva alias deprecated (target 2026-Q4)

### Phase 4 (Q4 2026)

Focus: **frontend split + productization avanzata**.

- FE-SPLIT-004 EditorCore extraction
- FE-CLN-002 design tokens migration
- FE-A11Y-001 accessibility audit
- FE-PERF-001 bundle size + code splitting
- API reference navigabile + demo dataset
- R-31 rollback scriptato

### Phase 5-7 (2027+)

Focus: **structural refactor + capability services**.

- R-19 arcgis_client.py split
- R-20 enrichment_task.py split
- STRUCT-001 file_service.py remaining I/O seams (upload/storage/preview) after pure helper extraction
- STRUCT-002/003/004/005/006 a seguire
- Capability services al trigger business

---

## 9. Come aggiornare questa roadmap

1. Apri PR docs-only.
2. Sposta voci tra Status (NEXT → CLOSED quando merged, NEXT → LATER
   quando rinviato, ecc.).
3. Aggiorna evidence (PR #/commit hash).
4. Se aggiungi nuova voce, includi: ID, descrizione 1-line, evidence
   `file:line` o ADR ref, status, effort qualitativo.

Indice ADR principale:
[`docs/architecture/decisions/README.md`](../architecture/decisions/README.md).
