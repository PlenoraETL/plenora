# ADR-0016: IMDF boundaries refactor (loader / persist / storage / router)

**Status**: Accepted
**Date**: 2026-04-23
**Deciders**: @CloudIA

## Context

Il cluster IMDF (`backend/app/core/imdf/`) si e' accumulato per iterazioni: `loader` pre-multi-host, `persist` estratto da `enrichment_task` per isolare un cleanup bug, `storage` aggiunto in R2 multi-host, `router` che ha progressivamente assorbito sia concerns HTTP sia concerns filesystem. Il design doc `orchestrate/phase_1_stabilization/d3_imdf_boundaries.md` (Phase 1, 2026-04-20) ha mappato 7 overlap concreti emersi dall'accumulo:

1. **§3.1** `site_id` risolto in 4 call site con 3 euristiche diverse (loader `_build_bundle_index`, persist `plenora_metadata.json`, router `get_bundle_metadata` scan dir, router `list_bundles` fallback scan).
2. **§3.2** Hydration da S3 duplicata in 4 call site con policy incoerenti — in particolare `/validate-publish` non hydratava, producendo `BUNDLE_MISSING` falso-positivo su bundle solo-S3.
3. **§3.3** Cleanup `tmp_dir` ownership condivisa loader↔persist, documentata solo nei commenti.
4. **§3.4** `persist.py` chiamava inline `storage.save_bundle` — inversione di dipendenza: modulo locale conosce modulo remoto.
5. **§3.5** Path-traversal guard `_BUNDLE_ID_RE` rideclarata in 3 file (router, validator, worker).
6. **§3.6** Schema `plenora_metadata.json` implicito: scritto da persist, letto da 4 reader con shape non dichiarata.
7. **§3.7** Bundle solo-S3 senza local cache — 3 policy divergenti (list OK, get richiedeva local, delete richiedeva hydrate).

Gli invariant pre-esistenti (ADR-0007 Protocol `ObjectStorage`, ADR-0011 FS dispatch, `invariants.md §1.7`) coprivano il dispatch result/file_source ma non il cluster IMDF come insieme. Mancava il livello "boundary dichiarato per modulo IMDF".

Phase 2 (`orchestrate/phase_2_hardening/kickoff.md §3 Batch 1`) apre track `A-D3` con vincolo: **nessun tocco a `backend/app/core/imdf/**` finche' l'execute plan non e' scritto e approvato**. Il plan (`orchestrate/phase_2_hardening/d3_execute_plan.md`, 2026-04-22) e' stato ratificato con 7 decisioni bloccanti/forti (R1-R7) prima di qualunque commit impl.

## Decision

Refactor boundary-per-modulo in **5 commit impl atomici** ratificati da questo ADR. Ogni commit chiude uno o piu' overlap del design doc, con signature pubbliche invariate e semantic end-user preservata (tranne 2 fix esplicitamente documentati: hydrate `/validate-publish` e invariant meta write).

| Commit | Hash | Scope | Overlap chiusi |
|---|---|---|---|
| 1 | `e86340e` | `validate_imdf_task_id` centralizzata in `app.shared.path_policy`; 3 delegation (router, validator, worker) | §3.5 |
| 2 | `4d95a85` | `_hydrate_local_cache` in `validate_publish_endpoint` (parita' `/publish`, `/bundles/{id}`) | §3.2 + §3.7 parte |
| 3 | `432b003` | `site_id` letto solo da `meta["site_id"]` nel router; `ImdfBundle.site_id` invariant dichiarato (R4) | §3.1 + §3.7 parte |
| 4 | `8593370` | `persist.py` non chiama piu' `storage.save_bundle`; worker `enrichment_task` orchestra persist→storage | §3.4 |
| 5 | `0d1b851` | `__init__.py` riflette moduli reali + boundary summary; schema `plenora_metadata.json` dichiarato in docstring; fix §9.6 meta write failure → rollback dest + raise | §3.3 + §3.6 + §9.6 |

Boundary dichiarati post-refactor (design §4):

- **loader** = input decoder. Autoritativo per `bundle.site_id` (R4: mai `None`; stringa vuota = euristica fallita). Zero side effect runtime oltre `tempfile.mkdtemp`.
- **persist** = local materializer puro. Move tmp_dir → `IMDF_BUNDLES_DIR/{task_id}/`, scrive `plenora_metadata.json` da `bundle.site_id`, cleanup `tmp_dir` garantito in `finally`. **Non** conosce S3 (R2).
- **storage** = cross-node bundle backup. Zip + upload + hydrate idempotente. **Non** conosce `ImdfBundle` (solo path e bytes).
- **router** = HTTP boundary + ACL + cache policy decisions (R3). Path-traversal via `app.shared.path_policy.validate_imdf_task_id`, group ACL fail-closed, `_hydrate_local_cache` chiamato da tutti gli endpoint GET/POST che leggono il bundle.
- **validator** = read-only report pre-publish. Usa `validate_imdf_task_id` come router. Chiamato dal router che ha gia' hydrato la cache; internamente non tocca `storage`.

## Rationale

Per ciascuna ratifica R1-R7 (acquisite nel checkpoint 2026-04-22) una motivazione architetturale:

- **R1** — hydrate `/validate-publish` **router-level** (non validator): coerenza con §4.4 "router e' piano di controllo cache HTTP"; pattern gia' usato da `/publish` (`router.py:633`) e `/bundles/{id}` (`router.py:339`). Tenere il validator puro preserva la sua testabilita' senza import cross-module su `storage`.
- **R2** — `persist` perde `storage.save_bundle`: layering inverso (persist "locale" conosceva storage "remoto"); error handling opaco sul best-effort S3 warning; test coupling (testare persist richiedeva mock di storage, e viceversa). Persist storico driver = "cleanup garantito su `shutil.move` failure"; quel valore va preservato in un modulo dedicato, non diluito.
- **R3** — `site_id` solo in loader; router legge `meta["site_id"]`: 3 euristiche divergenti erano a rischio di drift silenzioso; la scan del router e' piu' fragile (assume "unica sottodir non-.gdb con manifest/geojson") della euristica loader (manifest → candidate sorted).
- **R4** — invariant `ImdfBundle.site_id` mai `None`: pattern type-safe per evitare null ambigui; stringa vuota come unico sentinel per "euristica fallita" (bundle malformato), downstream reader non devono distinguere `None` da `""`.
- **R5** — mantenere `BUNDLE_MISSING` anche su hydrate fail: no allineamento FE richiesto, semantic "non disponibile qui e ora" e' invariata dal punto di vista del caller. Introdurre `HYDRATE_FAILED` sarebbe stato un cambio API.
- **R6** — numerazione ADR-0016: prossimo libero dopo ADR-0015 (MinIO provider).
- **R7** — scope lock no observability/streaming/retry: deadline produzione 2026-05-05; applicare il principio "minimum viable refactor". I follow-up sono registrati in §7 qui sotto, non dispersi nei 5 commit.

Per la decisione chiave `persist boundary` (design §8), 4 opzioni sono state considerate e 1 scelta (D):

## Alternatives considered

- **A — Status quo**: persist continua a chiamare storage inline. — *Rejected*: layering inverso persiste, error handling opaco S3, test coupling.
- **B — Assorbimento in storage** (`storage.persist_and_upload`): un solo modulo per materializzazione. — *Rejected*: viola single-concern (mesce "locale" con "remoto"), disperde il bug-driver storico di persist (cleanup garantito su `shutil.move` failure).
- **C — Assorbimento in loader** ("extract + persist"): caller unico. — *Rejected*: rompe invariant "loader zero side-effect oltre `tempfile.mkdtemp`" che tiene puliti i test.
- **D (scelta) — Persist resta modulo distinto, perde solo la chiamata a storage**: worker diventa orchestrator esplicito (`extract_imdf_zip` → `persist_imdf_bundle_to_disk` → `storage.save_bundle`). Persist = local materializer puro; storage = remote sync puro. Worker decide policy `logger.warning` best-effort.

Meta-decisioni rejected o deferred:
- **Introdurre `orchestrator.py` nuovo modulo** — *Rejected* (YAGNI): il worker `enrichment_task` e' gia' orchestratore di altre operazioni multi-modulo.
- **Rimozione totale di `has_gdb` iterdir fallback e counts fallback in `list_bundles`** — *Scope lock* (R7/commit 3 strict): coerenti con principio "router legge meta" ma fuori da scope site_id letterale; lasciati come dead-code da ripulire in follow-up.
- **Observability `imdf_metadata_read_fail_total`** — *Deferred* follow-up (R7).
- **Retry/enqueue su S3 upload failure** — *Deferred* follow-up (design §9.8).
- **Streaming bundle >1GB** — *Deferred* Phase 3 (design §10).

## Consequences

### Positive

- **7 overlap chiusi** (§3.1-§3.7 design doc); boundary per modulo dichiarati e testabili.
- **`site_id` single source** end-to-end (loader → persist meta → router/validator reader); parity test verde (`test_persist_writes_loader_site_id_unchanged`).
- **Bug latente `/validate-publish` bundle solo-S3** risolto: hydrate aggiunto, report valido invece di `BUNDLE_MISSING` falso-positivo.
- **Security tightening collaterale**: ACL ora applicata cross-node (prima `_assert_bundle_group_access` era skippata per bundle solo-S3 — mascherava esistenza senza decisione esplicita).
- **Invariant "meta sempre presente nel dest dir"** enforced (§9.6): bundle dir senza meta impossibile post-fix; rollback + raise al posto di warning+continue.
- **Utility condivisa** `app.shared.path_policy.validate_imdf_task_id`: regex + path-traversal guard deduplicati da 3 a 1 (se policy cambia, un solo punto da aggiornare).
- **`__init__.py` coerente** col codice reale: `processor` fantasma rimosso, 6 moduli reali citati, boundary D3 + ADR-0016 referenziati.
- **Test aggiunti**: 25 test contract in 5 file (`test_path_policy_imdf`, `test_validate_publish_hydrate_cross_node`, `test_site_id_resolution_single_source`, `test_persist_storage_decoupling`, `test_plenora_metadata_schema`). Full suite IMDF + correlati: 106/106 pass, zero regressione.

### Negative

- **Latenza `/validate-publish` +1-5s su cache miss**: parity con `/publish`, accettato (design §9.2).
- **Cambio di behavior §9.6**: meta write failure ora solleva invece di warning+continue. Worker `enrichment_task` catch-all gia' gestisce. Test esistenti (solo happy path) non impattati.
- **Bundle legacy pre-metadata + superadmin**: response `site_id=None` invece di valore derivato da scan. FE tollera Optional. Non-superadmin erano gia' 403 fail-closed (comportamento preservato).

### Neutral

- **Posto del `logger.warning` S3 upload** spostato da persist a worker. Stesso processo, stesso wording, stesso modulo logger chain — grep operativi invariati.
- **Scan residuo `gdb_path`/`ifc_path`** in `router.get_bundle_metadata`: mantenuto (i nomi file non sono nel meta come Path, solo come bool `has_gdb`/`has_ifc`).
- **Fallback `has_gdb` + `_count_features`** in `list_bundles`: mantenuti (scope lock commit 3 strict; dead-code su bundle con meta completo).

## References

- Source:
  - `backend/app/shared/path_policy.py` — `validate_imdf_task_id` + `_IMDF_TASK_ID_RE`
  - `backend/app/core/imdf/__init__.py` — boundary + invariant summary
  - `backend/app/core/imdf/loader.py:67` — `ImdfBundle` class docstring (invariant `site_id`)
  - `backend/app/core/imdf/persist.py` — schema `plenora_metadata.json` + fix §9.6
  - `backend/app/core/imdf/router.py:302` — hydrate in `validate_publish_endpoint`
  - `backend/app/core/imdf/router.py:355` — `get_bundle_metadata` legge meta
  - `backend/app/core/imdf/validator.py:169` — delegation utility path_policy
  - `backend/workers/tasks/enrichment_task.py` §776-820 — orchestration persist→storage
- Commit impl: `e86340e`, `4d95a85`, `432b003`, `8593370`, `0d1b851`
- Related ADR: [ADR-0007](0007-2026-04-21-protocol-runtime-checkable-vs-abc.md) (Protocol runtime_checkable), [ADR-0011](0011-2026-04-21-file-sources-dispatch-s3-threshold.md) (FS dispatch symmetry), [ADR-0012](0012-2026-04-21-preview-execute-policy.md) (Preview vs execute policy)
- Design doc: `orchestrate/phase_1_stabilization/d3_imdf_boundaries.md`
- Execute plan: `orchestrate/phase_2_hardening/d3_execute_plan.md`
- Invariants: `invariants.md §1.7.bis` (IMDF ortogonale, boundary ratificato), `§6` (D3 chiuso Phase 2)
- Follow-up (fuori D3):
  - Observability `imdf_metadata_read_fail_total` come signal unico di drift meta
  - Retry/enqueue su S3 upload failure (design §9.8)
  - Streaming bundle >1GB (design §10, scope Phase 3)
  - Cross-node hydrate error mapping 404 vs 503 (design §9.9)
  - Rimozione fallback `has_gdb` + `_count_features` in `list_bundles` (dead-code post-D3)
