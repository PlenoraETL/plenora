# ADR-0019: `INTERMEDIATE_PERSISTENCE_MODE` per-call override (R-25)

**Status**: Accepted
**Date**: 2026-05-01
**Deciders**: @CloudIA

## Context

`INTERMEDIATE_PERSISTENCE_MODE` (Phase 6 Batch 1, 2026-04-28) e' una settings globale che decide quanto materializzare degli snapshot intermedi della pipeline:

- `metadata_only` (default produzione) — solo `node_meta` + `node_hash`. Massima velocita'. Diff L2 non disponibile.
- `preview_only` — meta + hash + prime N righe (`INTERMEDIATE_PREVIEW_ROWS=500`).
- `full_pg` — tutte le righe in `node_rows` (legacy compat).
- `full_artifact` — meta + hash + Parquet + manifest su runtime object storage. Richiede `OBJECT_STORAGE_ENABLED=true` + bucket runtime configurato (cross-validato in `validate_intermediate_settings`).

Nel default `metadata_only`, un audit forte su una run specifica (es. confrontare risultati riga-per-riga con un baseline) richiedeva:

1. cambiare la env var a startup (es. `INTERMEDIATE_PERSISTENCE_MODE=full_pg`),
2. restartare backend + workers Celery,
3. eseguire la run,
4. ripristinare l'env e ri-restartare.

Tre downside:
- Friction operativa alta (4 step su lab.plenora.it production).
- Tutti gli altri job fra step 2 e step 4 girano col mode aumentato.
- Statefulness: l'ops deve ricordare di ri-restartare con la mode di default; un dimenticato lascia il sistema in un mode piu' costoso.

L'audit Phase 1 (R-25, 2026-04-30) ha flaggato questo come "audit gap": serviva un override per-execution che non richiedesse env+restart.

## Decision

Aggiungere un kwarg esplicito **`intermediate_mode_override: Optional[str] = None`** propagato lungo la call-chain:

```
HTTP query param `intermediate_mode`  (auth-gated superadmin)
  -> task_service.create_pipeline_task / create_enrichment_task
  -> Celery task (execute_pipeline_task / execute_enrichment_task)
  -> orchestrator.execute_pipeline (per pipeline) / direct (per enrichment_task)
  -> *_executor.execute (estrai / manipola / arricchisci)
  -> record_final_snapshot / record_checkpoint_snapshot (mode_override=...)
  -> _record_snapshot
  -> resolve_intermediate_policy
  -> resolve_intermediate_mode(default_mode, override_mode)
  -> persistence_mode finale
```

`resolve_intermediate_mode` (PR-B1, pure function in `app/core/intermediate/policy.py`):

- `override_mode is None` -> ritorna `default_mode` (bit-compat default-path).
- `override_mode` valido in `SUPPORTED_PERSISTENCE_MODES` -> ritorna `override_mode`.
- `override_mode` invalido (tipo non-str, stringa vuota, mode non riconosciuto, mode in `PLANNED_PERSISTENCE_MODES`) -> solleva `IntermediateSettingsError`. **Mai fallback al default**: un override sbagliato deve essere visibile.

Endpoint HTTP (PR-B3):

- `POST /api/projects/{id}/execute?intermediate_mode=...`
- `POST /api/projects/{id}/enrich-async?intermediate_mode=...`
- Validazione enum a boundary HTTP via Pydantic `Literal["metadata_only","preview_only","full_pg","full_artifact"]` -> mode invalido = **422**.
- Auth gate dual: il base-flow resta `require_not_esecutore`; check superadmin **solo se `intermediate_mode` e' valorizzato**. Non-superadmin con override = **403**.
- Niente nuovo decorator: il check superadmin e' inline (1 if-statement nel handler).

Capability runtime (es. `full_artifact` con object storage spento) NON e' verificata in `resolve_intermediate_mode`: resta startup-side in `validate_intermediate_settings`. Lo writer (snapshot artifact path) fail-closed al record-time se la capability manca al momento della scrittura.

## Rationale

### Perche' kwarg esplicito vs ContextVar

ContextVar (variabile contestuale Python) sarebbe stata una scelta meno invasiva: 1 set al boundary task + 1 get a `_read_settings`. Rifiutata perche':
- Stato implicito condiviso fra task: rischio di leak fra task se Celery prefork riusa worker.
- Test fragile: ogni test deve ricordare di reset il ContextVar.
- Invisible nei tracebacks: un override sbagliato non si vede nello stack.
- "Magic" in un codebase che predilige propagation esplicita (vedi `run_id`, `user_id`).

Kwarg esplicito ha costo lineare in 7 firme (policy + 3 snapshot wrappers + estrai/manipola executor + 2 task) ma ogni layer e' grep-abile, type-checked, documentato.

### Perche' fail-loud su override invalido

Un override invalido che cade in fallback al default genererebbe ambiguita' diagnostica:
- L'operatore richiede `full_pg` per audit;
- Backend silently degrada a `metadata_only`;
- Audit completo, ma `node_rows` vuota — l'operatore non capisce perche'.

Fail-loud (`IntermediateSettingsError`) catturato dal try/except non-bloccante in `query_executor.execute_query` / `manipola_executor` / `enrichment_task` produce un `logger.warning("Intermediate write fallita ...")`. La query stessa procede (snapshot e' best-effort: il file utente arriva), ma il warning rende esplicito che lo snapshot non e' stato scritto. **Niente fallback silenzioso** = niente audit gap.

### Perche' superadmin gate solo con override

L'endpoint `/execute` deve restare aperto a tutti i ruoli che gia' lo usano (admin, viewer, eccetera). Forzare superadmin sempre rompe la compatibilita'. Forzare superadmin solo quando l'override e' richiesto preserva il base-flow + protegge la nuova capability senza decorator dedicati o forking dell'endpoint.

### Perche' Pydantic Literal vs validazione manuale 400

Pydantic `Literal` produce 422 (`Unprocessable Entity`) deterministico, con body `{detail: [{loc:[query, intermediate_mode], msg:..., type:enum}]}`. Validazione manuale produrrebbe 400 con body custom; meno coerente con il resto delle API FastAPI. 422 e' tecnicamente piu' corretto (sintatticamente valido ma semanticamente fuori dall'enum) e si integra coi client OpenAPI auto-generati.

## Alternatives considered

- **Alt 1: ContextVar al boundary task**
  rejected because stato implicito + leak risk (vedi Rationale).

- **Alt 2: Body field invece di query param**
  rejected because il body `ExecuteRequest` e' condiviso fra endpoint sync/async. Un nuovo field opzionale richiederebbe migration coordinata di consumer FE/test esistenti, mentre query param e' additivo zero-impatto.

- **Alt 3: Settings session cache + bypass via header**
  rejected because piu' complesso senza beneficio. Header non e' first-class come query param nei client (curl/test/swagger).

- **Alt 4: 400 invece di 422 per mode invalido**
  rejected because Pydantic Literal default e' 422 e cambiarlo richiederebbe handler custom. 422 e' semanticamente piu' preciso (sintassi ok, validation enum fail).

- **Alt 5: separate endpoint `/api/projects/{id}/execute-with-audit`**
  rejected because doppia path = doppia maintenance + duplicazione policy auth. Single endpoint con query param e dual-auth e' DRY.

## Consequences

- **Positive**:
  - Audit run-specific senza env+restart.
  - Override invalido visibile (fail-loud + logger.warning).
  - Default produzione invariato (caller esistenti senza param invariati).
  - Auth gate addizionale isolato a 1 if-statement per endpoint, no nuovo decorator.
  - 4 mode supportati end-to-end via override (metadata_only / preview_only / full_pg / full_artifact).

- **Negative**:
  - 7 firme estese (kwarg passing-through). Maintenance: chi aggiunge nuovo executor/task deve includere `intermediate_mode_override`.
  - LOC delta: ~+200 prod / ~+1100 test contract (3 file).
  - `full_artifact` override richiede object storage gia' configurato a startup; un mismatch (override `full_artifact` con capability off) genera fail al record-time, non al boundary HTTP.

- **Neutral**:
  - Frontend non aggiornato (PR-B5 opzionale, fuori scope completion). L'override e' usabile via curl/superadmin per audit interno; UI staff-only differita.
  - `record_checkpoint_snapshot` accetta `mode_override` per simmetria con `record_final_snapshot`, anche se per arricchisci la skip-decision (ARRICCHISCI_INTERMEDIATE_CHECKPOINTS=final_only) ignora la mode. Documentato nel docstring.

## References

- **PR-B1** (#39): `resolve_intermediate_mode` pure function. Source: `backend/app/core/intermediate/policy.py:355-414`.
- **PR-B2** (#40): propagation estrai-final. Source: `backend/app/db/query_executor.py:771-783`, `backend/app/core/intermediate/snapshot.py:325-578`.
- **PR-B2.b + B3 + B4** (questo ADR): completion batch — manipola/arricchisci propagation + endpoint dual-auth + ADR. Source: `backend/app/core/manipola/executor.py:170-260`, `backend/app/core/arricchisci/api_connector.py:372-385`, `backend/app/core/arricchisci/api_connection_support.py:460-505`, `backend/app/core/orchestrator/router.py:129-170`.
- Settings: `backend/app/config.py:225-269` (`INTERMEDIATE_PERSISTENCE_MODE`, default `metadata_only`).
- Validation: `backend/app/core/intermediate/policy.py:233-340` (`validate_intermediate_settings`).
- Related: ADR-0011 (`FileSourcesDispatch S3 threshold`), ADR-0015 (`MinIO object storage provider`), ADR-0017 (`Features vertical status`).
- Related runbook: `backend/docs/ops/runbook.md` §"Audit run con override persistenza".
