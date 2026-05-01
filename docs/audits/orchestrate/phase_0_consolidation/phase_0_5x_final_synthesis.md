# Phase 0.5X — Final Synthesis

Status: **decision-ready synthesis. No code change, no test, no deploy.**
Scope: chiusura naturale della Phase 0.5X estesa. Aggrega 9 documenti
analitici in raccomandazioni operabili.

Input documenti (in `orchestrate/phase_0_consolidation/`):
- `wide_audit_2026_04_28.md` (mappa allargata Phase 0)
- `hot_file_deep_dive_0_5a.md` (rev. evidence-deep) — 5 hot file BE
- `frontend_wide_audit_0_5b.md` — 7 hot file FE
- `compat_residue_evidence_0_5c.md` — 8 marker COMPAT-RESIDUE + Q1–Q4
- `test_coupling_roadmap_0_5d.md` (rev. evidence-deep) — 38 test file
- `deploy_ops_migration_design_0_5e.md` (+ Codex note batch A1) — 15 R + 9 step
- `deploy_ops_second_pass_0_5e.md` — patch plan P1–P5 decision-ready
- `performance_baseline_0_5f.md` — baseline + raccomandazione MONITOR
- `ux_product_readiness_0_5g.md` — 15 flussi utente + 10 quick wins

---

## 1. Executive summary

Plenora-ETL è un sistema **governable ma non ancora industrializzato**.

I dati raccolti convergono su 4 punti:

1. **Il prodotto funziona end-to-end e i fix critici sono chiusi**: 26 bug registrati in `BUG.md` chiusi, 64/64 test verdi nel batch A1 storage, 6/6 result post-cleanup su object_storage, Wave 1+2 success. Le 3 feature core (estrai/manipola/arricchisci) sono usabili, eseguibili e schedulabili. Auth + MFA + N1 cross-tenant + MIME defense + lock_lost + idempotency sono blindati da test contract.

2. **L'osservabilità esiste ma non è aggregata**: 8 marker `[PERF]` emessi su 7 file (s3_download, fingerprint_ms, db_ms, backend=object_storage|db_legacy, ecc.), `intermediate_run_report.py` per drift detection, audit guard cross-file scanner per err_msg/silent except. Ma non c'è dashboard che li legga, niente p50/p95 endpoint API, niente queue depth Celery, niente web vitals frontend. La regola "MONITOR before OPTIMIZE" è coerente con lo stato attuale.

3. **Il debito strutturale è concentrato in 5 hot file backend e 7 frontend**, ma la conta dei blocker test era pessimistica: 6 file con S genuini + 3 cross-file scanner (vs ~20 stimati inizialmente). 3 dei 5 hot BE refactor possono procedere a 0h test modernization (`arcgis_client`, `file_service`, `execution_manager` heartbeat). Solo `enrichment_task` richiede ~14h harness Celery prerequisito.

4. **Il gap product-grade più visibile è ops/deploy + bus factor**: deploy.sh con auto-commit + no health gate + PEM hardcoded OneDrive Marco + frontend pre-swap distruttivo. Bus factor 1 ammesso esplicitamente in `incident_response.md`. Nessuno staging. Nessun GHA deploy. Nessun alerting. Step 0 deploy.sh hardening (P1–P5) è la patch a payoff/rischio migliore disponibile, ~4–4.5h totale, decisione finale: **implementabile subito**.

I 7 sub-stream Phase 0.5 hanno prodotto evidenza concreta sufficiente per scegliere le prime 2 mosse senza aprire altri batch di analisi. Una sola voce resta in evidence-pending (Q1–Q4 SQL su prod DB per compat residue) — non blocca le decisioni di sintesi.

---

## 2. Rating qualità per area

Scala: **A** = product-grade, **B** = funzionale ma con gap, **C** = sotto product-grade.

| Area | Rating | Sintesi evidence |
| --- | :---:| --- |
| **Auth + AuthZ** | A | MFA TOTP, JWT logout blacklist, rate-limit per IP, N1 cross-tenant, audit_action_coverage, login enumeration guard. Manca: self-service password reset, JWT httpOnly cookie. |
| **Storage policy + object storage** | A | Batch A1 fail-closed, validate_storage_config gate, separate IAM boundary BACKUP_BUCKET vs OBJECT_STORAGE_BUCKET, 6/6 result post-cleanup, marker `backend=object_storage|db_legacy`. Manca: lifecycle/retention attiva. |
| **Intermediate persistence** | A | Policy modes (metadata_only/preview_only/full_pg/full_artifact), fingerprint sample 16ms, `execute_values` 154s→42s, drift detection script, semantica 422/501 esplicita su compare. |
| **File service** | A | DB-only storage, MIME defense in depth, ZIP slip protection, N1 fail-closed, RB.5 race-condition fix, 7 test contract behavior-bound. |
| **ArcGIS integration** | B+ | C12 Bearer header + retry 401/498/499 + 429 backoff + write_batch refresh mid-batch. Manca: unit test su `_authenticate_token` strategies, file 2476 LOC mono-classe. |
| **Scheduler** | B | Lock heartbeat + lock_lost propagation + retry budget transient, orphan detection, scheduled_for catchup lineage, err_msg sanitization. Manca: PlenoraBeatScheduler custom (debt), alerting su fallimenti ricorrenti, cron preview UI. |
| **Enrichment + tasks** | B | Idempotency guard, N9 user re-check, batch parallel fail-hard RB.#14, columns_metadata propagation, result_id propagation. Manca: dedup `_trigger_carica_if_present` (bug-magnete), refactor 3 primitive su task 1430 LOC. |
| **Pipeline orchestrator** | B+ | Streaming response, artifact resolver con verify project_id, hard-fail mismatch. |
| **Backend tests** | B+ | 38 test file sui 5 hot file, 6 source-bound genuini + 3 cross-file scanner. CI gate verde. |
| **Frontend architecture** | B | Modularizzazione decente (sub-components estratti in ProjectList), boundary architectural test (no http import diretto). Manca: web vitals, ReactFlow profiler, validation client-side, a11y. |
| **Frontend tests** | C+ | ~21 test su 7 hot file (~10× ratio backend). FileManager 1 e2e, ApiConnectionManager 4, ProjectList 2, ResultNodeStandardView 13. Coverage minima per refactor sicuri. |
| **UX flows** | B-/B | 15 flussi mappati: 6 product-grade, 8 sotto, 1 misc. Friction trasversali (`confirm()` browser inconsistente, error type-specific assenti, a11y assente). |
| **Performance / observability** | C+ | Marker `[PERF]` esistono ma non aggregati. 0 dashboard. Niente p50/p95, niente web vitals, niente queue depth. Solo `BUG.md` numbers (#22, #24, Wave 2) come baseline. |
| **Deploy / ops** | C | deploy.sh: auto-commit, PEM hardcoded OneDrive, no health gate, frontend pre-swap distruttivo. Niente staging. Niente GHA deploy. Bus factor 1 documentato. IaC parziale (solo lifecycle.tf). |
| **Operability admin** | B-/C+ | DR drill periodici (incident_response, dr_drill_log/playbook), `/health` + `/ready` endpoint, dashboard admin/system-status (incompleto, 36 LOC BE). Manca: runbook centrale, alerting esterno, queue/throughput dashboard. |
| **Documentation / ADR** | B+ | invariants.md, ADR chain 0001-0017, runtime-prereqs, backup-restore, jwt-incident-response. Manca: deploy checklist (pre/post), operational changelog. |
| **Security audit posture** | A | 13-round audit chiuso, 80+ fix convergenti a zero, source-bound test guard cross-file (err_msg, silent except, breadcrumb). |
| **Compat residue** | B (pending) | 8 marker (2 emit + 6 docstring) classificati E1–E5 + A1/A4 + CR1–CR6. Q1–Q4 SQL queries pronte ma in attesa di esecuzione su prod DB. **No removal autorizzato senza Q1–Q4.** |

**Sintesi rating**: 8 aree A, 6 aree B+/B, 4 aree B-/C+, 1 area C (deploy/ops). Le aree A coprono il **dominio applicativo core** (auth, storage, intermediate, file). Le aree C/C+ coprono il **dominio operativo** (deploy, observability, frontend coverage). Pattern coerente con un MVP funzionante che deve maturare il livello operativo.

---

## 3. Top priorità finali ordinate

Ranking unificato cross-stream basato su ratio (impatto × urgenza) / (effort × rischio).

| Rank | ID | Area | Cosa | Effort | Rischio | Sblocca |
| ---:| --- | --- | --- | ---:| --- | --- |
| **1** | **DEPLOY-STEP0-A** | Ops | Batch 1 deploy.sh come **wrapper parametrico multi-environment**: P5 (profilo + PEM env override) + P1 (auto-commit guard + branch guard parametrico) + P2 (pre-deploy validate via URL profilo) + P3 (post-restart `/ready` gate via URL profilo) | ~4h | basso | R1, R2 parz, R4, R7-checklist; abilita 2nd operator + multi-environment foundation |
| **2** | **OBSERVABILITY-AGG** | Perf | Aggregare marker `[PERF]` già emessi: dashboard SQL (Grafana/Metabase) su `logs.details` JSONB. Q-PERF-1..6 (vedi 0.5f §5.1) | ~3h SQL + ~3h dashboard | trascurabile (read-only) | MONITOR per tutti i refactor futuri |
| **3** | **UX-QUICK-WINS-1** | UX | W2.1+W6.4 (`confirm()` → `useConfirmModal` cross-page) + W4.4 (messaging `skipped_idempotent`) + W5.1 (compare modal messaging per reason) + W4.5 (banner permission_revoked) | ~5h | basso | UX consistency cross-flow |
| **4** | **DEPLOY-STEP0-B** | Ops | Batch 2 deploy.sh: P4 atomic frontend swap via symlink + retain N=3 | ~1.5h + precondizione server | medio (one-shot precondizione) | R5; rollback FE istantaneo |
| **5** | **REFACTOR-EXEC-MGR** | BE | execution_manager.py heartbeat context manager (c1 small refactor) | ~4h | basso | 0h test mod (heartbeat test sono behavior-bound) |
| **6** | **REFACTOR-FILE-SVC** | BE | file_service.py split helper classi (FormatDetector/TypeInferrer/FileIdGenerator/ColumnInfoBuilder) | ~5.5h | basso | 0h test mod, riduce file da 1847→~1250 LOC |
| **7** | **REFACTOR-ARCGIS** | BE | arcgis_client.py auth submodule extraction (c1–c4 in 4 commit) + add unit test `_authenticate_token` | ~9h + ~2h test prerequisito | basso | 0h test mod esistente |
| **8** | **TEST-MOD-WAVE-1+2** | Test | Modernizzare test `err_msg_sanitize` + `celery_result_sanitization` (wave 1) + `lock_lost_propagation` + `scheduled_for_persist` + `n15b` (wave 2) | ~8h | medio | Sblocca refactor `execute()` futuro |
| **9** | **UX-QUICK-WINS-2** | UX | W4.1 (current step name in progress) + W7.1 (toast type-specific ApiConnectionManager) + W10.1 (cron preview) + W4.3 (cancel button) | ~10h | basso | Friction reduction in flussi caldi (F4, F7, F10) |
| **10** | **HARNESS-CELERY** | Test | Costruire `tests2/_helpers/celery_eager.py` (eager_mode + spy hooks) — prerequisito per refactor enrichment_task | ~5h | basso | Sblocca rank 11 |
| **11** | **REFACTOR-ENRICHMENT** | BE | enrichment_task.py extract 3 primitives + dedup `_trigger_carica_if_present` (c1–c3) | ~13h | medio | Solo dopo rank 10 |
| **12** | **OPS-RUNBOOK** | Ops | Documentare deploy-checklist.md (pre+post da 0.5e §2–3) + procedura recovery MFA admin + bus factor 2 doc handoff | ~4h | trascurabile | Operability + bus factor parz |
| **13** | **REFACTOR-FE-RESULTNODE** | FE | ResultNodeStandardView split GROUP BY/ORDER BY/HAVING/FORMAT in 4 sub-component | ~4–6h | basso | 13 test behavior-bound già verdi |
| **14** | **STAGING-SETUP** | Ops | EC2 staging + DNS + DB schema separato + SSM `/plenora/staging/*` + deploy.sh `--env` flag | ~5–7gg | medio | Sblocca GHA deploy futuro |
| **15** | **GHA-DEPLOY** | Ops | OIDC role + workflows/deploy.yml staging+prod + approval gate prod | ~2–3gg | medio | Solo dopo rank 14 |

Rank 1–3 sono **immediati e indipendenti**. Rank 4–6 sono refactor a 0h test prerequisito. Rank 8 abilita rank 5 follow-up (split execute). Rank 10 è prerequisito hard di rank 11. Rank 14 prerequisito di rank 15.

---

## 4. Quick wins implementabili subito

Selezione ad alta confidence: tutti questi sono **a basso rischio**, **a effort bounded**, **non richiedono prerequisiti**.

| ID | Area | Cosa | Effort | Riferimento |
| --- | --- | --- | ---:| --- |
| QW-1 | Ops | **Documenta deploy checklist** pre+post (deriva da 0.5e §2–3) come `backend/docs/ops/deploy-checklist.md` | ~2h | 0.5e §2–3 |
| QW-2 | UX | **`confirm()` → `useConfirmModal`** in ProjectList.jsx:154 e ApiConnectionManager.jsx:228 | ~1h | 0.5b §7.4, 0.5g W2.1 |
| QW-3 | UX | **Tooltip SRID** in FileManager (default 4326 spiegato) | ~0.5h | 0.5g W6.1 |
| QW-4 | UX | **Banner `permission_revoked`** dedicato in F4 (vs toast generico) | ~1h | 0.5g W4.5 |
| QW-5 | UX | **Compare modal messaging** per ogni `reason` (`metadata_only_no_rows`, `mode_mismatch`, `full_artifact_diff_not_implemented`, `preview_only`) | ~2h | 0.5g W5.1 |
| QW-6 | UX | **Messaging `skipped_idempotent`** in F4 ("Run già completato, mostro cached") | ~1h | 0.5g W4.4 |
| QW-7 | UX | **Toast type-specific ApiConnectionManager** save/test (URL malformed, auth missing, ecc.) | ~2h | 0.5g W7.1 |
| QW-8 | Ops | **Step 0 batch 1**: P5+P1+P2+P3 in `deploy.sh` (single PR) | ~3h | 0.5e2 §1, §9.2 |
| QW-9 | Perf | **Q-PERF-1..6 SQL queries** documentate (read-only su `logs.details`) — anche solo come saved queries Metabase/Grafana | ~3h | 0.5f §5.1 |
| QW-10 | UX | **Current step name nel progress** F4 (es. "Esecuzione nodo: 'geocoding'") | ~2h | 0.5g W4.1 |
| QW-11 | UX | **Cron preview** "Prossime 3 esecuzioni" in ScheduleWizard | ~2h | 0.5g W10.1 |
| QW-12 | UX | **Toast specifico per endpoint failing** in ProjectList loadData | ~1h | 0.5g W2.2 |

**Tot quick wins: ~21h**. Nessuno di questi richiede prerequisiti. Possono essere shippati in parallelo a rank 5–7 refactor.

---

## 5. Refactor candidates finali small/medium/large

Aggregato di 0.5a (BE) + 0.5b (FE) + 0.5d (test gate).

### 5.1 Small (effort ≤6h, 0h test mod prerequisito)

| ID | Target | Cosa | Effort | Test gate | Stato |
| --- | --- | --- | ---:| --- | --- |
| S1 | `execution_manager.py` | Heartbeat context manager (LockHeartbeat class) | ~4h | 7 test heartbeat behavior-bound già verdi | **proceed** |
| S2 | `arcgis_client.py` | Auth submodule extraction (c1+c2 di 4 commit totali) | ~3h | `test_arcgis_token_bearer_contract` (10 test) behavior-bound | **proceed**, parziale |
| S3 | `file_service.py` | Split helper classi (FormatDetector + TypeInferrer + FileIdGenerator + ColumnInfoBuilder) | ~5.5h | 7 test file behavior-bound, eventuale 1h import update | **proceed** |
| S4 | `ResponseConfigBuilder.jsx` | Pure functions extraction (`responseConfig.js`) + JsonTreeNode separato | ~3h | 0 test prerequisito (no test attuale, gap noto) | **proceed**, ma aggiungere unit test su `analyzeJsonStructure` cycle |
| S5 | `ParameterInput.jsx` | 6 list inputs in modulo separato + ColumnSelector | ~3h | 0 test attuali | **proceed**, low-risk presentational |
| S6 | `ResultNodeStandardView.jsx` | Split GROUP BY / ORDER BY / HAVING / FORMAT in 4 sub-component | ~4–6h | 13 test behavior-bound verdi | **proceed** |
| S7 | `FileManager.jsx` | `useFileUploadWizard` hook + FileCard estratto | ~4h | 1 test e2e + smoke manuale | **proceed**, con visual smoke |

### 5.2 Medium (effort 6–18h, prerequisito test mod o harness)

| ID | Target | Cosa | Effort | Prerequisito | Stato |
| --- | --- | --- | ---:| --- | --- |
| M1 | `arcgis_client.py` | Full split (c3 ArcGISAuth + c4 write batch) | +6h dopo S2 | Add unit test `_authenticate_token` (~2h) | **proceed dopo S2** |
| M2 | `enrichment_task.py` | Dedup `_trigger_carica_if_present` con orchestrator helper condiviso | ~6h | Harness Celery (~5h) | **dopo HARNESS-CELERY** |
| M3 | `enrichment_task.py` | Extract `_resolve_run_context` + `_finalize_run` (3 primitives) | +7h dopo M2 | M2 + modernizzazione 3 test source-bound (~9h) | **dopo M2** |
| M4 | `ApiEnrichNode.jsx` | `useApiEnrichNodeData` hook + split executable/standard mode | ~10h | boundary test passa invariato | **proceed**, con visual smoke esteso |
| M5 | `ApiConnectionManager.jsx` | Wizard step extraction in 6 sub-component + `<AuthForm />` | ~13h | 4 test contract behavior-bound | **proceed** post-quick wins |
| M6 | `execution_manager.py` | Split `execute()` in 5 fasi | ~16h | wave 2 modernizzazione test (~6h) | **dopo wave 2** |

### 5.3 Large (effort > 18h, prerequisiti pesanti)

| ID | Target | Cosa | Effort | Stato |
| --- | --- | --- | ---:| --- |
| L1 | Frontend versioning canvas schema + migration | ~5gg dev | **rinviato** post-refactor BE |
| L2 | Streaming `get_result_data` end-to-end (no full-buffer) | ~3gg dev | richiede MONITOR rank 2 prima |
| L3 | Diff L2 da `full_artifact` Parquet (DuckDB) | ~5gg dev | backlog BUG.md, non urgente |
| L4 | WebSocket/SSE per task progress real-time | ~5gg dev | richiede assessment di carico + gateway |
| L5 | Migrazione PlenoraBeatScheduler custom → Celery beat standard | ~3gg dev | scheduler stable, no urgenza |
| L6 | Self-service password reset + MFA recovery (SMTP) | ~5gg dev | richiede infra SMTP |

### 5.4 No-op (deliberato, non toccare)

| Target | Razionale |
| --- | --- |
| `app/core/manipola/transforms/spatial.py` (1941 LOC) | 32 funzioni stateless + 0 source-bound test + 0 caller diretti. Splittare per dimensione è il pattern che il prompt vieta. |
| `pages/ProjectList.jsx` (862 LOC) | Già fattorizzato in 4 sub-component (ProjectSection, ProjectCard, EseguibileCard, useEmptyHexCanvases). Defensive `loadData` con `.catch(() => default)` per ogni endpoint. Non c'è coupling reale che giustifichi ulteriore extraction. |
| `useCanvasStore.js` (429 LOC) | Zustand store ben fattorizzato; rinominarlo o splittarlo senza trigger è churn. |
| `api/*` boundary modules (7 file) | Pattern boundary architectural già al livello desiderato. Verificato da `apiEnrichNode.boundary.contract.test.js` + `connections.boundary.contract.test.js`. |
| `utils/permissions.js` (107 LOC) | Pure functions stateless. |

---

## 6. Ops/Deploy roadmap finale

Sequenza ratificata in 0.5e + 0.5e2 con decisione Step 0 = **proceed**, **revisionata sotto vincolo multi-environment**.

### 6.0 Vincolo multi-environment (architetturale)

Il sistema deve poter essere installato in futuro **su più server con configurazioni diverse** (es. prod, staging, cliente-X, on-prem isolato). Step 0 non è solo "sistemare lo script di Marco": è **il primo passo verso un deploy parametrico per ambiente**.

Implicazioni:
- Server, PEM, remote path, app URL, health URL devono essere configurabili **per ambiente**, non hardcoded.
- Profili `prod` / `staging` / `client-x` devono essere selezionabili senza modificare il codice dello script.
- Nessun **nuovo hardcode** ammesso. Default presenti, ma sempre overridable.
- `.env` e runtime config restano **per-installazione** (sul server, mai nel repo).
- Variabili per-environment cloud (`OBJECT_STORAGE_BUCKET`, `BACKUP_BUCKET`, `AWS_REGION`, `STORAGE_POLICY`) **non possono essere assunte uguali tra environment**.
- GHA deploy futura (Step 3 originale) dovrà poter scegliere il target environment via input — quindi la separazione "configurazione environment" vs "logica deploy" deve emergere già in Step 0.

Pattern raccomandato: introdurre una directory `deploy/profiles/` (versionata) che contenga **profili pubblici** (host, path, app URL, health URL, frontend domain) ma **nessun secret**. Il profilo viene selezionato via env var `PLENORA_DEPLOY_PROFILE` o flag `--profile=<nome>`. I secret (PEM path, AWS creds) restano per-operatore (env override).

### 6.1 Step 0 — deploy.sh come wrapper parametrico

**Batch 1 (rank 1, QW-8): P5 + P1 + P2 + P3** — ~4h totali (revisione +~1h vs stima precedente per overhead profile abstraction), single PR.

Note di applicazione del vincolo multi-environment:

- **P5 (PEM/SERVER → profilo)** — non più solo env override "PEM/SERVER", ma estrazione completa di tutti i parametri per-environment in profilo selezionabile:
  - File `deploy/profiles/<profile>.conf` (o YAML) per-environment con: `SERVER`, `REMOTE_BACKEND_PATH`, `REMOTE_FRONTEND_PATH`, `APP_HEALTH_URL`, `APP_BASE_URL`, `BACKEND_PORT`. **Nessun secret.**
  - Selezione via `PLENORA_DEPLOY_PROFILE=<nome>` o flag `--profile=<nome>`. Default `prod`.
  - PEM path resta per-operatore: `PLENORA_DEPLOY_PEM` env (default invariato per Marco), check `[ -f "$PEM" ]`.
  - **Niente nuovo hardcode**: il profilo `prod` esistente (Marco's setup) viene esso stesso committato come `deploy/profiles/prod.conf` con i valori attuali — ma resta sostituibile.
  - Log iniziale: `Deploy verso profilo=$PROFILE server=$SERVER (PEM: $(basename $PEM))`.

- **P1 (auto-commit guard + branch guard)** — invariato logicamente, ma il branch guard deve essere parametrico per profilo:
  - Default profilo `prod` accetta solo `main`.
  - Profilo `staging` (futuro) accetta `main` o `develop`.
  - Profilo `client-x` (futuro) potrebbe accettare un branch dedicato.
  - Configurato via `ALLOWED_BRANCHES` nel profile config, non hardcoded.

- **P2 (pre-deploy validate)** — usa l'URL del profilo:
  - `curl -fsS "$APP_HEALTH_URL"` invece di `curl http://127.0.0.1:5004/health`.
  - `validate_storage_config()` resta runtime (Python lato server) — **stessa funzione**, ma legge `.env` per-installazione (per-server), che può differire fra environment (`STORAGE_POLICY=object_storage` su prod, `=hybrid` su staging cliente, ecc.).
  - `check_deps()` SSH al server selezionato dal profilo.
  - Implicazione: la validazione cattura **drift fra profile config e .env del server target**, non assume valori globali.

- **P3 (post-restart `/ready` gate)** — usa `APP_HEALTH_URL`/`APP_READY_URL` del profilo:
  - Polling su URL parametrico, non `127.0.0.1:5004` hardcoded.
  - `systemctl is-active <unit names>` — i nomi unit potrebbero differire per environment (`plenora-backend` vs `plenora-staging-backend`?). Mettere in profile config: `SYSTEMD_UNITS="plenora-backend plenora-celery plenora-beat"`.
  - Timeout di readiness configurabile per profilo (`READY_TIMEOUT_SEC=60` default, override possibile per setup con cold-start più lenti).

Tutti read-only sul server (nessuna mutazione persistente fino al `git pull` esistente). Rollback singolo per patch via `git revert`.

**Batch 2 (rank 4, separato): P4 atomic frontend swap** — ~2h + precondizione server (revisione +~0.5h per parametrizzazione).
- Symlink `dist → dist-{sha}/` con retain `N` configurabile (default `RETAIN_FRONTEND_VERSIONS=3` nel profile).
- `REMOTE_FRONTEND_PATH` viene dal profilo, non hardcoded `/home/ubuntu/plenora-etl/frontend/`.
- Precondizione one-shot manuale (per ogni environment al primo deploy con P4): `mv dist dist-initial && ln -s dist-initial dist` sul server target.
- Tenuto separato per il rischio precondizione + possibile interazione con nginx config (verifica follow-symlinks per environment).

### 6.1.1 Struttura raccomandata profile config

```
deploy/profiles/
  prod.conf          # versionato, nessun secret
  staging.conf       # versionato (vuoto/template oggi)
  README.md          # come aggiungere un nuovo profilo
```

Contenuto profilo (illustrativo, non vincolante):
- `SERVER` (es. `ubuntu@host.example.com`)
- `REMOTE_BACKEND_PATH`
- `REMOTE_FRONTEND_PATH`
- `APP_HEALTH_URL`
- `APP_READY_URL`
- `APP_BASE_URL`
- `BACKEND_PORT`
- `SYSTEMD_UNITS`
- `ALLOWED_BRANCHES`
- `READY_TIMEOUT_SEC`
- `RETAIN_FRONTEND_VERSIONS`

**Mai nei profili**: PEM path, AWS access keys, SSM credentials, `.env` content. Tutto questo resta per-operatore o per-installazione (sul server target, fuori dal repo).

### 6.1.2 Cosa NON è in scope per il vincolo multi-environment in Step 0

- Provisioning di un nuovo environment (creazione EC2, DNS, RDS, S3 buckets) → Step 5 IaC.
- Sincronizzazione SSM `/plenora/<profile>/*` parameter store → Step 4 + Step 2.
- GHA workflow che seleziona profilo → Step 3.
- Editor automatico di `.env` per-environment → out of scope (resta manuale, vedi §6.2).

Step 0 introduce **solo l'astrazione profilo lato deploy.sh**, non la creazione di ambienti reali. Il singolo profilo `prod` esistente è il primo (e unico finora) profilo concreto. Aggiungerne altri richiede il lavoro infrastrutturale degli step successivi.

### 6.2 Cosa resta manuale anche dopo Step 0

(da 0.5e2 §7)
- Aggiornamento `.env` sul server: SSH + edit + restart.
- Migration DB: auto-apply al boot, ma deploy.sh non gate su pending.
- AWS SSO/credential refresh: manuale.
- systemd unit edit: manuale.
- Notifiche utenza: nessun canale automatico.
- Bus factor: anche con P5, conoscenza pratica resta su Marco.

### 6.3 Step 1+ (post Step 0, progetti separati)

Ordine ratificato in 0.5e §4 + 0.5e2 §8, **rivisto sotto vincolo multi-environment**:

| Step | Cosa | Effort stimato | Prerequisito |
| --- | --- | ---:| --- |
| Step 2 | Setup primo environment **non-prod** (es. staging): EC2 + DNS + DB schema separato + SSM namespace `/plenora/<env>/*` + nuovo `deploy/profiles/<env>.conf`. **Validazione vera del vincolo multi-environment**: il deploy.sh con Step 0 deve poter targetare il nuovo environment senza modifiche al codice | ~5–7gg + costi infra | Step 0 |
| Step 3 | GHA OIDC role + `workflows/deploy.yml` parametrico con input `environment: prod|staging|<name>` + approval gate per profili production-tier | ~2–3gg | Step 2 |
| Step 4 | Migrazione runtime services `.env` → SSM **per-environment** (`/plenora/<env>/*`), uniforma con backup units, chiude R6/R8. Lo schema deve garantire che la stessa codebase legga secret diversi per environment | ~1–2gg | Step 0 |
| Step 5 | IaC esteso multi-environment (Terraform workspace o module pattern): bucket S3 per env, IAM, KMS, SSM skeleton, EC2, RDS — via `terraform import` su risorse esistenti per profilo `prod`, nuove per `staging`/`client-x` | ~5–7gg | nessuno (read-only su risorse esistenti) |
| Step 6 | CI deploy gate: `validate_storage_config` con env (multi-profile) in CI + tests + validation green | ~1gg | Step 3 |
| Step 7 | DR drill esteso: rebuild infra da Terraform su account/profile separato | ~3gg | Step 5 |
| Step 8 | Bus factor 2: secondo operatore con accesso vault PEM (per-profile path possibili), AWS read+limited-write, runbook firmato + tabletop exercise | ~1–2gg | nessuno (organizzativo) |

### 6.4 Implicazione del vincolo multi-environment sull'ordine

Step 2 diventa il **vero stress test** del vincolo:
- Se Step 0 ha introdotto correttamente l'astrazione profilo, aggiungere `deploy/profiles/staging.conf` e farlo girare deve essere **lavoro di configurazione**, non di codice.
- Se invece emergono hardcode residui (es. nome systemd unit assunto, path remoto fisso, nome bucket assunto), Step 2 li scoprirà — patch ulteriori a `deploy.sh` saranno necessarie ma circoscritte.
- È un test naturale anti-regressione: un secondo profilo concreto valida che il primo profilo (`prod`) non abbia introdotto assunzioni nascoste.

### 6.5 Out-of-scope ratificati

(da 0.5e §5.3)
- Multi-tenant deploy (1 cliente = 1 stack).
- Blue-green / canary (overkill per setup attuale).
- Kubernetes (1 EC2 sufficiente).
- Service mesh / observability avanzata (Datadog/Sentry paid).
- ChatOps / Slack-bot deploy.

---

## 7. UX/Product roadmap finale

Sequenza derivata da 0.5g §15–16.

### 7.1 Quick wins UX (rank 3 + rank 9)

12 quick wins (vedi §4 di questa sintesi). Effort totale ~21h. Possono procedere in parallelo a Step 0 deploy + refactor BE.

Priorità interna:
- **Tier 1 (UX consistency)**: QW-2, QW-4, QW-5, QW-6 — ~5h totali. Risolvono inconsistenze cross-page e messaging dedicato per stati noti.
- **Tier 2 (productivity)**: QW-10, QW-11, QW-7 — ~6h. Migliorano produttività operatore.
- **Tier 3 (polish)**: QW-3, QW-12, QW-1 (deploy doc), QW-9 (perf SQL queries) — ~10h. Documentation + saved queries.

### 7.2 Interventi più grandi (post-quick wins)

(da 0.5g §16, ordinati per impatto)

| Rank | ID | Cosa | Impatto |
| ---:| --- | --- | --- |
| 1 | L4.1 | WebSocket/SSE per progress real-time | UX task lunghi |
| 2 | L5.1 | Diff L2 da full_artifact (DuckDB) | Sblocca compare avanzato, chiude backlog BUG.md |
| 3 | L8.1 | Streaming `get_result_data` per file > soglia | Riduce peak memory worker |
| 4 | L1.2 | Self-service password reset + SMTP | Onboarding senza admin |
| 5 | L10.1 | Migrazione Celery beat standard | Chiude debt PlenoraBeatScheduler |
| 6 | L3.1 | Versioning canvas schema + migration | Sblocca evoluzione nodi |
| 7 | L1.1 | JWT → httpOnly cookie + CSRF | Security posture |
| 8 | L4.3 | Pre-execution estimate | UX produttività |
| 9 | L10.2 | Alerting su fallimenti ricorrenti | Operability |
| 10 | L3.3 | Conflict detection multi-user editing | Sblocca multi-utente |

### 7.3 A11y (debt strategico, rinviato)

A11y completamente assente (0.5g §12.5). Per prodotto B2B con clienti pubblici IT (Comune, Stazioni FF.SS.) potrebbe diventare requisito normativo (AgID, WCAG 2.1 AA). **Da valutare appena emerge esigenza commerciale**, non parte di Phase 0.5.

---

## 8. Cosa NON toccare ora

Lista esplicita per evitare che il churn divori il payoff dei rank 1–7.

### 8.1 No-op deliberati (vedi §5.4)

- `spatial.py` — splittare per dimensione = trappola del prompt
- `ProjectList.jsx` — già ben strutturato
- `useCanvasStore.js` — store Zustand fattorizzato
- `api/*` boundary modules — pattern già al livello desiderato
- `utils/permissions.js` — pure functions

### 8.2 Compat residue removal

**Niente removal autorizzato senza Q1–Q4 SQL queries eseguite su prod DB** (vedi §9 evidence pending). I 6 marker docstring + 2 emit COMPAT-RESIDUE restano in code finché Q1–Q4 non confermano log evidence.

### 8.3 Refactor che richiedono harness non ancora costruito

- `enrichment_task.py` extract primitives (M2, M3) — richiede HARNESS-CELERY rank 10 prima.
- `execute()` split in 5 fasi (M6) — richiede wave 2 test modernization.

### 8.4 Ottimizzazioni performance senza MONITOR

(da 0.5f §8)
- **Nessun OPTIMIZE raccomandato** prima di rank 2 (OBSERVABILITY-AGG). I marker `[PERF]` esistono ma non sono aggregati. La regola "MONITOR before OPTIMIZE" è rigorosa.

### 8.5 Step 1+ deploy senza Step 0

GHA, staging, IaC esteso sono progetti separati. **Step 0 deve essere chiuso prima**, altrimenti si moltiplica complessità senza il foundation hardening.

### 8.6 Frontend refactor pre-test-coverage-uplift

Rank 13 (`ResultNodeStandardView` split) è OK perché ha 13 test. Ma `ApiEnrichNode.jsx` (M4) richiede visual smoke esteso prima di estrazione hook — 1 boundary test non basta. Suggerimento: aggiungere test contract base per ApiEnrichNode prima del refactor M4.

---

## 9. Evidence pending / decisioni aperte

### 9.1 Q1–Q4 compat residue (0.5c)

**Stato**: queries SQL pronte, esecuzione su prod DB pending.
- Q1 emit dispatch path coverage (telemetry per E1)
- Q2 ArcGIS auth strategies usage (per E2 referer/requestip removal)
- Q3 OAuth wrapper pattern usage (per A2 docstring scope)
- Q4 URBY_ALLOW_LOOPBACK_OUTBOUND env presence (per A3 cleanup)

**Decisione**: `Q1–Q4 restano evidence-pending, non bloccano la sintesi`. Nessuna rimozione compat autorizzata senza output. Esecuzione richiede operatore con accesso prod DB (~30 min execution + ~30 min interpretation).

### 9.2 Phase 1 vs continuare Phase 0

Decisione strategica aperta. Le opzioni:

| Opzione | Pro | Contro |
| --- | --- | --- |
| **Aprire Phase 1 batched** (rank 1, 2, 3 in parallelo) | Sblocca refactor + UX + observability insieme. Foundation hardening. | Richiede coordinamento tra 3 stream. |
| **Solo Step 0 deploy + QW** (rank 1, 3) | Single stream, basso rischio aggregato. Mantiene focus operativo. | Lascia observability gap aperto. |
| **Solo OBSERVABILITY-AGG** (rank 2) | Sblocca tutto il MONITOR-before-OPTIMIZE futuro. | Niente cambio user-visible immediato. |

**Raccomandazione**: opzione "**Aprire Phase 1 batched**", con Step 0 batch 1 come **prima azione concreta** (rank 1, vedi §10).

### 9.3 Decisioni architetturali aperte

- **Migrazione legacy globale fuori 6 progetti** (BUG.md backlog): row con `storage_key=NULL AND data NOT NULL` non rigenerati. Decisione: rinviata "solo se serve operativamente". OK.
- **Lifecycle/retention object storage attiva**: `lifecycle.tf` esiste ma non confermato applicato. Decisione aperta: applicare ora o post-Step 5 IaC.
- **`CHECK` DB su `plenora.results`** (`data IS NOT NULL OR storage_key IS NOT NULL`): proposed defense-in-depth, da introdurre solo dopo migration legacy globale. OK.
- **A11y prioritization**: rinviato a trigger commerciale. OK.

### 9.4 Bus factor 2

Decisione organizzativa, non tecnica. Step 0 P5 (PEM env override) è l'abilitatore tecnico ma non risolve da solo. Necessari:
- Vault condiviso per PEM (1Password, AWS Secrets Manager, ecc.)
- Secondo operatore con accesso AWS read+limited-write
- Runbook letto + tabletop exercise

Effort: 1–2gg organizzativi. Decisione: aprire post-Step 0 batch 1.

---

## 10. Next action raccomandata: primo batch concreto

**Primo batch**: rank 1 (DEPLOY-STEP0-A — batch 1 P5+P1+P2+P3 **come wrapper parametrico multi-environment**, scope deliberatamente minimale).

### 10.0 Scope minimale ratificato (Step 0 piccolo)

Step 0 deve restare **piccolo e contenuto**. Lo scope ratificato è esattamente:

**In scope batch 1**:
- Profile loader minimale (legge `deploy/profiles/<name>.conf`, applica defaults)
- Profilo `prod` come **primo e unico profilo concreto** committato
- Required vars validation: lo script fallisce esplicitamente se variabili obbligatorie del profilo mancano (`SERVER`, `REMOTE_BACKEND_PATH`, `APP_HEALTH_URL`, `APP_READY_URL`, `SYSTEMD_UNITS`, `ALLOWED_BRANCHES`)
- Niente auto-commit (P1)
- Branch guard parametrico (allowlist dal profilo)
- Pre-deploy / post-restart health URL letti dal profilo (P2/P3)
- Systemd units da profilo (P3)
- PEM via env operatore (`PLENORA_DEPLOY_PEM`)

**Esplicitamente fuori scope batch 1**:
- ~~Framework deploy complesso~~ (es. Ansible, Fabric, custom orchestrator)
- ~~Profili oltre `prod`~~ (no `staging.conf`, no `client-x.conf` concreto in questo batch — solo `README.md` come template)
- ~~Staging concreto~~ (resta Step 2)
- ~~GitHub Actions workflow~~ (resta Step 3)
- ~~SSM sync automatico~~ (resta Step 4)
- ~~Editor `.env` automatico~~ (out of scope, resta manuale)
- ~~Nuovi tool/dipendenze deploy~~ (rimane bash + ssh + scp + curl; niente yq/jq/python wrapper)
- ~~Multi-target parallelo~~ (un deploy = un profilo per invocazione)
- ~~Secret management nei profili~~ (i `.conf` restano puramente public config, no PEM, no AWS keys, no `.env` content)

Questa direzione è la **forma minima** che introduce la foundation multi-environment senza scope creep. Tutto il resto è incrementale e arriva con i prerequisiti naturali (Step 2+).

### 10.1 Razionale

1. **Effort bounded**: ~4h totali, single PR.
2. **Rischio basso aggregato**: P5/P1/P2/P3 sono read-only o script-only. Rollback `git revert` per patch.
3. **Tutti i prerequisiti backend esistono**: `/health`, `/ready`, `validate_storage_config`, `check_deps`. Niente da costruire lato app.
4. **Riduce 4 rischi originali Phase 0**: R1 (auto-commit), R2 parz (PEM), R4 (no health gate), R7-checklist (validate pre-deploy).
5. **Sblocca**: 2nd operator (P5), audit trail deploy pulito (P1), incident detection (P3), **multi-environment foundation** (struttura profile config).
6. **Decisione esplicita** in 0.5e2 §9: **proceed**, revisionata sotto vincolo multi-environment §6.0.

### 10.2 Cosa include il primo batch

Aggiornato sotto vincolo multi-environment (§6.1):

| Patch | Cosa | Effort |
| --- | --- | ---:|
| P5 | Estrai parametri per-environment in `deploy/profiles/prod.conf` (versionato, no secret). Selezione via `PLENORA_DEPLOY_PROFILE=<name>` o `--profile=<name>`, default `prod`. PEM via `PLENORA_DEPLOY_PEM` env (per-operatore, non in profilo). Check `[ -f "$PEM" ]` precondizione. Log iniziale stampa profilo + server + PEM basename | 60 min |
| P1 | Rimuovi auto-commit. Branch guard parametrico (`ALLOWED_BRANCHES` letto dal profilo, default `main` per `prod`). Rifiuta deploy da branch non in allowlist | 30 min |
| P2 | `pre_deploy_check()`: SSH al server selezionato dal profilo + `validate_storage_config` + `check_deps` + `curl -fsS "$APP_HEALTH_URL"` (URL dal profilo). Exit 1 se uno fallisce | 75 min |
| P3 | Post-restart: polling `curl "$APP_READY_URL"` con `READY_TIMEOUT_SEC` dal profilo (default 60s) + `systemctl is-active $SYSTEMD_UNITS` (lista dal profilo). Exit 1 se fallisce | 75 min |

Totale ~4h dev + ~30 min review + ~30 min smoke deploy controllato (con profilo `prod`, l'unico esistente in questo batch).

### 10.3 Checklist applicazione batch 1

- [ ] PR creato con 4 commit indipendenti (P5, P1, P2, P3 in ordine)
- [ ] `deploy/profiles/prod.conf` versionato con i valori attuali (server `15.160.219.93`, path `/home/ubuntu/plenora-etl/...`, ecc.) — **nessun nuovo hardcode introdotto, solo estrazione di quello esistente**
- [ ] `deploy/profiles/README.md` documenta come aggiungere un nuovo profilo + cosa NON va dentro (secret)
- [ ] Review locale: ogni patch è revertable singolarmente via `git revert <sha>`
- [ ] Review esplicita: nessuna stringa hardcoded host/path/url residua in `deploy.sh` (grep mirato)
- [ ] Test manuale a freddo: `bash deploy.sh check-deps` continua a funzionare invariato (resta su profilo default `prod`)
- [ ] Test manuale a freddo: `bash deploy.sh` su working tree sporco → exit 1 con messaggio P1
- [ ] Test manuale a freddo: `PLENORA_DEPLOY_PEM=/tmp/inesistente bash deploy.sh` → exit 1 con messaggio P5
- [ ] Test manuale a freddo: `PLENORA_DEPLOY_PROFILE=inesistente bash deploy.sh` → exit 1 con messaggio dedicato (profilo non trovato)
- [ ] Test manuale a freddo: simula profilo locale fittizio `deploy/profiles/dryrun.conf` con SERVER=invalid → P2 fallisce su SSH unreachable → exit 1 (verifica che la parametrizzazione funzioni davvero, non solo per `prod`)
- [ ] Primo deploy reale durante finestra controllata (profilo `prod`)
- [ ] Post-deploy: checklist 0.5e2 §3 (sanity check end-to-end)
- [ ] Aggiornare `incident_response.md` con la nuova procedura rollback (`git reset --hard <SHA-PRE-DEPLOY>` + restart) + uso del profilo
- [ ] Aggiornare `runtime-prereqs.md` con il pattern profile config + variabili attese in `deploy/profiles/<name>.conf`

### 10.4 Out-of-scope batch 1

(consolidato con §10.0; ripetuto qui per evitare scope creep)

- **NON** P4 atomic frontend swap — separato in batch 2 per precondizione server (anch'esso con `REMOTE_FRONTEND_PATH` e `RETAIN_FRONTEND_VERSIONS` dal profilo).
- **NON** profili reali aggiuntivi oltre `prod` (staging, client-x). L'astrazione viene introdotta, ma il **secondo profilo concreto** è Step 2 (richiede infra reale).
- **NON** framework deploy complesso (Ansible/Fabric/custom orchestrator). Resta bash + ssh + scp + curl.
- **NON** nuove dipendenze tool (no yq, jq, python wrapper) — `deploy/profiles/<name>.conf` formato shell-source-able.
- **NON** modifiche systemd unit file.
- **NON** modifiche `.env` server (resta per-installazione, mai automatico).
- **NON** SSM sync automatico.
- **NON** GHA workflow.
- **NON** staging setup concreto.
- **NON** refactor backend o frontend.

### 10.5 Anti-regressione vincolo multi-environment

Per evitare che future patch reintroducano hardcode, raccomandazione operativa: aggiungere a `tests2/` (futuro, post-batch 1) un **contract test ad alto livello** che faccia grep su `deploy.sh` per cercare:
- IP letterali (pattern `\d+\.\d+\.\d+\.\d+`) — solo nei profili
- Path assoluti `/home/ubuntu/...` — solo nei profili
- Hostname espliciti (es. `lab.plenora.it`) — solo nei profili
- Email/Slack URL — solo nei profili

Se grep trova match dentro `deploy.sh`, il test fallisce. Implementabile in ~30 min, **non parte di batch 1** (citato qui per memoria).

### 10.5 Successivo immediato

Dopo batch 1 mergeato e validato:
1. **batch 2 P4 atomic frontend swap** (rank 4, ~1.5h) — separato.
2. In **parallelo**: rank 2 (OBSERVABILITY-AGG) + tier 1 quick wins UX.
3. Subito dopo: rank 5 (REFACTOR-EXEC-MGR) + rank 6 (REFACTOR-FILE-SVC) — entrambi 0h test prerequisito.

---

## 11. Stato Phase 0.5X

**Status: Phase 0.5X chiusa — 7 sub-stream completati con decisioni esplicite.**

| Sub-stream | Output | Decisione |
| --- | --- | --- |
| 0.5a hot-file deep dive (BE) + evidence-deep | `hot_file_deep_dive_0_5a.md` | 5 file mappati, 4 refactor proposti + 1 no-op |
| 0.5b frontend wide audit | `frontend_wide_audit_0_5b.md` | 7 file mappati, 6 refactor proposti + 1 no-op |
| 0.5c compat residue evidence | `compat_residue_evidence_0_5c.md` | Q1–Q4 SQL queries pronte, **evidence-pending** su prod DB |
| 0.5d test coupling roadmap + evidence-deep | `test_coupling_roadmap_0_5d.md` | 38 test classificati, ~24h modernizzazione max, ~2h minimo |
| 0.5e deploy/ops migration design | `deploy_ops_migration_design_0_5e.md` | 15 R + 9 step + Codex note A1 |
| 0.5e second pass | `deploy_ops_second_pass_0_5e.md` | Step 0 P1–P5 decision-ready, **proceed** |
| 0.5f performance baseline | `performance_baseline_0_5f.md` | 7 area, 7 MONITOR + 4 NO-OP + 0 OPTIMIZE |
| 0.5g UX/product readiness | `ux_product_readiness_0_5g.md` | 15 flussi, 10 quick wins (~21h) + 10 interventi grandi |
| **0.5X synthesis** (questo doc) | `phase_0_5x_final_synthesis.md` | Next action: **rank 1 = batch 1 deploy.sh hardening** |

Phase 0.5X **non ha aperto** alcun refactor, alcun test, alcun deploy, alcuna modifica codice. È analisi pura con 9 documenti decision-ready.

Prossimo step naturale: **decisione esplicita su rank 1** (batch 1 deploy.sh hardening). Se approvato, parte primo batch concreto Phase 1.

---

*End of Phase 0.5X — Final Synthesis.*
