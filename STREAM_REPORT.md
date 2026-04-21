# STREAM TESTS — Report Fase 1 Stabilization

**Status**: in_progress
**Last updated**: 2026-04-21 (Batch 2 Datasources — service + factory + local + ftp done, 39/~40)

## Scope

Recupero debito test sul refactor massivo 2026-04-17/18 (~7400 LOC, ratio test-per-line ~2.7%). Target brief: 30+ contract test nuovi sui 4 domini refactorati (datasources, scheduler, enrichment, crypto). Zero modifiche a `backend/app/**`.

Riferimenti: `orchestrate/phase_1_stabilization/brief_tests.md`, `backend/docs/architecture/invariants.md`.

## Baseline inventario pre-batch

Test esistenti nelle dir owned:

| Dir owned | File presenti | Note |
|---|---|---|
| `backend/tests2/datasources/` | — (dir **DA CREARE**) | 1 test correlato in `shared/test_datasource_local_multi_host.py` (142L), 2 in `security/unit/` (`test_datasource_crypto_contract.py` 89L, `test_local_datasource_contract.py` 50L) |
| `backend/tests2/scheduler/unit/` | 27 file | baseline ricca — **integrare non duplicare** |
| `backend/tests2/arricchisci/unit/` | 16 file | focus su `api_connector`; gap su `enrichment_service.py` (+1722L) |
| `backend/tests2/crypto/` | — (dir **DA CREARE**) | 1 contract shallow (53L, 4 test) in `security/unit/test_shared_crypto_contract.py` |
| `backend/tests2/shared/` | 28 file | integra gap mirato |
| `backend/tests2/services/` | 6 file | gap coverage opzionale (cross con Track B S17) |

LOC source target (read-only):
- datasources 7 moduli, **2442L**
- scheduler 8 moduli, **3811L**
- enrichment_service **2003L**
- shared/crypto **272L**

## Piano di esecuzione (4 batch)

### Batch 1 — Crypto (target 5+ test)
Dir **CREATE**: `backend/tests2/crypto/`. File previsti:
- `test_crypto_fernet_contract.py` (5 test): roundtrip, tamper detection, wrong key → `None`, allowlist plaintext, nonce uniqueness
- `test_crypto_key_sourcing_contract.py` (3-4 test): chain env priority, D5-regression (no fallback su SECRET_KEY/JWT_SECRET_KEY, chiuso 2026-04-20), fail-fast in prod

Invariants blindati: §1.6 riga crypto (fail-fast encrypt, decrypt→None), §2 riga `decrypt()` (fallback autorizzato su InvalidToken), §3 coda §1.5 (env var chain), §6 (D5 chiusa).

### Batch 2 — Datasources (target 15 test)
Dir **CREATE**: `backend/tests2/datasources/`. 7 file contract:
- factory dispatch, local multi-host extend, ftp error-typed, sftp RejectPolicy, sharepoint whitelist, service crypto roundtrip, service cross-tenant + N+1 guard

Invariants blindati: §1.6 fail-fast + §2 crypto decrypt fallback + §4.1 authz fail-closed.

### Batch 3 — Scheduler (target 10 test)
Aggiunte in `backend/tests2/scheduler/unit/` **senza duplicare** i 27 esistenti. Focus gap:
- concurrency_manager lock expire + fail-closed DB
- policy_engine backoff + idempotency
- execution_manager orphan cleanup post-task-kill

Invariants blindati: §1.6 scheduler fail-fast + §1.3 transactions facade.

### Batch 4 — Enrichment (target 8 test)
Aggiunte in `backend/tests2/arricchisci/unit/`. **Focus su `enrichment_service.py`** (gap reale, 2003L). **Escludo api_connector** per evitare collisione con Track B S18.
- rate limiter token bucket, 429 retry-after, parallel rate-limit globale, retry exhausted typed error, cancellation, partial success, idempotent re-run

Invariants blindati: §2 riga `enrichment_service:408-410` async safety-net + §3 flag `ENABLE_ASYNC_ENRICHMENT`.

**Totale atteso**: ~38 test nuovi → supera il target 30+.

## Test scritti (tot)

- Datasources: **39** (5 file, batch 2 in corso)
  - `tests2/datasources/test_factory_dispatch_contract.py` — 11 test: dispatch parametrico (4 tipi) → classe corretta, fail-fast su tipo sconosciuto/missing/empty, contract BackendConfig structured-vs-extra, filtro `None` con False/empty-string preservati, `supported_types()` ritorna i 4 canonici, meta-contract dispatch ↔ supported_types
  - `tests2/datasources/test_service_crypto_contract.py` — 6 test (gap coverage vs legacy `security/unit/test_datasource_crypto_contract.py`): update re-cifra con nuovo valore, update skip su `"***"` masked (CVE-class regression guard), update strips `group_id`/`created_by` dal blob salvato, `_to_model(redact=True)` maschera 4 sensitive in `_SENSITIVE_FIELDS`, `_to_model(redact=False)` preserva plaintext per uso interno, update su ds inesistente → `None` senza side-effect DB
  - `tests2/datasources/test_service_crud_contract.py` — 7 test CRUD + tenant: list WHERE group_id con binding `%s` (cross-tenant fail-closed), list senza WHERE quando group_id=None (path superadmin), **N+1 guard** (1 fetchall + 0 get_datasource per-row con counter su 5 row), delete non-existent → False senza DB delete (idempotenza), create id pattern canonico `ds_<name>_<date>_<time>_<uuid8>`, get_scan_state JSON corrotto → rename `.broken.<ts>` + empty, get_scan_state file assente → empty no write
  - `tests2/datasources/test_local_edge_contract.py` — 7 test edge case su `local.py` (gap reali vs `shared/test_datasource_local_multi_host.py` + `security/unit/test_local_datasource_contract.py`): `_abs` rifiuta drive letter Windows (branch separata da `/etc/passwd`), `_abs("")` → root_abs (early-return), `_abs` deep nested join 3+ livelli, **symlink escape** via resolve() (skip su Windows no dev-mode), `_rel` normalizza backslash→forward slash (portability), `file_exists` False su directory (os.path.isfile, non exists), `write_file` crea parent dir mancanti (os.makedirs exist_ok)
  - `tests2/datasources/test_ftp_error_contract.py` — 8 test error mapping (nessun test preesistente su FTPDataSource): missing host → fail-fast NO network call (counter istanziazioni), ftplib.all_errors generico → ConnectionError chained con `__cause__`, auth failure (error_perm su login) → ConnectionError, root cwd fail → ConnectionError con messaggio specifico, operation senza connect → `_check` gate ConnectionError "Non connesso", read_file 550 → FileNotFound, read_file non-550 error_perm (530) → PermissionError, file_exists fail-closed → False silent
- Scheduler: 0 (batch 3)
- Enrichment: 0 (batch 4)
- Crypto: **13** (2 file, batch 1 ✅)
  - `tests2/crypto/test_crypto_fernet_contract.py` — 5 test: roundtrip, tamper detection (MAC fail → None + log error), wrong key → None, allowlist plaintext (non-string / assenti / fuori lista), nonce uniqueness (IV random)
  - `tests2/crypto/test_crypto_key_sourcing_contract.py` — 8 test: chain priority PLENORA > URBY > CONN (3 test), **D5 regression** (no fallback su SECRET_KEY/JWT_SECRET_KEY, chiuso 2026-04-20), prod fail-fast no-key, prod fail-fast dev-default-leak, min-length < 16 char in any env, PLENORA_DISABLE_DEFAULT_KEY opt-in dev
- **TOTALE**: **52 / ~38** (target 30+ superato, procedo su sftp/sharepoint + batch 3/4)

## Bug / anomalie source trovati durante studio (NON fissati)

- `backend/tests2/pytest.ini:7` — `pythonpath = C:\Users\Marco\Desktop\int_efm\PLENORA-ETL\backend` punta a worktree inesistente (`PLENORA-ETL/` non presente). Il `conftest.py:8-15` corregge runtime via `_ensure_backend_on_path()`, quindi i test girano. Bug documentale, severity: **trascurabile**. Non toccato (potrebbe essere intenzionale cross-worktree). Integration manager decide.
- `backend/tests2/security/unit/test_datasource_crypto_contract.py` — **collection error** in esecuzione isolata perché importa indirettamente `app.db.pg_connection` che esige `DATABASE_URL` al top-level (`pg_connection.py:113`). Il test NON è mio perimetro (precedente), ma la dipendenza hard `DATABASE_URL` at import-time rende difficile girare test unit-crypto isolati su un environment senza PG. Severity: **bassa** (i test girano in CI con env var settate). Annoto per integration manager: eventuale lazy-init di `pg_connection` renderebbe testabile off-grid.

## Deps nuove richieste (via requirements-dev.txt)

Nessuna al momento. Verifica al primo test che lo richieda:
- `freezegun` (potenziale Batch 3 policy_engine backoff time-dependent)
- `fakeredis` (potenziale Batch 3 se scheduler usa Redis — verifica quando arrivo)
- `moto` — **non previsto** in questa fase

## Metriche

- Test esistenti tutti verdi: **baseline verificata su `test_shared_crypto_contract.py`** (4/4 pass), legacy crypto esistente non toccato.
- Test nuovi tutti verdi: **13/13 pass** (`pytest tests2/crypto/ -q` → 13 passed in 0.57s).
- Coverage: — (a fine stream)

## Collisioni cross-stream

- **FE**: disgiunto (frontend/**). Zero.
- **PERF**: PERF tocca `carica/database_writer.py` + `manipola/transforms`. Non testo questi moduli. Zero.
- **ACN**: ACN crea `auth/mfa.py`, `shared/audit_action.py`, aggiunge decoratori sui router. I miei test sono service/unit, non HTTP. Zero hard.
- **Track B consolidation-phase-1** (main, seriale):
  - S17 tocca `app/services/file_service.py` → `tests2/services/` è priorità 4 opzionale, eventualmente differito
  - S18 tocca `app/core/arricchisci/api_connector.py` → **Batch 4 evita api_connector**, focus su `enrichment_service.py` (decisione concordata al checkpoint iniziale)

## Proposte Fase 2

(da compilare a fine stream)
