# ADR-0020: Source-bound test KEEP list (post R-04 / R-04b modernization)

**Status**: Accepted
**Date**: 2026-05-01
**Deciders**: @CloudIA

## Context

Le PR R-04 (#44) e R-04b (#46) hanno modernizzato 10 file di contract test
da source-bound (`inspect.getsource` + substring scan) a behavior-bound
(bytecode probe / spy / SQL capture / TestClient / eager-mode harness).

Post-modernizzazione restano **7 occorrenze** di `inspect.getsource` in 6
file `tests2/`. Questa ADR documenta che sono **scelte intenzionali**, non
debito residuo, classificate per categoria di pattern legittimo.

Lo scopo del documento e':

1. Evitare che future iterazioni di "audit source-bound" le trattino come
   debito da chiudere.
2. Documentare i criteri sotto cui un `inspect.getsource` resta accettabile
   (anche raccomandato) anche dopo R-04.
3. Fissare un baseline che le PR future possono usare come riferimento
   prima di aggiungere nuovi `inspect.getsource`.

## Decision

I 7 `inspect.getsource` residui sono **KEEP** se rientrano in una delle 3
categorie sotto. Nuove istanze sono ammesse solo se rientrano nelle stesse
categorie.

### Categoria 1 — Static-analysis policy enforcement

L'uso di `inspect.getsource` e' parte di un *linter custom* che impone
una policy a livello codebase, non e' un test fragile dello stato di una
funzione specifica.

**Files in questa categoria**:

| File:line | Symbol | Policy enforced |
|---|---|---|
| `tests2/carica/unit/test_database_writer_log_consistency_contract.py:299` | `DatabaseWriter._truncate_insert_oracle_redef`, `_replace_atomic`, `_truncate_insert` (vari cleanup-guard methods) | Ogni context-string di cleanup guard deve apparire ENTRO ~80 char da `logger.warning` (non `logger.debug`). Pattern simile a `test_audit_action_coverage_contract.py` che enumera endpoint con `@audit_action`. |

**Perche' KEEP**: rimuoverlo significherebbe rinunciare al check di policy
"i cleanup guard sono loggati a livello WARNING". Sostituirlo con un eager
test richiederebbe stub di tutto il chain DatabaseWriter (oltre +200 LOC
per metodo, oltre soglia ragionevole). Lo scan AST/source-string e'
**il pattern adatto** per questa policy.

### Categoria 2 — AST flow-control sentinel

L'uso di `inspect.getsource` e' input per `ast.parse` che valida
**proprieta' strutturali del codice** (es. una call non e' DENTRO un
certo `if`, un decorator e' applicato, un campo Pydantic esiste).
L'AST e' refactor-resilient (sopravvive a rinames di variabili e
riformattazioni), e source-string-only solo nel senso che opera sul
*codice* invece che sul *runtime*.

**Files in questa categoria**:

| File:line | Symbol | Invariant strutturale |
|---|---|---|
| `tests2/carica/unit/test_n13_alias_resolution_contract.py:193` | `CaricaExecutor.execute` | La call `_resolve_aliases_in_mappings(...)` NON deve essere DENTRO un `if not config.column_mappings`. Sentinel anti-regression Gap 2. |
| `tests2/shared/test_type_dispatch_contract.py:153` | `carica_router` modulo | Estrae body di `auto_mapping` via `ast.parse`, valida dispatch alias->dtype (int->int64, real->float64, ...). |
| `tests2/imdf/unit/test_persist_storage_decoupling.py:218` (1 occorrenza residua) | `enrichment_task` modulo | Verifica testo verbatim del `logger.warning` "imdf storage upload fallito (non-fatal, bundle disponibile solo sul nodo corrente)". E' un **contract verso ops** (alert/dashboard fanno grep su quella stringa esatta), non un dettaglio implementativo. |

**Perche' KEEP**: pattern legittimo di static analysis basato su AST.
Stesso paradigma di `test_audit_action_coverage_contract.py:120`,
`test_csv_encoding_explicit_contract.py:64,91`,
`test_datetime_utc_aware_contract.py:80,112,122`,
`test_mfa_totp_contract.py:65,70` — gia' tutti riconosciuti come
"static-analysis scanner pattern" (vedi audit Phase 1 conclusioni).

### Categoria 3 — Scheduler integration deferred

L'uso di `inspect.getsource` e' un compromesso pragmatico per testare
proprieta' di un task body senza richiedere full integration eager con
`Scheduler` + `DB` + `Beat` worker. La sostituzione e' tecnicamente
possibile ma richiede infrastruttura test pesante (database test
isolato, scheduler mock harness, ecc.) fuori scope dei batch test-only.

**Files in questa categoria**:

| File:line | Symbol | Body property |
|---|---|---|
| `tests2/scheduler/unit/test_execution_manager_scheduled_for_persist_contract.py:28,43` | `ExecutionManager.execute` | `scheduled_for` kwarg e' usato nel body oltre la signature (≥3 occorrenze) e arriva a logger / log entry / open_run. |
| `tests2/scheduler/unit/test_schedule_task_scheduled_for_contract.py:35` | `schedule_pipeline_task` | `scheduled_for` propagato dal beat al task. |

**Perche' KEEP per ora**: convertire richiede:
- Setup DB scheduler test (intermediate.runs + log_runs + scheduled_for column).
- Mock harness per `ExecutionManager` + `BeatScheduler` integrazione.
- Eager mode propagation di `scheduled_for` con stub di run lifecycle.

Effort stimato: 1 PR dedicata di tipo "scheduler test infrastructure",
analoga a PR #37 che ha chiuso la stessa cosa per `enrichment_task`.

**Quando convertire**: appena viene aperto un batch "scheduler test
reliability" mirato, riusando il pattern `_helpers/celery_eager.py`
esteso a `ExecutionManager`.

## Rationale

### Perche' non convertire forzatamente i 7

- **Categoria 1 e 2 sono pattern raccomandati** in test che fanno
  policy enforcement / AST sentinel. Convertirli a behavior-bound
  ridurrebbe la qualita' dei test (perdita di robustezza static-analysis).
- **Categoria 3** richiede infrastruttura dedicata; convertire 3
  occorrenze isolate senza l'harness produce test deboli.

### Perche' documentare invece di lasciare implicito

- Future PR di audit potrebbero re-flaggare questi 7 come "debito" senza
  contesto. L'ADR fornisce il riferimento per chiudere la discussione
  velocemente.
- Le categorie definite stabiliscono criteri oggettivi: nuove istanze di
  `inspect.getsource` devono rientrare nelle 3 categorie, altrimenti
  vanno modernizzate.

## Alternatives considered

- **Alt 1: forzare la conversione di tutti i 7**. Rifiutato perche'
  Categoria 1 e 2 sono **legittime** e Categoria 3 richiede infrastruttura
  dedicata fuori scope.
- **Alt 2: lasciare implicito (no ADR)**. Rifiutato perche' produce
  audit-loop futuri che ri-flaggano gli stessi residui.
- **Alt 3: aggiungere un `# noqa` marker custom (es. `# source-bound: keep`)
  e linter che enforcano le 3 categorie**. Considerato ma non implementato
  in questa ADR: aggiungere un linter custom richiede tooling e
  mantenimento; i 7 residui sono pochi e il documento serve come
  reference sufficiente. Se in futuro emergessero >20 residui legittimi
  un linter custom diventa giustificato.

## Consequences

- **Positive**:
  - Future iterazioni di audit hanno un riferimento chiaro per non
    re-flaggare i 7 residui.
  - Criteri di Categoria 1/2/3 stabiliscono il "vocabolario" condiviso
    per accettazione di nuovi `inspect.getsource`.
  - Categoria 3 ha un trigger preciso ("scheduler test infrastructure
    PR") per la futura conversione.

- **Negative**:
  - Nessuna conversione effettiva: i 7 restano source-bound.
    Mitigato dal fatto che Categoria 1 e 2 sono semanticamente diversi
    da source-bound fragile.

- **Neutral**:
  - Se uno dei 7 file viene refactor-ato in modo che il pattern
    Categoria 1/2/3 non si applica piu', la conversione behavior-bound
    diventa naturale al refactor.

## References

- **PR #44** (`commit 3c25ea5`): R-04 N9 source-bound -> behavior-bound
  (11 inspect.getsource modernizzati).
- **PR #46** (`commit 8eeb1b3`): R-04b non-auth source-bound batch
  modernization (9 file, +/-23 occorrenze).
- **PR #37** (`commit 981508f`): foundation harness `_helpers/celery_eager.py`
  per task Celery (riusabile da Categoria 3 future PR).
- Pattern KEEP gia' riconosciuti come legittimi in audit Phase 1:
  `test_audit_action_coverage_contract.py`, `test_csv_encoding_explicit_contract.py`,
  `test_datetime_utc_aware_contract.py`, `test_mfa_totp_contract.py`.
- Related: ADR-0007 (Protocol vs ABC) — analogia con scelte di test
  pattern che hanno trade-off documentati.
