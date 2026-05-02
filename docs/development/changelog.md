# CHANGELOG — URBY-ETL

PR raggruppate per tema. Storia decisionale, non release calendar.

Format: `feat / fix / chore / refactor / test / docs / perf` per tipo,
con scope tra parentesi. Reference `#PR` su GitHub.

---

## Phase 1 hardening — chiuso 2026-04-30

Stabilization push pre-launch. Focus: security, test reliability, ops
runbook, compat residue cleanup.

### Security & runtime

- **#35** `fix(security)`: SSRF single-source design with REST_FILE
  strict (P1-C). Anti-SSRF primitive consolidato in
  `app.shared.api_utils._check_outbound_address`, riusato dal shim
  REST_FILE strict. Rifiuta URL verso loopback/RFC1918 private senza
  opt-in.
- **#34** `test(p1-b)`: modernize logger-bound tests to behavior-bound
  (4 file). Pattern di sostituzione spy / handler over source-string.
- **#27** `fix(security)`: no leak project names cross-tenant in 409
  cascade detail (PR-δ). Cross-tenant info leak chiuso.
- **#26** `test(auth)`: add esecutore kiosk-only contract test suite
  (PR-β). Role `esecutore` blindato: lista + esegui eseguibili,
  niente accesso canvas/admin.

### Scheduler

- **#36** `refactor(scheduler)`: LockHeartbeat context manager
  (PR-#36 c1). Heartbeat estratto da inline a context manager,
  testabile e refactor-safe.
- **#37** `test(reliability)`: Phase 1 batch — env isolation +
  Celery eager harness + behavior-bound modernization.
  Foundation `tests2/_helpers/celery_eager.py` con
  `install_enrichment_task_mocks`. Modernizza 2 file enrichment_task
  contract.

### UX / consistency

- **#28** `feat(ux)`: tier-1 quick wins for async/cascade messaging
  (PR-A). Toast feedback + cascade message clarity.
- **#29** `feat(ux)`: tier-1 confirm migration for AdminBackups +
  TicketDetail (PR-A2).
- **#30** `refactor(ux)`: canvas confirm() -> ConfirmProvider +
  useConfirm context (PR-B). Native confirm() sostituito con modal
  dedicato.
- **#33** `fix(ux)`: usePreview permission_revoked banner +
  console.log cleanup. Banner dedicato per scenario N9 in preview.

### Ops & docs

- **#24** `docs(ops)`: add observability baseline + ops runbook
  minimum (A1).
- **#31** `docs(ops)`: consolidate A2 sink decision + Q1-Q4 compat
  residue runbook.
- **#32** `docs(ops)`: record B17 staging + P4 atomic frontend swap
  roadmap.

### Schema inference

- **#25** `fix(schema-inference)`: read api_enrich output_mapping +
  prefer result.columns. Bridge N11 chiuso: type inference dal canvas
  arricchito propagato a CARICA.

---

## R-25 intermediate persistence — chiuso 2026-05-01

Per-call override della `INTERMEDIATE_PERSISTENCE_MODE` globale per
audit / debug run-specific senza env+restart.

- **#39** `feat(intermediate)`: resolve_intermediate_mode pure function
  (R-25 PR-B1). Foundation kwarg validation + fail-loud su override
  invalido.
- **#40** `feat(intermediate)`: propagation
  intermediate_mode_override estrai-final (R-25 PR-B2). Kwarg
  esplicito (no stato d'istanza) propagato attraverso 7 firme dal
  boundary `QueryExecutor.execute_query` fino a
  `resolve_intermediate_policy`.
- **#41** `feat(intermediate)`: R-25 completion — B2.b
  manipola/arricchisci + B3 endpoint dual-auth + B4 ADR/runbook.
  Estende propagation a manipola/arricchisci, introduce endpoint
  HTTP `?intermediate_mode=...` superadmin-gated, ADR-0019,
  runbook §4.
- **#47** `feat(intermediate)`: R-25 PR-B5 — ExecutionModal
  intermediate_mode toggle staff-only. UI staff-only nel
  ExecutionModal frontend. R-25 al 100%.

---

## R-04 / R-04b test reliability — chiuso 2026-05-01

Modernizzazione `inspect.getsource` source-bound a behavior-bound.

- **#44** `test(security)`: R-04 N9 source-bound -> behavior-bound
  modernization. 11 inspect.getsource modernizzati: bytecode probe
  (co_names ricorsivo), spy diretti su collaborator, SQL capture via
  pg_connection.execute spy, TestClient + dependency override,
  eager mode + spy per kwarg propagation user_id.
- **#46** `test`: R-04b batch test reliability non-auth modernization
  (9 file). Pattern omogeneo R-04 esteso a:
  `test_n8_runs_retention_applied_contract.py`,
  `test_pipeline_task_idempotency_contract.py`,
  `test_carica_idempotency_contract.py`,
  `test_carica_task_retry_policy_contract.py`,
  `test_manipola_runtime_cycle_detection_contract.py`,
  `test_persist_storage_decoupling.py`,
  `test_column_types_sources_contract.py`,
  `test_n11_canvas_types_propagation_contract.py`,
  `test_eseguibili_size_cap_contract.py`.

---

## FE-22 polling reliability — chiuso 2026-04-30/05-01

Polling unificato + cleanup CSS/orphan files.

- **#42** `fix(reliability)`: FE polling single-flight via
  useTaskPolling (FE-22). `ImdfPublisherNode` + `EseguibileExecuteWizard`
  migrati al pattern `useTaskPolling` esistente. `useExecution` single-
  flight per-key per CARICA multi-key.
- **#43** `chore(cleanup)`: remove FE orphan files + dedup inline
  @keyframes spin (-353 righe).
- **#45** `fix(css)`: aggiungere selettori per 12 classi missing
  confermate (FE audit). Coverage CSS classi referenziate da JSX ma
  non definite (alias o one-liner).

---

## Compat / productization — chiuso 2026-05-01

- **#38** `chore(maintainability)`: FE alert→toast + eslint rationale
  + ADR MFA disclosure. R-10 (alert→toast in `useImdfBundles`),
  R-15 (16 eslint-disable annotate con razionale), R-26 (ADR-0018
  documenta tradeoff `403 mfa_required` vs `401 generico`).
- **#48** `chore(compat)`: R-21/22/23 deprecation warnings + ADR-0020
  + README BE productization. DeprecationWarning emit-once per 4
  alias legacy (`URBY_ALLOW_LOOPBACK_OUTBOUND`, `get_oauth2_token`,
  `get_oauth2_client_credentials_token`, `get_object_storage`),
  removal target 2026-Q4. ADR-0020 documenta i 7
  `inspect.getsource` residui come KEEP intenzionali in 3 categorie.
  README BE productization come single-source per setup/test/ADR.

---

## Phase 1 structural closure — chiuso 2026-05-02

Closeout strutturale FE/BE post-hardening. Focus: riduzione file grandi,
confini go-ready, cleanup dead code/export e documentazione zero-residuals.

### Backend

- `STRUCT-001`: `file_service.py` ridotto a facade leggera; upload
  single/multi-layer, raw upload, read/preview/storage dispatch estratti
  in moduli leaf con collaborator espliciti.
- `MULTI-GDB-001`: upload multi-layer GDB-in-ZIP corretto preservando il
  blob ZIP originale per storage.
- `BE-HARDEN-001C`: multi-layer upload go-ready: shared blob dispatch,
  reference-aware delete, batch transaction `save_file_sources_batch`,
  typed contracts `FileSourceRow`/`BlobDispatchResult`.
- `BE-DELETE-001/002`: delete object-storage esplicito e race-safe:
  `FOR UPDATE` su row/sibling, cleanup storage post-commit, indice
  parziale `idx_file_sources_storage_key`.

### Frontend

- `FE-SPLIT-004`: `EditorShell` centralizza guard/loading/error/canvas
  wrapper per Estrai/Manipola/Arricchisci; `AccessDeniedBanner`
  condiviso.
- `FE-HYGIENE-001/002/003`: dead code sweep su componenti/hook orfani,
  cleanup test noise e riferimenti stale.
- `FE-PERF-001`: `ImdfPreviewModal` lazy, maplibre/deckgl fuori dal
  grafo statico degli editor.
- `FE-PHASE1-CLOSURE-1/2/3`: common-kit unused eliminato, EditorShell
  a11y formalizzata, primo sweep inline styles, `FileManager` estratto
  in page orchestrator + subcomponents.
- `FE-FINAL-AUDIT`: export utils morti rimossi e docs aggiornati.
  FE Phase 1 e' zero-residuals operativo; `FE-CLN-002` e
  `FE-SPLIT-001/002/003/005` sono decision gate Phase 2.

---

## Pre-#26 (storia precedente, sintesi)

- **#22-#25** — fix arricchisci api_enrich single-step branch +
  schema inference output_mapping + ci-functional dataflow_test
  PG service + ci-functional cleanup PLENORA_ALLOW_LOOPBACK_OUTBOUND
  job-level env.
- **#19-#21** — fix tests HTTP status drift (201/204) + ci-functional
  nightly cron + dataflow user/db PG service.
- Pre-#19 — Phase 0 / Phase 0.5 / batch hardening pre-2026-04-25.
  Storia in commit log + ADR registry; non riassunta qui per evitare
  duplicazione con `git log`.

---

## Convenzioni questo file

- Append-only per voci storiche. Edit retroattivo solo per correggere
  factual errors.
- Raggruppamento per **tema**, non per release date.
- 1 voce per PR mergiata, con `#PR-number` come reference.
- Body breve (1-3 righe). Per dettagli: PR description + ADR.
