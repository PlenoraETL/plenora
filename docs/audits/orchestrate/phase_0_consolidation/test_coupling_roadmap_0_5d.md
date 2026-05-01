# Phase 0.5d — Test Coupling Roadmap (Evidence-deep edition)

Date: 2026-04-28 (rev. 2026-04-28 evidence-deep)
Status: **analysis only — no test modernization, no refactor opened.**

This revision keeps the recommendations and the wave structure but adds:
- exact test file paths
- per-test line numbers (function-level)
- per-test classification (B / S / A / mixed) with the *specific*
  assertion that drives the classification
- what the test really protects (one-sentence)
- concrete modernization sketch for tests that need it
- explicit "leave invariant" list with rationale
- modernization order **before** refactor commits c1/c2/...
  (cross-referenced with 0.5a evidence-deep)

Read together with:
- `hot_file_deep_dive_0_5a.md` (rev. evidence-deep)
- `phase_0_4_test_coupling_evidence.md` (initial inventory)
- `wide_audit_2026_04_28.md`

---

## 0. Definizioni e tassonomia

| Tag | Pattern caratteristico | Esempio | Comportamento bloccato |
| --- | --- | --- | --- |
| **B** | mock/monkeypatch su collaboratori, `assert_called_with`, `assert_called_once`, `pytest.raises`, return value assertions | `mock_http.get.assert_called_once()`, `monkeypatch.setattr(real_db, "save_file_source", fake_fn)` | comportamento osservabile (sopravvive a MOVE/RENAME) |
| **S** | `inspect.getsource(symbol)` + literal substring assertion; `Path.read_text()` + `"literal" in src`; `src.count(...)` | `assert "is_already_completed" in source`, `assert 'err_msg = f"Errore inatteso: {e}"' not in src` | presenza di una stringa specifica nel sorgente |
| **A** | `ast.parse(source)` + tree walk, position-based check come `init_idx < first_loader_idx` | `tree = ast.parse(source); assert ...` | struttura sintattica (più robusto a refactor cosmetici, rompe su extract method) |
| **Mixed** | un test file contiene test B + test S/A | `test_enrichment_task_idempotency_contract.py` (1 signature B + 2 source S) | classificare per-test, non per-file |
| **Cross-file scanner** | regex su sorgente di N file diversi (pattern globale "no bare except", "no str(e) in return") | `test_celery_result_sanitization.py` | invariant globale di audit |

Una falsa posizione comune: **`inspect.signature(fn)` è B**, perché la
signature è API pubblica. Solo `inspect.getsource(fn)` è S.

---

## 1. Tabella Sintetica per Hot File (invariata)

| Target | Test totali | B (test) | S (test) | A (test) | Cross-file scanner | Effort modernizzazione (h) | Cosa sblocca |
| --- | ---:| ---:| ---:| ---:| ---:| ---:| --- |
| `execution_manager.py` | 13 file / 49 test | 25 | 9 | 0 | 4 file (cross-cutting) | ~16h, ~5h se solo wave 1-2 | refactor c1/c2/c3 di 0.5a §5.6 |
| `enrichment_task.py` | 7 file / 41 test | 27 | 11 | 3 | 0 | ~14h | refactor c1/c2/c3 di 0.5a §4.6 |
| `arcgis_client.py` | 2 file / ~10 test | 10 | 0 | 0 | 0 | **0h** | refactor c1/c2/c3/c4 di 0.5a §1.6 |
| `file_service.py` | 7 file / ~50 test | 50 | 0 | 0 | 0 | **0h** | refactor c1/c2/c3/c4 di 0.5a §3.6 |
| `spatial.py` | 9 file / ~40 test | 40 | 0 | 0 | 0 | **0h** | n.a. (no-op raccomandato) |

---

## 2. `execution_manager.py` — 13 file test (49 test totali)

### 2.1 Per-test classification + line numbers

#### File 1: `tests2/scheduler/unit/test_execution_manager_contract.py` (6 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_execute_carica_uses_result_id_artifact_before_output_path` | 49 | **B** | `_execute_carica` preferisce result_id quando entrambi presenti; output_path stale è ignorato |
| `test_execute_carica_fails_when_no_run_specific_artifact_is_available` | 71 | **B** | `_execute_carica({})` ritorna `success=False` con messaggio "Artefatto della run non disponibile" |
| `test_execute_blocks_cross_group_isolation` | 93 | **B** | group_id mismatch tra schedule e project → fail-closed |
| `test_execute_blocks_schedule_without_group_id` | 110 | **B** | schedule senza group_id → fail-closed |
| `test_execute_blocks_project_without_group_id` | 126 | **B** | project senza group_id → fail-closed |
| `test_execute_passes_group_isolation_when_groups_match` | 142 | **B** | happy path con group_id matching → no isolation failure |

**Pattern**: tutti usano `ExecutionManager.__new__(ExecutionManager)` +
`MagicMock` per i sub-services. Behavior-bound. **Modernizzazione: 0h.**

#### File 2: `tests2/scheduler/unit/test_execution_manager_heartbeat_contract.py` (7 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_start_lock_heartbeat_returns_state_and_starts_thread` | 41 | **B** | thread esistente, vivo, name `heartbeat-{id}` |
| `test_heartbeat_thread_is_daemon` | 56 | **B** | `state["thread"].daemon is True` |
| `test_heartbeat_calls_refresh_lock_repeatedly` | 68 | **B** | con interval 30ms, ≥2 chiamate in 150ms |
| `test_stop_lock_heartbeat_stops_thread_deterministically` | 84 | **B** | dopo stop il thread non è più `is_alive()` |
| `test_stop_lock_heartbeat_is_idempotent` | 97 | **B** | seconda chiamata stop non solleva |
| `test_heartbeat_does_not_call_refresh_after_stop` | 107 | **B** | `refresh_lock.call_count` invariato dopo stop |
| `test_heartbeat_swallows_refresh_errors` | 121 | **B** | `refresh_lock.side_effect=Exception` → thread non muore |

**Pattern**: tutti usano `ExecutionManager()` reale (non `__new__`) con
`fake = MagicMock()` su `concurrency`. Tutti behavior-bound, testano la
*semantica* di `_start_lock_heartbeat`/`_stop_lock_heartbeat` non la
loro forma sintattica. **Modernizzazione: 0h.**

> Implicazione su 0.5a §5.6 c1 (introdurre `LockHeartbeat` context
> manager): se i metodi `_start_lock_heartbeat` / `_stop_lock_heartbeat`
> restano come thin wrapper sul context manager, **questi 7 test
> passano invariati**. Il refactor c1 può procedere senza modernizzazione.

#### File 3: `tests2/scheduler/unit/test_execution_manager_lock_lost_propagation_contract.py` (3 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_execute_source_reads_lock_lost_flag_from_heartbeat_state` | 24 | **S** | `inspect.getsource(ExecutionManager.execute)` contiene `heartbeat_state["lock_lost"]` o equivalenti |
| `test_execute_result_includes_lock_lost_key_in_result_schema` | 36 | **S** | source contiene `'"lock_lost"'` (chiave nel dict result) |
| `test_execute_logs_warning_when_lock_lost_true` | 46 | **S** | source contiene `logger.warning`/`logger.error` + linea con `lock_lost` |

**Cosa protegge davvero (intent)**: che `execute()` propaghi
`lock_lost` flag (a) nel result dict, (b) nei log con warning quando True.
Senza, la rilevazione del thread heartbeat resta silente nei log
ERROR del thread, invisibile al chiamante.

**Modernizzazione concreta** (~3h):
- harness: `_make_manager()` con `MagicMock(concurrency)` configurato per
  ritornare `False` su `refresh_lock` e `False`/`queued` su `try_acquire`
  (= simulate lock lost durante run)
- test 1 → `result = manager.execute(...)`; `assert result["lock_lost"] is True`
- test 2 → `assert "lock_lost" in result`
- test 3 → `caplog.at_level(logging.WARNING)` durante `execute()`;
  `assert any("lock_lost" in r.message.lower() for r in caplog.records)`

Attenzione: `app.utils.logger` è custom; `caplog` standard potrebbe non
catturare. Alternative: monkeypatch `logger.warning` con spy MagicMock.

**Bloccante per 0.5a §5.6 c2** (refactor `execute()` con `with` heartbeat).

#### File 4: `tests2/scheduler/unit/test_execution_manager_lock_lost_contract.py` (5 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_heartbeat_does_not_try_reacquire_when_refresh_returns_true` | 53 | **B** | `refresh_lock=[True, ...]` → `try_acquire.assert_not_called()` |
| `test_heartbeat_attempts_reacquire_when_refresh_returns_false` | 73 | **B** | `refresh_lock=[False, True, ...]` → `try_acquire` chiamato con `("sched-1",)` |
| `test_heartbeat_successful_reacquire_does_not_set_lock_lost_flag` | 94 | **B** | dopo `refresh=False` + `try_acquire=acquired` → `state["lock_lost"] is not True` |
| `test_heartbeat_failed_reacquire_sets_lock_lost_flag` | 111 | **B** | dopo `refresh=False` ripetuto + `try_acquire=queued` → `state["lock_lost"] is True` |
| `test_heartbeat_state_exposes_lock_lost_as_false_by_default` | 135 | **B** | stato iniziale `state["lock_lost"] is False` |

**Pattern**: usa `ExecutionManager()` + `MagicMock(concurrency)` con
`refresh_return_values` come side_effect sequence.
**Modernizzazione: 0h.**

> Nonostante il nome "contract", questi test sono **completamente
> behavior-bound**. La conta di 0.5a precedente li classificava
> erroneamente come 5 source-bound; sono 5 B.

#### File 5: `tests2/scheduler/unit/test_execution_manager_scheduled_for_persist_contract.py` (2 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_execute_body_uses_scheduled_for_beyond_signature` | 24 | **S** | `source.count("scheduled_for") >= 3` (anti-shadow: signature+docstring non bastano) |
| `test_execute_logs_scheduled_for_when_present` | 37 | **S** | window scan ±6 righe attorno a ogni occurrence di `scheduled_for`, almeno una vicino a `logger.`/`create_log_entry`/`schedule_info`/`execution_info`/`pipeline_info`/`open_run` |

**Cosa protegge davvero**: che `scheduled_for` (catchup tick logico)
non sia solo un parametro morto; deve essere effettivamente usato in log
e payload per audit lineage.

**Modernizzazione concreta** (~2h):
- harness: `manager.execute(schedule_id="s1", scheduled_for="2026-04-28T06:00:00Z")`
- test 1 → `assert log_service.create_log_entry.call_args.kwargs["schedule_info"]["scheduled_for"] == "2026-04-28T06:00:00Z"`
- test 2 → stesso assert su `execution_info.scheduled_for` e/o argomenti
  open_run

**Bloccante per 0.5a §5.6 c2**.

#### File 6: `tests2/scheduler/unit/test_execution_manager_orphan_and_release_contract.py` (3 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_execute_marks_schedule_as_orphan_when_project_does_not_exist` | 101 | **B** | project_meta None → `schedule_service.mark_as_orphan` chiamato + `log_service.create_log_entry(event="PIPELINE_FAILED")` + return `status="orphan"` senza acquisire lock |
| `test_finally_release_lock_retries_once_after_transient_db_failure` | 171 | **B** | `release` solleva `psycopg2.OperationalError`, secondo tentativo OK → log warning [GESTITO] + nessuna propagation |
| `test_finally_does_not_propagate_when_both_release_attempts_fail` | 242 | **B** | release fallisce 3 volte → log [CRITICO] + `execute()` ritorna comunque (no exception bubbling) |

**Pattern**: `__new__` + MagicMock + `monkeypatch` su `time.sleep`.
**Modernizzazione: 0h.**

#### File 7: `tests2/scheduler/unit/test_execution_manager_err_msg_sanitize.py` (4 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_err_msg_inatteso_no_str_e_interpolation` | 47 | **S** | `Path.read_text()` + `'err_msg = f"Errore inatteso: {e}"' not in src` + canonical string presente |
| `test_err_msg_execution_error_branch_preserved` | 69 | **S** | source contiene `if isinstance(e, ExecutionError):` + `err_msg = str(e)` entro 3 righe |
| `test_handle_failure_failed_branch_has_canonical_error_token` | 100 | **S** | `_handle_failure` failed branch contiene token canonico `"error": "schedule_execution_fatal"` |
| `test_no_schedule_execution_retry_token_added` | 141 | **S** | retry branch NON ha token aggiunto (decisione ratificata) |

**Cosa protegge davvero**: non leak di `str(e)` user-facing tramite il
return dict di Celery. Sicurezza: pre-fix `Errore inatteso: {e}` esponeva
internals (psycopg2 errors, file paths). Domain `ExecutionError` resta
safe (messaggio controllato).

**Modernizzazione concreta** (~2h):
- harness: `manager.execute()` con `_get_orchestrator` che solleva
  `RuntimeError("internal psycopg2 leak")`
- test 1 → `assert "internal psycopg2 leak" not in result["message"]` +
  `"consulta log" in result["message"]`
- test 2 → `_get_orchestrator` solleva `ExecutionError("user-safe message")`
  → `assert "user-safe message" in result["message"]`
- test 3 → assert `result["error"] == "schedule_execution_fatal"`
- test 4 → variant con retry trigger e check token assente

> **Sicurezza guard**. NON indebolire. Modernizzare = espressione come
> behavior, mantenendo la copertura.

**Non bloccante** per 0.5a §5.6 c1/c2 (i refactor non toccano questi rami).

#### File 8: `tests2/scheduler/unit/test_execution_manager_release_retry_wrapper.py` (7 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_release_retry_budget_n3_on_all_transient` | 105 | **B** | release fallisce 4 volte transient → tentativi == 3 (budget N) |
| `test_release_retry_exits_early_on_success` | 142 | **B** | secondo tentativo OK → no terzo |
| `test_release_retry_non_transient_does_not_retry` | 170 | **B** | `RuntimeError` non in (OperationalError, DeadlockDetected) → no retry |
| `test_release_retry_backoff_exponential_with_jitter` | 201 | **B** | `time.sleep` chiamato con valori in range [base*2^n*0.8, base*2^n*1.2] |
| `test_release_retry_budget_total_10s_interrupts` | 246 | **B** | totale ≤ 10s anche se N=3 non ancora esaurito |
| `test_release_retry_log_warning_per_attempt` | 290 | **B** | logger.warning chiamato per-tentativo |

**Pattern**: `__new__` + MagicMock + monkeypatch `time.sleep`.
**Modernizzazione: 0h.**

#### File 9: `tests2/scheduler/unit/test_artifact_resolution_contract.py` (2 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_execute_carica_prefers_result_id_before_output_path` | 45 | **B** | result_id presente → load_dataframe NON viene chiamato sull'output_path stale |
| `test_execute_carica_rejects_result_id_from_other_project_even_if_output_path_exists` | 70 | **B** | result_id appartenente a project_id ≠ → `success=False` con "non appartiene" + `load_dataframe.assert_not_called()` |

**Pattern**: `__new__` + `patch(...)` context managers su modules.
**Modernizzazione: 0h.**

#### File 10: `tests2/scheduler/unit/test_silent_assignment_breadcrumb.py` (3 test, **cross-file scanner**)

| Test | Line | Tipo | Scope cross-file |
| --- | ---:| --- | --- |
| `test_no_bare_silent_assignment_in_target_files` | 58 | **S/regex** | scan 4 file (`pipeline_task`, `carica_task`, `enrichment_task`, `execution_manager`) per `except Exception:\n    <ident>=` senza `as e:` |
| `test_silent_assignment_sites_have_debug_breadcrumb` | 110 | **S** | per ognuno dei 5 siti noti, presenza di `logger.debug` + `type(e).__name__` |
| `test_silent_assignment_fallback_values_preserved` | 133 | **S** | fallback semantico (return {}, =None) preservati |

**Cosa protegge davvero**: audit guard Batch 7.f-bis — zero
silent-assignment senza breadcrumb.

**Modernizzazione (NON raccomandata)**: trasformare in 5 e2e separati
(uno per sito) costerebbe ~4h e perderebbe il valore di scanner globale.
**Lasciare invariato**, aggiornare la lista file se refactor sposta
codice fuori dei target.

#### File 11: `tests2/scheduler/unit/test_celery_result_sanitization.py` (15 test, **cross-file scanner**)

| Test | Line | Tipo | Scope |
| --- | ---:| --- | --- |
| `test_schedule_task_has_canonical_token_and_message` | 59 | **S** | schedule_task |
| `test_schedule_task_return_dict_no_longer_interpolates_str_e` | 65 | **S** | schedule_task |
| `test_carica_task_outer_has_canonical_token` | 79 | **S** | carica_task |
| `test_carica_task_outer_preserves_error_details_traceback` | 86 | **S** | carica_task |
| `test_carica_task_outer_preserves_admin_internal_message` | 94 | **S** | carica_task |
| `test_carica_task_datasource_has_canonical_token` | 109 | **S** | carica_task datasource branch |
| `test_carica_task_datasource_no_str_e_in_return` | 116 | **S** | idem |
| `test_carica_task_arcgis_has_canonical_token` | 135 | **S** | carica_task arcgis branch |
| `test_carica_task_arcgis_old_interpolation_removed` | 142 | **S** | idem |
| `test_imdf_publish_task_has_canonical_token` | 154 | **S** | imdf_publish_task |
| `test_imdf_publish_task_no_str_e_in_error_field` | 160 | **S** | idem |
| `test_execution_manager_execute_carica_has_canonical_token` | 172 | **S** | execution_manager._execute_carica |
| `test_execution_manager_execute_carica_no_bare_str_e_return` | 178 | **S** | idem |
| `test_no_str_e_in_return_dict_error_field_across_all_sites` | 199 | **S** | aggregato 4 file con whitelist |
| `test_all_6_canonical_tokens_present_across_files` | 242 | **S** | aggregato sanity check |

**Cosa protegge davvero**: audit Batch 9 §3.1 / S1 — zero leak
`str(e)` nei return dict user-facing dei task Celery.

**Modernizzazione (NON raccomandata)**: 6 e2e per ognuno dei 6 token
canonici. Costo ~5h, perdita di controllo aggregato.
**Lasciare invariato**.

#### File 12: `tests2/scheduler/unit/test_cleanup_breadcrumb_coverage.py` (3 test, **cross-file scanner**)

| Test | Line | Tipo | Scope |
| --- | ---:| --- | --- |
| `test_no_bare_except_pass_in_target_files` | 60 | **S/regex** | 5 file: zero `except Exception:\n    pass` bare |
| `test_each_cleanup_site_has_logger_debug_breadcrumb` | 132 | **S** | per ognuno dei 15 siti, presenza `logger.debug` |
| `test_cleanup_sites_count_matches_scope` | 158 | **S** | sanity: count totale siti |

**Cosa protegge davvero**: audit Batch 7.f — breadcrumb su silent
cleanup. **Lasciare invariato**.

#### File 13: `tests2/scheduler/unit/test_n15b_scheduled_for_always_set_contract.py` (5 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_task_source_has_scheduled_for_fallback_logic` | 48 | **S** | source di `schedule_pipeline_task` contiene `"scheduled_for is None"` o `"scheduled_for = scheduled_for or"` + `datetime.now`/`utcnow` |
| `test_task_preserves_explicit_scheduled_for` | 66 | **B** | mock `ExecutionManager.execute`; assert kwargs ricevuti contengono lo `scheduled_for` esplicito |
| `test_task_computes_scheduled_for_when_none_for_cron` | 96 | **B** | scheduled_for=None + triggered_by="cron" → kwargs ricevuti hanno scheduled_for ISO format |
| `test_task_computes_scheduled_for_when_none_for_manual` | 129 | **B** | idem manual |
| `test_two_cron_invocations_without_scheduled_for_get_different_values` | 157 | **B** | due chiamate distinguibili |

**Cosa protegge davvero**: il task `schedule_pipeline_task` (file
`workers/tasks/schedule_task.py`) **non execution_manager**, ma rilevante
perché tocca lo stesso meccanismo di `scheduled_for` propagation.

**Modernizzazione (test 1 only, ~1h)**: `manager_mock = MagicMock()`;
`schedule_pipeline_task("s1", triggered_by="cron")` (senza
scheduled_for); assert `manager_mock.execute.call_args.kwargs["scheduled_for"]`
è una stringa ISO ben formata e ≠ None.

### 2.2 Riepilogo per `execution_manager.py`

| File | Test S genuini | Test B | Modernizzare? |
| --- | ---:| ---:| --- |
| 1. contract | 0 | 6 | No (0h) |
| 2. heartbeat | 0 | 7 | No (0h) |
| 3. lock_lost_propagation | 3 | 0 | **Sì (~3h)** |
| 4. lock_lost (≠ propagation) | 0 | 5 | No (0h) |
| 5. scheduled_for_persist | 2 | 0 | **Sì (~2h)** |
| 6. orphan_and_release | 0 | 3 | No (0h) |
| 7. err_msg_sanitize | 4 | 0 | **Sì (~2h)** |
| 8. release_retry_wrapper | 0 | 7 | No (0h) |
| 9. artifact_resolution | 0 | 2 | No (0h) |
| 10. silent_assignment_breadcrumb | 3 | 0 | **No** (cross-file scanner, lasciare) |
| 11. celery_result_sanitization | 15 | 0 | **No** (cross-file scanner, lasciare) |
| 12. cleanup_breadcrumb_coverage | 3 | 0 | **No** (cross-file scanner, lasciare) |
| 13. n15b_scheduled_for | 1 | 4 | **Sì** test 1 (~1h) |

**Totale modernizzazione consigliata: ~8h** (file 3 + 5 + 7 + n15b test 1).

Wave 1 (~2h): file 7 (err_msg_sanitize) — security guard, isolato.
Wave 2 (~6h): file 3 + 5 (sblocca 0.5a §5.6 c2 `execute()` refactor) +
n15b test 1.

---

## 3. `enrichment_task.py` — 7 file test (41 test)

### 3.1 Per-test classification + line numbers

#### File 1: `tests2/arricchisci/unit/test_enrichment_task_idempotency_contract.py` (3 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_enrichment_task_accepts_run_id_kwarg` | 21 | **B** | `inspect.signature(execute_enrichment_task).parameters["run_id"]` esiste con default None |
| `test_enrichment_task_source_checks_idempotency_when_run_id_passed` | 31 | **S** | source contiene `is_already_completed` o `idempotency` |
| `test_enrichment_task_skipped_idempotent_returns_early` | 41 | **S** | source contiene `skipped_idempotent` |

**Cosa protegge davvero**: idempotency guard C4 round 3 — retry con
stesso run_id non rieseguono.

**Modernizzazione concreta (~1h, dopo harness)**:
- test 2 → harness: monkeypatch `is_already_completed` per ritornare True;
  `execute_enrichment_task.apply(args=[..., run_id="r1"])`;
  `assert result["status"] == "skipped_idempotent"`
- test 3 → idem, assert direttamente sulla shape del return

#### File 2: `tests2/arricchisci/unit/test_enrichment_task_tenant_binding_lock.py` (10 test, 33 inspect.getsource refs)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_execute_enrichment_task_does_not_import_singleton` | 28 | **S** | source modulo NON contiene `import api_connection_service` (singleton) |
| `test_execute_enrichment_task_instantiates_local_api_service` | 50 | **S** | source contiene `import ApiConnectionService` + `ApiConnectionService()` |
| `test_execute_enrichment_task_sets_current_group_id_before_loaders` | 66 | **A** | `init_idx = source.find("._current_group_id =")`; `first_loader_idx = min(...)`; `assert init_idx < first_loader_idx` (verifica posizionale AST-style) |
| `test_execute_enrichment_task_propagates_result_id_in_dataframe_branch` | 104 | **S** | window scan attorno a `rows_processed": len(final_df)` per `"result_id"` |
| `test_execute_enrichment_task_propagates_result_id_in_file_branch` | 130 | **S** | window scan attorno a `"output_type": "file"` per `"result_id"` |
| `test_update_task_progress_accepts_explicit_task_id` | 151 | **B** | signature di `update_task_progress` ha kwarg `task_id` keyword-only |
| `test_execute_enrichment_task_propagates_task_id_in_progress_calls` | 190 | **A** | `ast.parse(source)`; tree walk per ogni Call a `update_task_progress`; assert ognuna passa `task_id=task_id` kwarg |
| `test_execute_enrichment_task_eseguibile_branch_fetches_group_id` | 225 | **S** | source ramo `is_eseguibile` contiene `get_eseguibile` + assignment `_group_id = (_eseg.get("group_id") or "")` |
| `test_execute_enrichment_task_imdf_branch_has_no_undefined_locals` | 257 | **A** | ast walk per assert no `NameError` potenziale nel ramo IMDF |
| `test_execute_enrichment_task_module_has_no_orphan_singleton_helpers` | 282 | **S** | source modulo non contiene definitions per helper deprecati `find_latest_output_file`, `load_dataframe_from_file` (alias rimossi) |

**Cosa protegge davvero (8 invariants)**:
1. tenant binding race-safe (no singleton condiviso fra task concorrenti)
2. `_current_group_id` settato PRIMA dei loader (fail-closed)
3. `result_id` in entrambi i rami (download via /api/tasks/{id}/download)
4. `task_id` esplicito a `update_task_progress` (Celery thread-local fix)
5. branch eseguibile risolve group_id da tabella eseguibili
6. branch IMDF non lascia variabili locali non definite
7. modulo non contiene helper orfani

**Modernizzazione concreta (~7h, dopo harness)**:
- test 1 → harness con `apply()` + spy su `_current_group_id` setter
  (es. tramite mock di `ApiConnectionService` che traccia `__init__` e
  setter); assert NON c'è chiamata al singleton.
- test 2 → spy su `ApiConnectionService.__init__`; assert chiamato.
- test 3 → spy con timeline sequenza chiamate; assert
  `_current_group_id setter call timestamp` < `_load_*` first call timestamp.
- test 4 → harness con apply() ramo dataframe; assert `result["result_id"] is not None` quando saved successfully
- test 5 → harness con apply() ramo file_result (executable); idem
- test 6 → resta B (signature check), 0h
- test 7 → harness con `update_task_progress` mockato + spy;
  invocare `_run_single_node` da thread; assert call_args.kwargs include `task_id`
- test 8 → harness ramo eseguibile con `get_eseguibile` mockato;
  assert `_load_*` chiamato con group_id corretto
- test 9 → integration test ramo IMDF (manca scenario, da costruire)
- test 10 → pure source check su nomi simboli; **possiamo lasciarlo S**
  perché un audit guard "no orphan helper" ha valore aggregato.

**Bloccante per 0.5a §4.6 c1, c3.**

#### File 3: `tests2/arricchisci/unit/test_batch_parallel_merge_contract.py` (6 test)

| Test | Line | Tipo |
| --- | ---:| --- |
| `test_merge_raises_runtime_error_on_cardinality_mismatch_explosion` | 60 | **B** |
| `test_merge_raises_runtime_error_on_cardinality_shrink_dedup` | 91 | **B** |
| `test_merge_runtime_error_does_not_mutate_node_results_on_failure` | 108 | **B** |
| `test_merge_combines_columns_when_all_nodes_same_cardinality` | 139 | **B** |
| `test_merge_skips_non_dataframe_node_results` | 176 | **B** |
| `test_merge_does_not_emit_silent_warning_on_failure` | 208 | **B** |

**Pattern**: importa `_merge_batch_parallel_node_results` direttamente,
costruisce DataFrame, assert su raise / behavior.
**Modernizzazione: 0h.** Sopravvive a qualsiasi refactor del task,
perché testa il helper module-level (riga 46 di `enrichment_task.py`).

#### File 4: `tests2/arricchisci/unit/test_enrichment_task_columns_metadata_propagation.py` (2 test)

| Test | Line | Tipo | Cosa protegge davvero |
| --- | ---:| --- | --- |
| `test_enrichment_task_uses_helper_no_silent_warning` | 17 | **S** | `Path.read_text()` su enrichment_task.py: contiene `save_columns_metadata_with_retry` + NON contiene "Impossibile salvare columns_metadata" (warning silenzioso pre-fix) |
| `test_enrichment_task_save_error_propagates_to_celery` | 33 | **B** | mock `update_project_columns_metadata.side_effect = TypeError`; `pytest.raises(ColumnsMetadataSaveError)` |

**Modernizzazione test 1 (~1h, dopo harness)**: harness con mock
`save_columns_metadata_with_retry` che solleva `ColumnsMetadataSaveError`
non-transient → assert task FAILED.

#### File 5: `tests2/arricchisci/unit/test_init_execution_context_fail_closed.py` (5 test)

Testa `app/core/arricchisci/api_connector.py:_init_execution_context`,
**NON** `enrichment_task.py`. Falso blocco in 0.5a precedente.
**Modernizzazione: 0h** (e fuori scope).

#### File 6: `tests2/arricchisci/unit/test_node_may_explode_cardinality_contract.py` (12 test)

Testa `app/core/arricchisci/parallel.py:_node_may_explode_cardinality`
+ `compute_parallel_groups`. **Falso blocco** in 0.5a precedente.
**Modernizzazione: 0h** (e fuori scope).

#### File 7: `tests2/arricchisci/unit/test_generate_error_report_secret_redaction.py` (6 test)

Testa `app/core/arricchisci/enrichment_service.py:generate_error_report`
+ secret redaction nel CSV. **Falso blocco** in 0.5a precedente.
**Modernizzazione: 0h** (e fuori scope).

### 3.2 Riepilogo per `enrichment_task.py`

| File | Test S/A genuini | Test B | Note |
| --- | ---:| ---:| --- |
| 1. idempotency | 2 | 1 | Modernizzare 2 test (~1h) |
| 2. tenant_binding_lock | 9 (5 S + 3 A + 1 sopravvive S) | 1 | **Bloccante**, ~7h |
| 3. batch_parallel_merge | 0 | 6 | Sopravvive (helper module-level) |
| 4. columns_metadata_propagation | 1 | 1 | ~1h |
| 5. init_execution_context | 0 | 5 | **Fuori target** (api_connector) |
| 6. node_may_explode | 0 | 12 | **Fuori target** (parallel.py) |
| 7. error_report_secret | 0 | 6 | **Fuori target** (enrichment_service) |

**Totale modernizzazione consigliata (per sbloccare 0.5a §4.6): ~9h** +
~5h harness Celery (riusabile per altri task) = **~14h**.

---

## 4. `arcgis_client.py` — 2 file test

### 4.1 Per-test classification

#### File 1: `tests2/security/unit/test_arcgis_token_bearer_contract.py` (~10 test)

| Test | Line | Tipo |
| --- | ---:| --- |
| `test_request_passes_token_in_authorization_header_not_params` | 56 | **B** |
| `test_request_strips_token_from_params_if_legacy_caller_passes_it` | 96 | **B** |
| `test_post_data_has_token_in_header_not_body` | 121 | **B** |
| `test_request_retries_once_on_body_error_498` | 153 | **B** |
| `test_request_does_not_loop_on_repeated_498` | 201 | **B** |
| `test_request_backoffs_and_retries_on_429` | 234 | **B** |
| `test_request_gives_up_on_429_after_max_retries` | 268 | **B** |
| `test_request_uses_exponential_backoff_when_retry_after_is_malformed` | 297 | **B** |
| `test_write_batch_refreshes_token_mid_batch_on_498` | 334 | **B** |

**Pattern uniforme**: `MagicMock(http_client)` + `client._token_cache[k]={...}` precompilato + `client._request_with_token(...)` invocato + `mock_http.get.assert_called_once()` + headers/params assertions. **Tutti behavior-bound.**

#### File 2: `tests2/integration/test_arcgis_online_bearer_smoke.py`

Smoke vs ArcGIS Online reale. Skip in CI, behavioral.

### 4.2 Modernizzazione: 0h.

Il refactor 0.5a §1.6 (c1–c4) può procedere senza modifiche test.
Possibili import update (`from app.core.features.arcgis_auth import ...`)
sono triviali (1 line per file).

---

## 5. `file_service.py` — 7 file test

### 5.1 Per-test classification

#### File 1: `tests2/services/test_file_service_db_contract.py` (12 test)

| Test | Line | Tipo |
| --- | ---:| --- |
| `test_upload_persists_bytes_in_db` | 98 | **B** |
| `test_get_file_source_reads_from_db` | 127 | **B** |
| `test_no_temp_dir_leak_on_upload` | 171 | **B** |
| `test_service_can_be_instantiated_with_mocks` | 225 | **B** |
| `test_service_no_longer_exposes_files_dir_fs_attrs` | 257 | **B** |
| `test_service_init_no_longer_accepts_files_dir` | 267 | **B** |
| `test_factory_no_longer_accepts_files_dir` | 275 | **B** |
| `test_dispatch_below_threshold_keeps_data_in_db` | 288 | **B** |
| `test_dispatch_above_threshold_uploads_s3_and_nulls_data` | 313 | **B** |
| `test_upload_failure_does_not_create_db_row` | 353 | **B** |
| `test_read_blob_from_s3_downloads_and_cleans_temp` | 394 | **B** |
| `test_read_blob_from_s3_cleanup_temp_on_download_exception` | 462 | **B** |

**Pattern**: monkeypatch su `app.db.db_service` + stub storage.

#### File 2: `tests2/services/test_file_service_policy.py` (6 test)

| Test | Line | Tipo |
| --- | ---:| --- |
| `test_db_policy_small_payload_stays_in_bytea` | 103 | **B** |
| `test_db_policy_ignores_threshold` | 124 | **B** |
| `test_object_storage_policy_small_payload_uploads` | 152 | **B** |
| `test_object_storage_policy_upload_failure_no_db_row` | 178 | **B** |
| `test_hybrid_below_threshold_stays_in_db` | 207 | **B** |
| `test_hybrid_above_threshold_uploads` | 231 | **B** |

#### File 3: `tests2/services/test_type_inferrer_date_contract.py` (~11 test)

Tutti test sulla classe `TypeInferrer` direttamente. Line 46–151. **Tutti B.**

#### File 4: `tests2/security/unit/test_file_service_filesystem_contract.py` (3 test)

Test su `DefaultFileSystem.extract_zip` direttamente. Line 18–37. **Tutti B.**

#### File 5: `tests2/security/unit/test_file_service_mime_defense_in_depth.py` (6 test, line 70–168)

Tutti `monkeypatch + pytest.raises` pattern. **Tutti B.**

#### File 6: `tests2/security/unit/test_n1_file_service_cross_tenant_contract.py` (~10 test, line 58–180)

Tutti `patch.object + with raises` pattern. **Tutti B.**

#### File 7: `tests2/security/unit/test_temp_lifecycle_core.py` (8 test, line 42–290)

**Cross-module**: testa `artifacts/resolver._write_temp_blob`,
`imdf/loader.extract_imdf_zip`, `arricchisci/api_connector._rest_file`.
NON testa `file_service` direttamente. **Falso blocco in 0.5a precedente.**

### 5.2 Modernizzazione: 0h.

Il refactor 0.5a §3.6 (c1–c4) può procedere senza modifiche test, **a
patto** che:
- helper classi (FormatDetector, TypeInferrer, FileIdGenerator,
  ColumnInfoBuilder) restino importabili da `app.services.file_service`
  via re-export (commit c1–c2).
- DI Protocols restino importabili da
  `app.services.file_service.{FileSystemProtocol, ...}` via re-export
  (commit c3).

Senza re-export, ~10 import statement nei test devono cambiare. **1h
import update**, non modernizzazione semantica.

---

## 6. `spatial.py` — 9 file test

### 6.1 Per-test classification

Tutti i ~40 test in 9 file (line range fra 70–381 per i correctness e
~70–500+ per CRS parametri / error contract / reproject). **Tutti
behavior-bound** (importano `op_geo_*` o `_resolve_metric_crs`,
costruiscono GeoDataFrame, assert su CRS, area numerica, geometry valida,
raise types).

### 6.2 Modernizzazione: 0h.

Raccomandazione 0.5a §2.6 è **no-op**, quindi non rilevante.

---

## 7. Cross-File Audit Scanner — Lasciare invariati

3 test sono scanner cross-file su pattern globali. NON sono blocker
diretti per i refactor 0.5a, e modernizzarli costerebbe più della loro
sostituzione e2e.

| Test | Scope | Site count | Lasciare invariato? |
| --- | --- | ---:| --- |
| `test_silent_assignment_breadcrumb.py` | 4 file (pipeline_task, carica_task, enrichment_task, execution_manager) | 5 siti | **Sì** |
| `test_celery_result_sanitization.py` | 4 file (schedule_task, carica_task, imdf_publish_task, execution_manager) | 6 siti | **Sì** |
| `test_cleanup_breadcrumb_coverage.py` | 5 file | 15 siti | **Sì** |

**Manutenzione**: se un refactor sposta codice fuori dei file scansionati,
aggiornare la `_TARGET_FILES` list. Non è modernizzazione test, è
manutenzione di una whitelist.

---

## 8. Sicurezza / Audit Guards da Preservare (invariato)

I seguenti 8 invariants sono **security/audit-critical** e vanno
preservati in qualsiasi modernizzazione (espressi come behavior, non
indeboliti):

1. **Token in Authorization header (no params)** —
   `test_arcgis_token_bearer_contract.py` test 1, 2, 3 (line 56, 96, 121)
2. **DB-only storage + policy dispatch** —
   `test_file_service_db_contract.py` + `test_file_service_policy.py`
3. **N1 cross-tenant fail-closed** —
   `test_n1_file_service_cross_tenant_contract.py`
4. **MIME defense in depth** —
   `test_file_service_mime_defense_in_depth.py`
5. **lock_lost propagation** —
   `test_execution_manager_lock_lost_propagation_contract.py` (3 test, da modernizzare)
6. **err_msg / `"error": str(e)` sanitization** —
   `test_execution_manager_err_msg_sanitize.py` + `test_celery_result_sanitization.py`
7. **breadcrumb su silent except** — cross-file scanner (lasciare)
8. **Tenant binding `_current_group_id` PRIMA dei loader** —
   `test_enrichment_task_tenant_binding_lock.py` test #3 (line 66, AST-bound)

---

## 9. Falsi Blocchi (consolidato vs precedente revisione)

Conta correzioni rispetto a 0.5a originale:

| 0.5a contava | Reale | Lista test "fuori target" / "in realtà behavior-bound" |
| --- | --- | --- |
| `arcgis_client.py` 1 source-bound | **0 S** | `test_arcgis_token_bearer_contract.py` è B-only (MagicMock + assert_called) |
| `file_service.py` 7 source-bound | **0 S** | tutti monkeypatch/mock/raise pattern |
| `enrichment_task.py` 7 source-bound | **3 file con S/A genuini** (idempotency, tenant_binding, columns_metadata) | 4 file fuori target: batch_parallel (helper module-level), init_execution_context (api_connector), node_may_explode (parallel.py), error_report (enrichment_service) |
| `execution_manager.py` 6+ source-bound | **4 file con S genuini** (lock_lost_propagation, scheduled_for_persist, err_msg_sanitize, n15b test 1) + **3 cross-file scanner** + **6 file behavior** | falsi blocchi: heartbeat, lock_lost (≠ propagation), orphan_and_release, release_retry_wrapper, contract, artifact_resolution |

---

## 10. Ordine Modernization Pre-Refactor (cross-ref con 0.5a §X.6)

### 10.1 Wave 0 — refactor a effort modernizzazione ZERO

Possono procedere ORA senza toccare test (eventuale import update <1h):
- **0.5a §1.6** (`arcgis_client` auth submodule, c1–c4) → **0h** test
  prerequisito + 2h aggiungere unit test su `_authenticate_token`
  (gap di copertura, non blocker)
- **0.5a §3.6** (`file_service` split helper, c1–c4) → **0h** test
  prerequisito (re-export preserva imports)
- **0.5a §5.6 c1** (`execution_manager` LockHeartbeat context manager
  back-compat) → **0h** test prerequisito (i 7 heartbeat test sono B)

### 10.2 Wave 1 — security guards (~5h)

Sblocca: niente refactor specifico, ma rimuove pattern source-bound dai
security guard più importanti.

1. `test_execution_manager_err_msg_sanitize.py` (4 test, ~2h) →
   harness manager + scenarios "raise unknown / raise ExecutionError"
2. `test_celery_result_sanitization.py` (15 test, ~3h se cross-file
   scanner viene **mantenuto come scanner** e NON modernizzato; ~5h se
   parzialmente modernizzato per 6 token canonici) — **decisione: NON
   modernizzare**, restare scanner

### 10.3 Wave 2 — execution_manager carry (~6h)

**Bloccante per 0.5a §5.6 c2** (`execute()` con `with` heartbeat).

1. `test_execution_manager_lock_lost_propagation_contract.py` (3 test, ~3h)
2. `test_execution_manager_scheduled_for_persist_contract.py` (2 test, ~2h)
3. `test_n15b_scheduled_for_always_set_contract.py` test 1 (~1h)

### 10.4 Wave 3 — cross-file scanner (lasciare invariati)

Nessun lavoro di modernizzazione. Manutenzione della watchlist solo se
refactor sposta codice fuori dei file scansionati.

### 10.5 Wave 4 — enrichment_task harness (~14h)

**Bloccante per 0.5a §4.6 c1, c2, c3.**

1. **c0 prereq**: harness Celery `tests2/_helpers/celery_eager.py`
   con eager_mode + spy su `Task.update_state`, su instance creation di
   `ApiConnectionService`, su lifecycle (`open_run`/`close_run`) (~5h).
   Riusabile per altri task tests.
2. `test_enrichment_task_idempotency_contract.py` (2 test S → B, ~1h)
3. `test_enrichment_task_tenant_binding_lock.py` (10 test, di cui ~9
   genuinamente da modernizzare, ~7h)
4. `test_enrichment_task_columns_metadata_propagation.py` (1 test S → B, ~1h)

### 10.6 Tot

- Wave 0: 0h test mod + 2h unit test gap = 2h sblocco arcgis/file_service/heartbeat
- Wave 1: ~2h sblocco security guards
- Wave 2: ~6h sblocco `execute()` refactor
- Wave 4: ~14h sblocco enrichment_task primitives + dedup carica

**Tot massimo: ~24h** (esclusi cross-file scanner lasciati invariati).
**Minimo per sbloccare i refactor a basso rischio: 2h**
(Wave 0, sblocca rank 1, 3, 4 di 0.5a).

---

## 11. Stato Phase 0.5d (rev. evidence-deep)

**Status: analysis only. No test modernization. No refactor opened.**

Output:
- per ognuno dei 5 target: lista test files con line numbers per test
  function;
- per ogni test: classificazione B / S / A con rationale + cosa
  protegge davvero (intent);
- modernizzazione concreta (sketch) per i test che ne hanno bisogno;
- 7 falsi blocchi confermati (test fuori target o behavior-bound che
  0.5a originale contava come blocker);
- 8 security/audit invariants enumerati con riferimento esatto;
- ordine wave 0–5 con effort stimato + cosa sblocca per ogni wave.

Ranking refactor di 0.5a invariato. Conta test source-bound ridotta da
"~20 file" iniziale a **6 file con S genuini + 3 cross-file scanner**.

Pending Phase 0:
- 0.5b — frontend wide audit
- 0.5c — Q1–Q4 SQL queries (`compat_residue_evidence_0_5c.md`)
- 0.5f — perf baseline
- 0.5g — UX / product readiness

---

*End of Phase 0.5d (rev. evidence-deep).*
