# ADR-0012: Policy preview vs execute per `estrai / manipola / arricchisci`

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

I tre moduli core `estrai / manipola / arricchisci` espongono ciascuno due operazioni sullo stesso executor: `execute()` (persiste output reali — file, DB, artefatto) e `preview()` (non persiste, alimenta la UI con un campione). La policy di errore di questi due path era storicamente **mista e non documentata**: l'inventory report Fase 0 (`inventory_report.md` §4) e l'invariant §1.6 riga `estrai/manipola/arricchisci` la classificavano esplicitamente come *"Misto preview-vs-execute — Convenzione (non ancora invariant): execute = fail-fast; preview = best-effort loggato. Unificazione rimandata (D-fascia)"*.

La divergenza osservata post-inventory (cfr. `orchestrate/phase_1_stabilization/d6_preview_execute_policy.md` §2):

- **Log level su except top-level di preview**: estrai usa `warning` con traduzione user-friendly; manipola `warning` con `[GESTITO]`; arricchisci `error` + `exc_info=True`. Tre politiche diverse sulla stessa operazione logica.
- **Error taxonomy verso UI**: solo estrai protegge l'utente dal leak di dettagli interni (`_traduci_errore_sql`); manipola e arricchisci passano `str(e)` raw.
- **Tenant binding in preview**: solo manipola ha convenzione esplicita (`_init_security_context(strict=False)`).
- **Per-node vs pipeline-level error granularity**: arricchisci ha granularita' per nodo; manipola catch a livello pipeline; estrai single-node.
- **Swallow residui**: per-node except silenziosi nel parallel path di arricchisci + fallback silenziosi in manipola.transforms + estrai feature-service error mapping (tutti chiusi tatticamente in Phase 1 via S26, commit `db9ef03` arricchisci + `6db33c7` manipola).

Phase 1 ha chiuso **il codice** (S26). Questa ADR ratifica formalmente la policy per rendere la riga invariant vincolante su ogni PR successiva.

## Decision

Policy canonica per `app/core/{estrai,manipola,arricchisci}`:

1. **`execute()` → fail-fast su dato critico.** Ogni errore che compromette correttezza, persistenza o tenancy propaga fino al router e diventa `HTTPException` con `logger.error` prefisso `[CRASH]`. Nessun ritorno "soft" `{success: false}` sul path che persiste.
2. **`preview()` → best-effort loggato, scope ristretto.** L'anteprima non persiste e alimenta la UI: ogni errore non critico viene catturato a livello di pipeline node, loggato almeno `warning` con contesto strutturato, e mappato su return uniforme `{success: False, message: str}` leggibile dal frontend. **Mai silent**: nessun `except: pass`, nessun `logger.debug` come unico canale per un errore gestito.
3. **Fail-fast resta obbligatorio anche in `preview()`** per tre famiglie di errori:
   - **auth / tenant binding non risolvibile** (group_id mancante quando obbligatorio),
   - **canvas malformato** (result node assente, edge dangling, connection_id non esistente),
   - **validazione contrattuale di input** (tipi sbagliati sulla signature dell'executor).

   Questi salgono come eccezione al router, anche in preview.
4. **`mode-specific`, non flag-driven.** La distinzione preview/execute vive come due metodi separati sullo stesso executor. Non introdurre `is_preview=True` sullo stesso path: moltiplica i branch e riproduce la confusione che questa ADR risolve.
5. **Return schema uniforme per la UI**: `preview()` ritorna sempre `{success: bool, message?: str, columns?: [...], rows?: [...], total_count?: int, preview_count?: int, truncated?: bool}`. `execute()` ritorna l'artefatto (file_id / row_count / path) oppure solleva.

L'invariant §1.6 riga `estrai/manipola/arricchisci` viene riscritta con tre righe canoniche (una per operazione) e l'eccezione autorizzata `_init_security_context(strict=False)` di `manipola/executor.py:305` viene registrata in §2 come fallback autorizzato (motivo + perimetro + log + piano).

## Rationale

- **Execute = fail-fast perche' persiste.** Un `{success: false}` su path di scrittura lascia il DB in stato ambiguo (parziale? rollbackato? orphan?) e il chiamante deve indovinare. HTTPException + log `[CRASH]` e' inequivocabile: "non persisto, spiego perche', logga dove serve".
- **Preview = best-effort perche' la UI non deve sbattere.** L'utente sta esplorando: un errore non deve restituire un 500 se il canvas e' ben formato ma i dati sorgente hanno un problema puntuale. Il return `{success: False, message: str}` e' un contratto che il FE gia' sa gestire (mostra banner rosso con il messaggio).
- **Ma preview NON e' silent.** I tre scenari "auth / canvas / contratto" sono bug strutturali: nasconderli come `{success: false}` rende impossibile distinguerli da errori dato. Saliti come HTTPException il FE li mostra come errore di sistema (500/400), cosi' l'ingegnere sa dove guardare.
- **No flag `is_preview=True` sullo stesso executor.** Questa era la scelta piu' "DRY" a prima vista, ma moltiplica i branch `if self.is_preview:` dentro metodi lunghi e rende il codice illeggibile. Due metodi separati con uno shared state (self) sono piu' chiari.
- **Return schema uniforme** riduce il branch FE: oggi PreviewModal tollera chiavi extra per retro-compat, ma il target di standardizzazione e' esplicito. Il FE cleanup e' Phase 2.
- **La ratifica arriva dopo il codice** perche' S26 ha dimostrato che la policy e' gia' implementabile senza refactor grossi: ogni modulo ha risposto al pattern senza riscritture. Ratificare prima avrebbe fissato una policy teorica; ratificare dopo fissa quella che il codice dimostra funzionare.

## Alternatives considered

- **Parametro `is_preview=True` sullo stesso `execute()`** — **Rejected**. Moltiplica i branch interni, i test devono coprire 2N scenari invece di 2 × N, e il log context diventa ambiguo. L'argomento "DRY" cede davanti a un method stub di 3 righe.
- **Preview fail-fast uniforme come execute** — **Rejected**. Ogni errore dato in preview diventerebbe 500 lato UI, rompendo l'esperienza di esplorazione su dataset problematici. La UI ha un contratto "mostra banner rosso leggibile" che richiede `{success: false, message}`.
- **Preview silent-success (maschera errori come preview vuota)** — **Rejected**. Questo era il pattern pre-S26 in alcuni rami. Il problema: un utente vede "0 righe" sia quando il dataset e' vuoto sia quando c'e' un bug, e non ha come distinguere. Il messaggio esplicito e' un requisito product, non un dettaglio di implementazione.
- **Un'unica classe `PreviewContext` condivisa tra i tre moduli** — **Rejected (per ora)**. Tentativo di astrarre la parte preview in un mixin cross-modulo. Le tre logiche di preview differiscono abbastanza (estrai single-query, manipola pipeline sequenziale, arricchisci parallel) da rendere l'astrazione premature. Riprendere in Phase 2 se emerge duplicazione concreta.
- **Policy diversa per arricchisci** (lasciare `logger.error + exc_info=True` come default) — **Rejected come default, Accepted come eccezione locale**. Il traceback verboso ha senso sul generic `except Exception` per la complessita' del modulo (async, rate limiter, parallel), ma per exception typed del dominio `warning` senza traceback e' sufficiente. Regola operativa: default `warning`, fallback `error + exc_info` solo sul catch generico finale.

## Consequences

### Positive
- Un solo pattern documentato per 3 moduli → review PR piu' veloce: "questo catch rispetta la policy?" → guarda l'invariant riga per preview/execute + 3 righe di design.
- Il return schema uniforme abilita semplificazione FE in Phase 2.
- La regola "preview fail-fast su auth/canvas/contratto" elimina la classe di bug "errore strutturale nascosto dietro preview vuota".
- I test contract futuri (Phase 2, stream TESTS) hanno un target preciso da blindare: un test per modulo × policy.
- La registrazione di `_init_security_context(strict=False)` in invariants §2 rimuove l'anomalia "eccezione autorizzata non documentata" segnalata dall'inventory.

### Negative
- La distinzione "auth/canvas/contratto" richiede disciplina: un catch top-level troppo largo in preview puo' inavvertitamente swalloware uno di questi tre. Mitigazione: review checklist §5.2 di invariants.md (la PR deve dichiarare se introduce nuovo fallback).
- Alcuni log oggi omettono `op="preview"|"execute"` esplicito. La policy lo richiede implicitamente (filtering in osservabilita'), ma l'allineamento retroattivo non e' in scope ADR — si fa incrementalmente nei prossimi PR.
- Il contratto preview `{success, message?, columns?, rows?, total_count?, preview_count?, truncated?}` e' piu' stringente di quanto alcuni executor restituiscano oggi. Il cleanup schema-by-schema e' Phase 2.

### Neutral
- La ratifica non introduce codice nuovo. S26 aveva gia' chiuso il pattern tatticamente; questa ADR formalizza la convenzione per PR successive e rende la riga `invariants.md §1.6` vincolante.
- Nessun test contract in questa ADR. I test `test_preview_execute_policy_contract.py` per i tre moduli sono in backlog Phase 2 (stream TESTS).

## References

- Source: `backend/app/core/estrai/executor.py:49` — `_traduci_errore_sql` translator (pattern di reference per error mapping user-facing)
- Source: `backend/app/core/manipola/executor.py:146-174` — `_init_security_context(strict=True)` in `execute()`
- Source: `backend/app/core/manipola/executor.py:305` — `_init_security_context(strict=False)` in `preview()` (fallback autorizzato registrato in invariants §2)
- Source: `backend/app/core/arricchisci/executor.py` — preview swallow chiuso da S26
- Commit: `db9ef03` — S26 preview swallow arricchisci
- Commit: `6db33c7` — S26 preview swallow manipola
- Design doc: `orchestrate/phase_1_stabilization/d6_preview_execute_policy.md`
- Invariants: `backend/docs/architecture/invariants.md` §1.6 (riga `estrai/manipola/arricchisci` post-ratifica), §2 (`_init_security_context(strict=False)` fallback autorizzato)
- Related: ADR-0007 (Protocol pattern per contract comuni), `invariants.md §4.5` (docstring non stale)
