# PHASE 1 STABILIZATION — Closeout

**Data chiusura**: 2026-04-21
**Stato**: CHIUSA
**Scope**: `orchestrate/phase_1_stabilization/kickoff.md`
**Runbook done-check**: `orchestrate/phase_1_stabilization/done_check.md`
**Mirror interno (tracking fase)**: `orchestrate/phase_1_stabilization/closeout_report.md`

Questo file e' la versione **git-tracked** del closeout di Phase 1. Il contenuto corrisponde a `orchestrate/phase_1_stabilization/closeout_report.md` (cartella `orchestrate/` non sotto version control). In caso di divergenza, questo file e' fonte di verita'.

---

## 1. Sintesi

Phase 1 si chiude con:

- **Track A (4 stream paralleli)** — tutti mergiati su `main`, STREAM_REPORT consolidato in `STREAM_REPORT.md`.
- **Track B (consolidation-micro S17-S28)** — tutti chiusi: S17, S18, S23, S25, S26, S27 con commit atomici; S28 chiuso come triage di conformita' il 2026-04-20 (nessuna modifica codice).
- **Track C (governance D3-D6)** — D5 chiuso con fix codice + doc; D4 parziale (`URBY_DISABLE_DEFAULT_KEY` rimosso; i due alias residui bloccati su verifica `.env` produzione); D3 e D6 restano design doc senza refactor codice (in attesa di policy formale).
- **Architecture A1 (storage policy)** — 3 commit canonici applicati (validate + dispatch simmetrici FS-4/FS-5).
- **Integration hotfixes post-merge** — 5 commit: deps ACN, deps FE, beat `SET LOCAL`, MFA fixture PG, MFA status robustezza.

Nessun fake closure. Quello che non e' stato completato e' classificato esplicitamente come carry-over in 3 bucket (§10).

---

## 2. Perimetro coperto

| Blocco | File principali / aree | Stato |
|---|---|---|
| Track A — TESTS | `backend/tests2/` (10 moduli nuovi) | chiuso |
| Track A — ACN | ADR 0001-0011, audit trail, MFA, log immutable, SSM | chiuso |
| Track A — FE | `frontend/src/` PreviewModal + ResultNode split + hooks | chiuso |
| Track A — PERF | `transforms/spatial.py`, `op_concat`, `op_md5`, bench, baseline | chiuso |
| Track B — micro-consolidation | 7 file backend (vedi §5) | chiuso (S28 triage) |
| Track C — governance | `d3_*`, `d4_*`, `d5_*`, `d6_*` | parziale (vedi §6) |
| Architecture A1 | `storage_config.py`, `result_store.py`, `file_service.py` | chiuso design + impl canonica |
| Integration | deps, beat retention, MFA hotfixes | chiuso |

---

## 3. Track A — stream closure

**Head dei 4 stream mergiati su main:**

| Stream | Head commit | Riferimento |
|---|---|---|
| TESTS | `20b8cad` | `[stream-tests] closeout: stream TESTS done (90 test, 10 commit contract)` |
| ACN | `7f24fba` | `[stream-acn] adr: backfill ADR-0002..0011 + link in README` |
| FE | `83d7765` | `[stream-fe] hooks: useEditorData cancellation fixes stale writes` |
| PERF | `61a3446` | `[stream-perf] perf/spatial: batch 3 — voronoi STRtree (181x) + clean_topology incremental union (14x)` |

Dettaglio chiusure per stream: `STREAM_REPORT.md`.

Punti non-tecnici da registrare:

- L'ordine di merge effettivo ha rispettato `done_check.md` §Merge sequenziale: TESTS → ACN → FE → PERF.
- Nessun merge ha prodotto conflitti numerosi (>10 file) su stream singolo → disciplina di ownership tenuta.
- Nessun rollback di merge necessario.

---

## 4. Track A — integration hotfixes post-merge

Fix applicati dopo i 4 merge, necessari per allineare ambiente di test e prod:

| Commit | Scope | Motivo |
|---|---|---|
| `0d2b163` | `backend/requirements.txt` | Aggiunta `pyotp` + `qrcode[pil]` per stream ACN MFA |
| `583c2a2` | `frontend/package.json` | Aggiunta `react-window` + `i18next` + `react-i18next` per stream FE |
| `a32c9f3` | `backend/app/beat.py` retention DELETE | `SET LOCAL plenora.retention_cleanup = 'true'` prima della DELETE sui `plenora.logs`, per coesistere con i trigger log-immutable introdotti dallo stream ACN. Vedi §9 per il tema log policy |
| `f0c1b5d` | `backend/tests2/conftest.py` fixture `mfa_user` | Tipi PG-compatibili per `is_active` (bool) / `created_at` / `updated_at` (timestamp). La fixture usava tipi SQLite che rompevano su PG |
| `3f45f7a` | `backend/app/core/mfa/service.py` `status()` | Gestione timestamp `str \| datetime \| None` robusta (driver PG puo' restituire stringa, datetime o None a seconda del path) |

Questi fix non riaprono gli stream originari: chiudono anomalie di integrazione emerse solo su `main` post-merge.

---

## 5. Track B — consolidation-micro S17-S28

**Commit atomici, prefisso `[consolidation-phase-1]`:**

| ID | Commit(s) | Perimetro | Pattern applicato |
|---|---|---|---|
| S17 | `91b7109` | `app/services/file_service.py` | silent `except: pass` → `logger.debug/warning` con contesto |
| S18 | `60dadc7` | `app/core/arricchisci/api_connector.py` | retry loop con logging esplicito del fallback |
| S23 | `e006b56` | `app/core/carica/database_writer.py` | cleanup silent → debug logs (coordinato con PERF per non sovrapporre) |
| S25 | `ef3ba63` | `app/core/eseguibili/router.py` | silent → debug/warning logs, scope router-localizzato |
| S26 | `db9ef03`, `6db33c7` | preview swallow `arricchisci` + `manipola` | "no silent swallow" — pattern tattico D6 applicato **prima** della ratifica formale |
| S27 | `92f0c31`, `9e1246b`, `be20d19`, `afe3aba`, `834bf91` | cluster IMDF (router files_list, loader drop_z, storage unlink tmp_zip/tmp_meta hydrate+upload) | silent → debug logs, preservando retro-compat |
| S28 | — (no code change) | `app/shared/authz_helpers.py`, `token_blacklist.py`, `sharing.py` | **chiuso come triage** il 2026-04-20: perimetro gia' conforme a `invariants.md` §4.1/§4.3/§2. Dettaglio in `orchestrate/phase_1_stabilization/kickoff.md` §Aggiornamenti |

**Totale Track B**: 11 commit di codice + 1 triage S28 (zero code change).

---

## 6. Track C — governance D3-D6

| ID | Esito | Commit(s) | Carry-over |
|---|---|---|---|
| D3 — IMDF loader/persist/storage boundaries | design doc consolidato, **nessun refactor codice applicato** | `orchestrate/phase_1_stabilization/d3_imdf_boundaries.md` (solo doc) | Refactor boundaries → Phase 2 |
| D4 — crypto aliases retro-compat | parziale: `URBY_DISABLE_DEFAULT_KEY` rimosso | `3924bfb` | 2 alias residui (`URBY_ENCRYPTION_KEY`, `CONN_ENCRYPT_KEY`) → **bucket operation** (verifica `.env` lab.plenora.it via SSH) |
| D5 — SECRET_KEY / JWT_SECRET_KEY fallback | chiuso | `9fc330a` (rimozione fallback), `81ac385` (docs invariants + inventory) | — |
| D6 — preview vs execute policy | policy proposta non ancora ratificata come ADR formale; la parte tattica "no silent swallow" e' gia' applicata via S26 | `orchestrate/phase_1_stabilization/d6_preview_execute_policy.md` | Ratifica policy come ADR → **bucket micro_0bis** |

Nota onesta su D6: il codice e' gia' allineato al comportamento atteso dal design doc (via S26 `db9ef03` + `6db33c7`), ma manca la ratifica formale. E' un gap doc, non comportamentale.

---

## 7. Architecture — A1 storage policy

Design doc: `orchestrate/phase_1_stabilization/a1_storage_policy.md`.

Implementazione canonica applicata in 3 commit atomici `[consolidation-phase-1]`:

| Commit | Scope |
|---|---|
| `2a80970` | A1 commit A — `validate_storage_config` + `DEFAULTS` (fail-fast su config invalida all'avvio) |
| `1176c32` | A1 commit B — `result_store` dispatch policy-aware (db / object_storage / hybrid) |
| `c5578db` | A1 commit C — `file_service` dispatch policy-aware, simmetria con FS-4 |

Provider attualmente wirati: DB locale + AWS S3. MinIO come provider P1: design approvato ma implementazione non in scope Phase 1 (carry-over Phase 2). Azure/GCP esplicitamente fuori scope (A1 §8).

`invariants.md §3.9` cambia stato da "keep + documenta" a "keep + enforced by A1 policy" (vedi §8 qui sotto).

---

## 8. Aggiornamenti `invariants.md`

Modifiche applicate in commit dedicato con prefisso `[invariants]`:

1. **Header** — `Status: aggiornato post Phase 1 — 2026-04-21`.
2. **§3 Core path canonici** — aggiunta voce STORAGE_POLICY come path canonico per dispatch storage; D4 registrato come decisione parziale con link al bucket operation.
3. **§3.9** — passaggio da "keep + documenta" a "keep + enforced by A1 policy" con riferimento ai 3 commit A1.
4. **§6 Residuals roll-up** — rimozione S17-S27 dalla lista "riaprire dopo / batch dedicato"; aggiornamento stato D3/D4/D6 secondo §6 qui sopra.

Nessun cambio a §1, §2, §4, §5 (policy di uniformita' e review rules restano invariate).

---

## 9. Tema log — chiarimento esplicito

Questo punto va registrato senza ambiguita' perche' `SET LOCAL plenora.retention_cleanup` applicato in `a32c9f3` potrebbe essere letto come risoluzione piu' ampia di quanto sia effettivamente:

- **Cosa risolve `SET LOCAL plenora.retention_cleanup`**: sblocca **esclusivamente** il job di retention autorizzato (Celery beat DELETE su `plenora.logs`) rispetto ai trigger log-immutable introdotti da stream ACN. Flag session-local, valida solo dentro la transazione del job.

- **Cosa NON risolve**: il lifecycle complessivo dei log. Append-only enforcement, retention policy formale, purge controllato restano temi **non trattati** in Phase 1.

- **Dove va la review architetturale log (append-only / retention / purge controllato)**: **tema Phase 2**. Da portare come design dedicato, con ADR di supporto, con test contract che verifichino l'invariante di immutabilita' rispetto a scenari di attacco / errore operativo / compliance audit.

- **Cosa non e' ammesso nel frattempo**: nessun bypass manuale implicito della policy. `SET LOCAL plenora.retention_cleanup` e' l'unico path autorizzato e va tenuto scoped al beat job; non puo' diventare uno `set-and-forget` di scope piu' largo ne' un'escape hatch per code path che dovrebbero rispettare append-only.

Il commit `a32c9f3` e' percio' da leggere come fix operativo puntuale, non come chiusura del tema log.

---

## 10. Aggiornamenti `inventory_report.md`

`backend/docs/architecture/inventory_report.md` §5.5 "Residuals classification":

- **Rimossi da "Riaprire dopo / batch dedicato"**: S17 (`91b7109`), S18 (`60dadc7`), S23 (`e006b56`), S25 (`ef3ba63`), S26 (`db9ef03`, `6db33c7`), S27 (`92f0c31`, `9e1246b`, `be20d19`, `afe3aba`, `834bf91`), S28 (chiuso 2026-04-20 come triage).
- **Spostati in "Tenere per fase successiva"**: D3 (Phase 2), A1 follow-up (Phase 2, MinIO provider + bulk review).
- **Chiusi**: D5 (`9fc330a`, `81ac385`).
- **Parziali**: D4 (`3924bfb` rimozione `URBY_DISABLE_DEFAULT_KEY`; 2 alias residui su bucket operation).
- **Carry-over ratifica formale**: D6 (policy design gia' applicata tatticamente via S26; ratifica ADR formale su bucket micro_0bis).

---

## 11. Carry-over — classificazione per bucket

### Bucket `operation`

Lavoro che richiede accesso / verifica su ambiente prod o staging e non si chiude da main worktree:

- **D4 alias crypto residui** — `URBY_ENCRYPTION_KEY` e `CONN_ENCRYPT_KEY`. Bloccati su verifica `.env` su `lab.plenora.it` via SSH. Quando l'accesso e' disponibile, controllare quali alias sono effettivamente in uso in prod e decidere se mantenere il fallback o dismettere tramite ADR.

### Bucket `phase_2`

Tematiche architetturali che richiedono design dedicato + execution dedicata:

- **D3 — refactor IMDF boundaries** (loader / persist / storage). `site_id` risolto in 3+ posti, hydration duplicata. Design doc gia' scritto, execution in Phase 2.
- **A1 follow-up** — provider MinIO P1 implementation + contract test per matrice policy/provider; bulk review di tutti i chiamanti file storage per allineamento al dispatch canonico.
- **Log lifecycle complessivo** — append-only enforcement architetturale, retention policy formale, purge controllato. Vedi §9.
- **Tematiche emerse dagli STREAM_REPORT** — raccolte in `orchestrate/phase_2_hardening/` durante apertura fase 2.

### Bucket `micro_0bis`

Housekeeping documentale / ratifica formale, chiudibile in un pass doc-only successivo:

- **D6 ratifica ADR formale** — il comportamento e' gia' in codice (S26), manca il record ADR in `backend/docs/architecture/decisions/`.
- **Doc housekeeping** — allineamenti minori di `invariants.md` / `inventory_report.md` / README orchestrate che emergono naturalmente rileggendo post-chiusura. Aggregati in un pass unico, non in commit sparsi.

---

## 12. Verdetto finale

**Phase 1 Stabilization: CHIUSA il 2026-04-21.**

- Track A: 4/4 stream mergiati, STREAM_REPORT consolidato.
- Track B: 7/7 item chiusi (S17-S27 via commit, S28 via triage).
- Track C: 1/4 chiuso (D5), 1/4 parziale con carry-over esplicito (D4), 2/4 in carry-over classificato (D3 → phase_2, D6 → micro_0bis).
- Architecture A1: design + impl canonica applicata, follow-up provider in phase_2.

Non ci sono chiusure forzate. Ogni item non-done e' registrato per nome nel bucket appropriato e il lavoro residuo e' scoped in modo non ambiguo.

La fase termina con margine sufficiente per aprire il pass **micro-0bis** (cleanup doc-only + ratifica D6) prima di Phase 2 Hardening, rispettando la disciplina di commit atomici e l'invariant "un solo path canonico".

---

## 13. Riferimenti

- Kickoff: `orchestrate/phase_1_stabilization/kickoff.md`
- Done-check runbook: `orchestrate/phase_1_stabilization/done_check.md`
- Mirror orchestrate (local): `orchestrate/phase_1_stabilization/closeout_report.md`
- STREAM_REPORT consolidato: `STREAM_REPORT.md`
- Invariants: `backend/docs/architecture/invariants.md`
- Inventory: `backend/docs/architecture/inventory_report.md`
- Design docs Track C: `orchestrate/phase_1_stabilization/d3_imdf_boundaries.md`, `d4_crypto_aliases.md`, `d5_secret_fallbacks.md`, `d6_preview_execute_policy.md`
- Design doc Architecture: `orchestrate/phase_1_stabilization/a1_storage_policy.md`
- Precedente closeout Phase 0: `CONSOLIDATION_REPORT.md`
