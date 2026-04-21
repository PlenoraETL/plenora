# ADR-0005: Helper condiviso `recheck_user_authorized` (DRY sui task Celery long-running)

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

URBY-ETL esegue lavori async via Celery: pipeline ETL, enrichment API-REST, preview long-running, generazione eseguibili, carica massivo. L'endpoint HTTP accoda il task con `task.delay(...)`; il worker lo pickup dopo un tempo variabile (sotto carico: anche minuti di coda).

Problema di sicurezza documentato come **N9** nel security audit (vedi memory `project_security_audit`): **tra submit e pickup l'admin puo' revocare permessi all'utente**. L'endpoint HTTP ha gia' validato `assert_project_access` al momento della submit, ma se al pickup l'utente e' stato:

- disattivato (`is_active = FALSE`),
- spostato a un altro gruppo (`group_id` cambia),
- de-autorizzato al project specifico (via `allowed_groups` o simili),

il worker deve **rifiutare l'esecuzione**, non procedere con i privilegi stantii.

Senza helper condiviso, ogni task body dovrebbe duplicare ~20 righe di controllo: lookup user, verifica `is_active`, build di `CurrentUser` fresco, chiamata ad `assert_project_access`, fail-handling. Duplicazione su 4+ task body = rischio drift: un fix a `assert_project_access` si propaga a 3 su 4 e un worker resta vulnerabile.

## Decision

Centralizzare il re-check in **`app/shared/async_user_recheck.py`**, funzione pubblica `recheck_user_authorized(user_id, project_id, *, task_id, run_id, task_name)`.

Contract:

- `user_id=None` (task cron/scheduled) → ritorna `None` (skip — legittimo).
- User OK → ritorna `None`, task procede.
- User non autorizzato → ritorna `dict` con `status="permission_revoked"`, `success=False`, `message=<ragione>`. Il caller puo' ritornarlo direttamente dal body.

Il dispatcher interno:
1. `get_user(user_id)` — se non esiste o `is_active=False` → revoked.
2. Costruisce un `CurrentUser` live da DB (non dal payload del task, che e' snapshot al submit).
3. Invoca `assert_project_access(project_id, live_user)` — `HTTPException` in uscita → revoked.
4. Qualsiasi eccezione inattesa → **fail-closed**: treated as revoked. Meglio rifiutare un task legittimo che procedere con permessi indeterminati.

Call-site attuali:
- `workers/tasks/carica_task.py:121`
- `workers/tasks/enrichment_task.py:116`
- `workers/tasks/pipeline_task.py:129` (pipeline execute)
- `workers/tasks/pipeline_task.py:390` (pipeline preview long-running)

## Rationale

- **Single source of truth**. Se domani `assert_project_access` cambia policy (es. aggiunta di `allowed_groups` join), tutti i worker si aggiornano gratis. Nessuna mailing list di "ricordati di aggiornare anche X".
- **Return value vs raise**. L'helper ritorna un dict invece di alzare exception. Ragione: i task Celery devono ritornare un payload strutturato che il frontend polla (`get_task_result`). Un'exception da re-check diventerebbe un 500 generico; il dict con `success=False, status="permission_revoked"` e' un outcome semantico, non un crash.
- **`status="permission_revoked"` come enum-like**. Il frontend puo' discriminare da altri failure mode (`"validation_failed"`, `"timeout"`, `"source_unreachable"`, ...). Messaggio in italiano user-facing e' stabile per stringhe-chiave.
- **Fail-closed su eccezione generica**. Il try/except wrappa l'intero check. Se qualcosa rompe (es. DB down), ritorna revoked invece di None. Trade-off accettato: **disponibilita' ridotta preferibile a privilegio stantio**. Un task ritorna `permission_revoked` con messaggio tecnico; l'utente riprova; se DB e' davvero down, il submit stesso sarebbe fallito prima.
- **Zero impatto su task non-user (cron)**. `user_id=None` → early-return `None`. Nessun check, nessun overhead.
- **Lazy import delle dipendenze**. `get_user`, `CurrentUser`, `assert_project_access` vengono importati *dentro* il body della funzione. Motivo: `app.shared` non deve dipendere top-level da `app.db.config_db_service` o `app.core.auth` — evita import cycle e permette a `async_user_recheck.py` di essere importato come helper generico senza pre-caricare mezzo backend.

## Alternatives considered

- **Decorator `@requires_permission(project_arg="project_id")`** — **Rejected**. Decorator Celery-task-friendly e' fragile: devi introspectare `*args`/`**kwargs` per estrarre `project_id` (task signatures non sono uniformi). Refactor di firma di task + rischio di decorator misconfigured che skippa il check silenziosamente. Il chiamare esplicito e' piu' leggibile e piu' difficile da sbagliare.
- **Middleware Celery (signal `task_prerun`)** — **Rejected**. I Celery signals si registrano globalmente; applicarli selettivamente richiede liste di task_name o filtri fragili. Inoltre `task_prerun` non ha un modo pulito di abortire la task con payload strutturato (dovrebbe raise, che diventa 500 come sopra).
- **Duplicazione esplicita in ogni task body** — **Rejected**. E' quello che succedeva prima di N9; il drift e' garantito.
- **Check HTTP-only (fidarsi del gate in ingresso)** — **Rejected**. Il threat model N9 e' proprio "admin revoca tra submit e pickup". Ignorare la finestra di staleness lascia il bug in piedi.

## Consequences

### Positive
- 4 task body allineati su 20 righe ciascuno → 2 righe di import + 1 di call. Leggibilita' salita.
- Single source per il fix. Se bisogna aggiungere check di MFA al pickup (ipotetico futuro), si aggiunge qui e i 4 worker si beneficiano.
- Test contract in `tests2/security/unit/test_n9_async_permission_revoked_contract.py` esercita tutti i branch (user None, user disabled, cross-group deny, project non esistente, exception recovery). Test vive in un posto, copre tutti i call-site.
- Fail-closed documentato e testato: DB down → revoked, non crash.

### Negative
- Overhead di una query `get_user` + una eventuale `assert_project_access` per ogni pickup. Pratica misura: ~10-30ms/pickup, irrilevante rispetto ai task multi-secondi (qualsiasi pipeline ETL).
- I task non-user (cron) devono ricordarsi di passare `user_id=None`. Convention enforced tramite test contract: un task che dimentica la chiamata e' visibile in code-review (vedi commit log del fix N9).

### Neutral
- Nuovo modulo `app/shared/async_user_recheck.py` — aggiunge 1 file a `shared/`. Accettato: la cartella e' pensata proprio per helper cross-dominio.

## References

- Source: `backend/app/shared/async_user_recheck.py` — intero modulo (~90 righe)
- Call-site: `backend/workers/tasks/carica_task.py:121`, `enrichment_task.py:116`, `pipeline_task.py:129`, `pipeline_task.py:390`
- Tests: `backend/tests2/security/unit/test_n9_async_permission_revoked_contract.py`
- Related: ADR-0004 (JWT claim sono snapshot al login — questo helper compensa la staleness al pickup async)
- Memory: `project_security_audit` — N9 era uno dei 13 round di audit
- Invariants: `backend/docs/architecture/invariants.md` §auth — re-check async
