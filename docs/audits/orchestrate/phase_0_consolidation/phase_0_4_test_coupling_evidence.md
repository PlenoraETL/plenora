# Phase 0.4 — Test Coupling Audit + Evidence Plan E1-E5

**Data**: 2026-04-27
**Stato**: audit completato. **Read-only**, nessun codice/test modificato.
**Vincoli rispettati**: nessun deploy, nessun cambio behavior, no SSH, batch piccolo, stop con report.

**Update vs baseline.md §6**: numeri aggiornati con grep esaustivo. Il baseline diceva "~17 + 5 file"; il count reale e` **~26 file** con `inspect.getsource` + 4 con `patch(<mod>.logger)` + alcuni casi `ast.parse` cross-codebase che NON sono source-bound (sono security audit by design).

---

## 1. Test source-bound — inventario completo

### 1.1 Categoria A — `monkeypatch.setattr(<mod>, "logger", ...)` o `patch("<mod>.logger")`

**4 file**, ~8 occorrenze. **Modernizzabili ORA**, behavior-bound, indipendenti da refactor target. Pattern target: handler stdlib agganciato per nome (B2.0 consolidato).

| File | Occorrenze | Note |
|---|---:|---|
| `tests2/shared/test_artifact_resolver_trusted_roots.py` | 3 (l. 87, 128, 167) | `patch("app.core.artifacts.resolver.logger")` × 3 |
| `tests2/services/test_project_service_canvas_parser_narrow.py` | 3 (l. 59, 113, 147) | `patch("app.services.project_service.logger")` × 3 |
| `tests2/security/unit/test_audit_action_decorator.py` | 1 (l. 90) | `monkeypatch.setattr(audit_mod, "logger", spy_logger)` |
| `tests2/security/unit/test_auth_delete_group_logging_contract.py` | 1 (l. 53) | `monkeypatch.setattr(auth_mod, "logger", spy_logger)` |

**Stato**: candidati Phase 0.x batch dedicato (modernization commit-only, separato da refactor target). Stima: ~1h modernization + test gate per file.

### 1.2 Categoria B — `inspect.getsource(<module>)` o `inspect.getsource(<class>)`

**~22 file**, ~50 occorrenze. **NON modernizzabili da soli** — sono accoppiati al *target* via `__code__.co_filename`. Si modernizzano in coppia col refactor del target (regola METHOD: "test-only modernization in commit separato, prima del refactor").

| File test | Target principale | # occorrenze | Carry-over verso |
|---|---|---:|---|
| `tests2/security/unit/test_n9_async_permission_revoked_contract.py` | orchestrator/recheck_user_authorized + execute_pipeline_task + TaskService.create_pipeline_task + PostgresLogHandler.open_run | 9 | refactor orchestrator/scheduler/log handler |
| `tests2/arricchisci/unit/test_enrichment_task_tenant_binding_lock.py` | enrichment_task + execute_enrichment_task | 8 (+1 ast.parse) | refactor `workers/tasks/enrichment_task.py` |
| `tests2/security/unit/test_n11_canvas_types_propagation_contract.py` | orchestrator/service + orchestrator/router | 2 | refactor orchestrator |
| `tests2/carica/unit/test_database_writer_log_consistency_contract.py` | dw_module (gia` modernizzato post-B2.0) | 4 (modernizzato) | nessuno (modernizzato) |
| `tests2/carica/unit/test_database_writer_bulk_integration_contract.py` | DatabaseWriter methods (gia` modernizzato post-B2.4a, ast su classe-scaffold) | 4 (modernizzato) | nessuno (modernizzato) |
| `tests2/manipola/unit/test_manipola_runtime_cycle_detection_contract.py` | ManipolaExecutor._carica_sorgenti_progetto + manipola_executor module | 2 | refactor manipola/executor |
| `tests2/scheduler/unit/test_execution_manager_lock_lost_propagation_contract.py` | ExecutionManager.execute | 3 | refactor scheduler/execution_manager |
| `tests2/scheduler/unit/test_execution_manager_scheduled_for_persist_contract.py` | ExecutionManager.execute | 2 | refactor scheduler/execution_manager |
| `tests2/scheduler/unit/test_schedule_task_scheduled_for_contract.py` | schedule_pipeline_task | 1 | refactor scheduler |
| `tests2/scheduler/unit/test_n15b_scheduled_for_always_set_contract.py` | schedule_task.schedule_pipeline_task | 1 | refactor scheduler |
| `tests2/orchestrator/unit/test_pipeline_task_idempotency_contract.py` | execute_pipeline_task | 3 | refactor orchestrator |
| `tests2/arricchisci/unit/test_enrichment_task_idempotency_contract.py` | execute_enrichment_task | 2 | refactor enrichment_task |
| `tests2/imdf/unit/test_persist_storage_decoupling.py` | persist_mod + enrichment_task | 2 | refactor imdf/persist + enrichment_task |
| `tests2/carica/unit/test_n13_alias_resolution_contract.py` | CaricaExecutor.execute + exec_mod (ast.parse) | 2 | refactor carica/executor |
| `tests2/carica/unit/test_carica_task_retry_policy_contract.py` | carica_task module | 1 | refactor workers/tasks/carica_task |
| `tests2/carica/unit/test_carica_idempotency_contract.py` | execute_carica_task | 1 | refactor workers/tasks/carica_task |
| `tests2/shared/test_column_types_sources_contract.py` | estrai_mod + enrich_mod + enricher_mod | 3 | refactor estrai/enrichment |
| `tests2/shared/test_type_dispatch_contract.py` | carica_router | 1 (+ ast.parse) | refactor carica/router |
| `tests2/security/unit/test_eseguibili_size_cap_contract.py` | eseguibili_router.analyze_upload | 1 | refactor eseguibili/router |
| `tests2/security/unit/test_n8_runs_retention_applied_contract.py` | close_run | 2 | refactor intermediate/writer |

**Stato**: ognuno di questi test scatta il modernization-commit quando si tocca il rispettivo target. Phase 0 NON ne modernizza nessuno preventivamente — sarebbe lavoro a vuoto.

### 1.3 Categoria C — `ast.parse` su filesystem walk (security audit)

**3 file**. **NON sono test source-bound** nel senso classico: sono security audit cross-codebase by design. Esistono per garantire che NESSUN file dell'app violi una invariante (es. `datetime.utcnow()` deprecated). Modernizzarli e` controproducente — sostituirebbe l'audit con qualcosa di piu` debole.

| File | Cosa controlla | Trattamento |
|---|---|---|
| `tests2/security/unit/test_csv_encoding_explicit_contract.py` | ast walk: nessun `pd.read_csv` senza `encoding=` | **B intenzionale** — security guard |
| `tests2/security/unit/test_datetime_utc_aware_contract.py` | ast walk: nessun `datetime.utcnow()` deprecated | **B intenzionale** — security guard |
| `tests2/security/unit/test_audit_action_coverage_contract.py` | ast.parse di un file fisso per coverage decoratore | **B intenzionale** — coverage audit |
| `tests2/security/unit/test_mfa_totp_contract.py` | ast.parse di `_AUTH_PY` + `_MFA_PY` (2 file fissi) | source-bound parziale: se i file vengono spostati, va aggiornato il path. Tracciare. |

### 1.4 Riassunto categoria

| Categoria | File | Modernizzabili ora | Note |
|---|---:|---|---|
| A — logger patch | 4 | si | Modernization batch dedicato Phase 0.x candidato |
| B — `inspect.getsource(target)` | ~22 | no, accoppiati al refactor target | Phase 1+ in coppia col refactor |
| C — ast.parse cross-codebase audit | 3-4 | no (B intenzionale, security guard) | Lasciare |

---

## 2. Test coupling — modernization plan A (raccomandato)

**Obiettivo**: chiudere la categoria A in 1 commit dedicato, zero behavior change, abilita refactor futuri di artifacts/project_service/audit_action/auth.

**Approccio**: per ogni file, sostituire `patch("<mod>.logger")` / `monkeypatch.setattr(<mod>, "logger", ...)` con uno dei due pattern equivalenti behavior-bound (gia` consolidati in Phase 5):

**Pattern P1 — handler stdlib agganciato per nome**:
```python
import logging

def _attach_log_capture(monkeypatch, logger_name):
    records = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record)
    target_logger = logging.getLogger(logger_name)
    target_logger.addHandler(handler)
    monkeypatch.setattr(target_logger, "level", logging.DEBUG)
    monkeypatch.setattr(target_logger, "propagate", True)
    yield records  # uso come fixture
    target_logger.removeHandler(handler)
```

**Pattern P2 — spia diretta su `logger.<level>`** (pattern A3 appena usato):
```python
def _spy_warning(monkeypatch, mod):
    calls = []
    monkeypatch.setattr(mod.logger, "warning", lambda msg, *a, **kw: calls.append(msg))
    return calls
```

**Quale pattern per quale file**:

| File | Pattern proposto | Motivo |
|---|---|---|
| `test_artifact_resolver_trusted_roots.py` | P2 (spia diretta) | 3 use site simili, P2 e` piu` corto |
| `test_project_service_canvas_parser_narrow.py` | P2 (spia diretta) | idem |
| `test_audit_action_decorator.py` | P2 (spia diretta) | gia` usa pattern simile altrove |
| `test_auth_delete_group_logging_contract.py` | P2 (spia diretta) | idem |

**Stima implementazione**: ~2-3h (4 file × ~30-45 min ognuno) + test gate `pytest tests2/{shared,services,security}/unit/test_*` (1-2 min).

**Rischio**: basso. Behavior-bound assertion identica al precedente (record di warning catturati). Zero modifica al codice di produzione.

**Stop pre-implementation**: in attesa di tua decisione su:
1. Aprire batch Phase 0.x test-modernization-A in 1 commit unico (raccomandato), o 1 commit per file (4 commit), o lasciarlo per Phase 1.
2. Se aprire ora: scrivere io i 4 diff in stop pre-commit per review, oppure procedere file-per-file con commit incrementali.

---

## 3. Evidence plan E1-E5

### 3.1 Stato attuale dei 5 candidati COMPAT-RESIDUE / legacy

| ID | Posizione | Tipo emissione | Osservabilita` attuale |
|---|---|---|---|
| E1 | `manipola/executor.py:416` | `logger.warning("[COMPAT-RESIDUE] manipola._carica_sorgenti_progetto: ...")` | Scritto via `app/utils/logger.py` → sink DB `app/core/logs/` (PostgresLogHandler) → **interrogabile** |
| E2 | `arricchisci/api_connection_source_loading.py:340` | `logger.warning("[COMPAT-RESIDUE] api_connector._filesystem_fallback_load_project ...")` | Idem E1 — interrogabile |
| E3 | `manipola/plugin_executor.py:556` | **Solo commento** `# Fallback al vecchio sistema...`, **non emette log** | Non osservabile senza modifica codice |
| E4a | `arricchisci/enrichment_service.py:263` | **Solo commento** `# legacy: tutti i parametri sono trattati come required` | Non osservabile |
| E4b | `arricchisci/enrichment_validation_preview.py:154` | `return True  # legacy: tratta tutto come required` | Non osservabile |
| E5 | `auth/auth.py:299` | **Solo commento** `# legacy bcrypt hanno un costo di verify leggermente diverso ...` | Osservabile via DB query `password_hash` format |

### 3.2 Evidence — query / probe proposti (NON eseguiti)

#### E1 — `manipola._carica_sorgenti_progetto`

**Query DB (read-only)**:
```sql
SELECT count(*), min(created_at), max(created_at)
FROM plenora.logs
WHERE message LIKE '%[COMPAT-RESIDUE] manipola._carica_sorgenti_progetto%'
  AND created_at > NOW() - INTERVAL '30 days';
```

**Decisione**:
- Se count = 0 in 30gg: **rimovibile** in Phase 1 dopo conferma di un altro periodo di osservazione (60gg).
- Se count > 0: il path e` ancora vivo. Identificare i caller (request_id / user_id se loggati) e capire se serve.

#### E2 — `api_connector._filesystem_fallback_load_project`

**Query DB**:
```sql
SELECT count(*), min(created_at), max(created_at)
FROM plenora.logs
WHERE message LIKE '%[COMPAT-RESIDUE] api_connector._filesystem_fallback_load_project%'
  AND created_at > NOW() - INTERVAL '30 days';
```

**Decisione**: stesso pattern di E1.

#### E3 — `plugin_executor` "Fallback al vecchio sistema"

**Probe codice (read-only)**:
```bash
# Identificare il caller del fallback (capire CHE COSA cade in questo path):
grep -B 30 "Fallback al vecchio sistema" backend/app/core/manipola/plugin_executor.py | head -50
```

**Action proposed (NON Phase 0)**: aggiungere `logger.warning("[COMPAT-RESIDUE] plugin_executor.fallback_old_system: ...")` come prima istruzione di quel ramo per renderlo osservabile. Cambio di codice: serve 1 batch dedicato Phase 1 (basso rischio, +1 log warning).

**Decisione**: senza il warning aggiunto, NON si puo` decidere se rimuoverlo. Inserito nell'evidence plan come prerequisito.

#### E4 — `enrichment_service.py:263` + `validation_preview.py:154` "legacy: tutti required"

**Probe codice (read-only)** — capire se il branch "legacy" e` mai non-attivo:

```bash
grep -B 10 -A 15 "legacy: tutti" backend/app/core/arricchisci/enrichment_service.py
grep -B 10 -A 15 "legacy: tratta tutto come required" backend/app/core/arricchisci/enrichment_validation_preview.py
```

Lettura attesa: `validation_preview.py:154` ritorna `True` come fallback quando `auth_config.get("required_params")` non e` lista. Il branch "non-legacy" si attiva solo se la API connection ha un `required_params` esplicito nella sua auth config. Verificare quante API connection nel DB hanno `required_params` settato:

```sql
SELECT count(*) AS total,
       count(*) FILTER (WHERE auth_config::jsonb ? 'required_params') AS with_required
FROM plenora.api_connections;
```

**Decisione**:
- Se `with_required = 0`: il branch "non-legacy" e` dead code → rimovibile (entrambi i commenti diventano obsoleti)
- Se `with_required > 0`: i 2 commenti sono fuorvianti ma il codice serve. Rimuovere i commenti o riscriverli.

#### E5 — `auth.py:299` legacy bcrypt

**Query DB (read-only)**:
```sql
SELECT
    count(*) FILTER (WHERE password_hash LIKE '$2b$%' OR password_hash LIKE '$2a$%' OR password_hash LIKE '$2y$%') AS bcrypt_users,
    count(*) FILTER (WHERE password_hash LIKE '$argon2%') AS argon2_users,
    count(*) FILTER (WHERE password_hash NOT LIKE '$%') AS other,
    count(*) AS total
FROM plenora.users;
```

**Decisione**:
- Se `bcrypt_users = 0`: rimovibile (commento obsoleto, codice puo` essere semplificato)
- Se `bcrypt_users > 0`: il commento serve ancora come hint di migrazione progressiva (i bcrypt vengono ricodificati al prossimo login successful). Lasciare.

### 3.3 Esecuzione evidence plan

**Vincolo**: le query SQL DEVONO essere eseguite contro il database produzione `15.161.59.22:5432/plenora` con credenziali read-only. NON via SSH al server, ma tramite client SQL diretto (es. `psql`) — l'operatore decide quando.

**Output atteso** (per ogni candidato): tabella numeri + decisione binaria (keep/remove/observe-more).

**Tempo stimato**: ~30 min (5 query DB) + ~15 min interpretazione + decisioni.

**NON in Phase 0**: l'esecuzione del SQL e` ammessa (read-only DB), ma le **rimozioni del codice** sono Phase 1+.

---

## 4. Conclusioni Phase 0.4

### 4.1 Test source-bound — situazione reale

- **22 file** in categoria B (accoppiati al refactor target) — modernizzano in coppia col loro refactor.
- **4 file** in categoria A (logger patch) — modernizzabili ora. Candidato Phase 0.x batch dedicato.
- **3-4 file** in categoria C (security audit cross-codebase) — lasciare (B intenzionale).

### 4.2 Evidence E1-E5 — situazione reale

- **E1, E2** osservabili gia` (warning emessi → log DB). Query SQL pronta.
- **E3** richiede +1 log warning per essere osservabile (Phase 1, modifica codice +1 riga).
- **E4** richiede DB query su `api_connections.auth_config`.
- **E5** richiede DB query su `users.password_hash`.

### 4.3 Phase 0 deliverable a chiusura

Phase 0.4 e` l'ultimo step doc-only di Phase 0. Output complessivo Phase 0:
- `consolidated.md` — synthesis baseline
- `probe_0_2_deploy.md` — probe + piano Phase 1 server alignment
- `phase_0_3_security_env.md` — SSRF design + A2/A3 marker + env audit + ENVIRONMENT probe
- `phase_0_4_test_coupling_evidence.md` — questo file
- 4 commit nel repo (1 docfix + 1 A2 + 1 A3 + 1 push test ottenuto)

### 4.4 Cosa Phase 0.4 NON ha fatto

- Nessun codice modificato ✓
- Nessun test modificato ✓
- Nessun deploy ✓
- Nessun SSH (sufficiente quanto raccolto in 0.2 + ENVIRONMENT probe in 0.3) ✓
- Nessuna query SQL eseguita (proposte solo) ✓

---

## 5. In attesa di decisione

1. **Modernization batch test categoria A** (4 file logger patch) — aprire ora come Phase 0.x oppure rimandare a Phase 1?
2. **Esecuzione query SQL E1-E5** — quando? Operatore esegue manualmente con `psql` e incolla output?
3. **Closeout Phase 0** — preparare `phase_0_consolidation/closeout.md` che ratifica baseline + carry-over a Phase 1 (server cutover + SSRF design + retry consolidation + ecc.)?
