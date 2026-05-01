# CI Red — 3 Binari Plan (Quick Unblock + Taxonomy + Bug Reali)

Status: **analysis only — no patch, no deploy, no test runtime.**

Run di riferimento: `25093005588` post-merge PR #4 (38 failed + 38 errors).

---

## Binario A — Quick CI Unblock

3 cluster a basso effort, indipendenti, ognuno propria PR.

### A1 — Cluster F5: BACKUP_BUCKET env mancante in CI

#### Conferma file/test esatti

`backend/tests2/admin_backups/test_degraded_paths.py`, 4 test:
- `test_list_backups_when_boto3_missing_returns_empty`
- `test_list_backups_when_client_error_returns_empty`
- `test_list_backups_raises_on_missing_dependency`
- `test_list_backups_empty_bucket_returns_empty`

#### Root cause

I test sono `unit + contract` (riga 22, `pytestmark = [pytest.mark.unit, pytest.mark.contract]`). Chiamano `from app.core.admin_backups import service` + `service.list_backups()`. Il path `list_backups()` invoca `cloud_backends.get_backup_object_storage()` che a sua volta chiama `_get_backup_bucket()` che legge `os.getenv("BACKUP_BUCKET")` (o `BACKUP_S3_BUCKET` legacy).

Workflow CI ha `STORAGE_POLICY=db` + `DATABASE_URL` dummy + Redis env, ma **nessun `BACKUP_BUCKET`**. Il `_get_backup_bucket()` solleva `ValueError: BACKUP_BUCKET (o BACKUP_S3_BUCKET legacy) non configurato`.

Ironia: i 4 test sono `degraded_paths` — testano scenari di error (boto3 missing, ClientError, ecc.) — ma falliscono prima di arrivare al test stesso, perché il setup env esplode.

#### Tipologia

**Env CI missing**, non dependency.

#### Patch minima

Aggiungere 1 env var al workflow `Backend pytest` job:

```yaml
env:
  STORAGE_POLICY: db
  DATABASE_URL: postgresql://nobody:nopass@127.0.0.1:1/test_unit_no_real_db
  BACKUP_BUCKET: test-backup-bucket-no-real-s3
  CELERY_BROKER_URL: redis://localhost:6379/0
  CELERY_RESULT_BACKEND: redis://localhost:6379/0
```

**Pattern identico a PR #2** (DATABASE_URL dummy). Il bucket non viene mai realmente toccato — i test mockano `boto3` internamente. Serve solo a far passare il guard `_get_backup_bucket()`.

#### Branch + scope

- **Branch**: `chore/ci-fix-backend-backup-bucket-env`
- **Scope**: 1 file (`.github/workflows/ci.yml`), +1 env var
- **Effort**: ~10 min
- **Sblocca**: 4 test
- **Approvazione richiesta**: SÌ.

---

### A2 — Cluster F4: pyodbc residuo in test/services/

#### Conferma file/test esatti

`backend/tests2/services/test_connection_service_mssql_sanitize.py`, 3 test:
- `test_test_mssql_does_not_leak_raw_sqlstate_in_message_or_details`
- `test_test_mssql_aggregated_message_cites_sanitized_first_error`
- `test_test_mssql_log_preserves_technical_detail`

#### Root cause

Test marcati `pytest.mark.unit + contract + security` (riga 30). Importano:

```python
from app.services.connection_service import (
    ConnectionService,
    _sanitize_mssql_error,
)
```

`ConnectionService` importa transitive `pyodbc` per il path MSSQL. Il file è in `tests2/services/`, NON in `tests2/integration/`, quindi **non passa per i 4 harness integration** che ora hanno `pytest.importorskip("pyodbc")` (PR #4).

#### Tipologia

**Dependency missing — pattern simmetrico a PR #4 ma scope ridotto** (1 file).

#### Patch minima

Opzioni:

| Opzione | Pro | Contro |
| --- | --- | --- |
| **A** `pytest.importorskip("pyodbc")` a module-level del test | Stesso pattern PR #4, chirurgico | Test skippato in CI, copertura ridotta |
| **B** Refactor `connection_service` per lazy import di `pyodbc` solo dentro funzione MSSQL | Architetturale, fix il problema runtime | Scope diverso, fuori da CI red baseline batch |
| **C** Aggiungere `pyodbc` a `requirements-dev.txt` | Test gira sempre | `pyodbc` richiede `unixodbc-dev` apt + `msodbcsql18`; complessità install runner |

**Raccomandazione**: opzione **A** (importorskip). I test sono `security/contract` — copertura locale invariata, in CI skippati. Pattern già accettato per PR #4.

#### Branch + scope

- **Branch**: `chore/ci-fix-backend-pyodbc-services`
- **Scope**: 1 file (`backend/tests2/services/test_connection_service_mssql_sanitize.py`), +2 righe (`import pytest` se mancante + `pytest.importorskip`)
- **Effort**: ~10 min
- **Sblocca**: 3 test
- **Approvazione richiesta**: SÌ.

---

### A3 — Cluster F2 + F3: pyarrow / openpyxl / fastparquet (gap reale prod)

#### Conferma file/test esatti

**F2 — pyarrow / fastparquet** (8 failed):
- `tests2/intermediate/unit/test_intermediate_run_data_artifact_contract.py` × 3 test
- `tests2/intermediate/unit/test_full_artifact_contract.py` × 2 test (downstream conseguenza)
- `tests2/arricchisci/unit/test_materialize_in_memory_contract.py` × 1 (parquet param)
- `tests2/shared/test_in_memory_result_contract.py` × 1 (parquet param)

**F3 — openpyxl** (5 failed):
- `tests2/arricchisci/unit/test_materialize_in_memory_contract.py` × 3 test (xlsx)
- `tests2/shared/test_in_memory_result_contract.py` × 1 (xlsx param)

Errori fastparquet (2) sono fallback di `pandas` quando `pyarrow` manca: stesso cluster F2, stesso fix.

#### Root cause (CRITICA)

**`pyarrow` e `openpyxl` sono usati in produzione MA NON sono in `requirements.txt`**. Verificato:

| Modulo | Usato in prod | In `requirements.txt`? | In `requirements-dev.txt`? |
| --- | --- | --- | --- |
| `pyarrow` | `app/core/intermediate/artifact_writer.py:158` (`df.to_parquet(buf, engine="pyarrow")`), `artifact_reader.py:107` (`pd.read_parquet`), `db/query_executor.py:736-737` (`import pyarrow as _pa; import pyarrow.parquet as _pq`) | ❌ NO | ❌ NO |
| `openpyxl` | `app/core/features/features_service.py:687, 1224` (`df.to_excel`, `pd.read_excel`), `db/query_executor.py:517` (`df.to_excel`) | ❌ NO | ❌ NO |
| `fastparquet` | (non importato direttamente dal codice; è solo fallback engine di pandas dopo pyarrow) | ❌ NO | ❌ NO |
| `pyodbc` | `app/services/connection_service.py` (test/MSSQL connect) | ❌ NO | ❌ NO |

In **dev locale**, `pyarrow` e `openpyxl` vengono installati come transitive di altre lib (es. `pandas[excel]`, `geopandas`, ecc.) o per scelta dell'operatore. In **CI runner clean**, niente di tutto questo.

**Questo NON è un gap solo di test**: è un gap reale di **runtime di produzione**. Una build pulita del backend deployed potrebbe rompersi appena qualcuno fa export xlsx o usa la feature `full_artifact`.

#### Tipologia

**Production gap esposto da CI** — non era visibile prima perché collection si fermava a monte.

#### Patch minima

Aggiungere a `backend/requirements.txt` (NON `requirements-dev.txt`, perché sono dipendenze runtime di produzione):

```
# Pandas I/O optional engines, usati attivamente in produzione:
# - pyarrow: parquet read/write (intermediate full_artifact, query_executor)
# - openpyxl: xlsx read/write (features service, query_executor)
pyarrow>=15.0
openpyxl>=3.1
```

Niente `fastparquet`: pandas usa `pyarrow` come default e `fastparquet` come fallback opzionale. Se `pyarrow` è disponibile, `fastparquet` non serve.

| Opzione | Pro | Contro |
| --- | --- | --- |
| **A (raccomandata)** Aggiungere a `requirements.txt` | **Fixa gap reale prod**, non solo CI; allinea dev/test/prod; sblocca 13 test | Aumenta size install (~30-35 MB pyarrow + ~5 MB openpyxl); allunga tempo install in CI di ~30s |
| **B** `pytest.importorskip` su tutti i 4 file test | Niente install change | Maschera gap prod reale; install prod resta rotta su build fresca |
| **C** Aggiungere a `requirements-dev.txt` | Sblocca CI, niente prod cambio | Maschera comunque il gap prod (codice fa import a module-level: artifact_writer.py importa `pyarrow` direttamente) |

**Raccomandazione**: opzione **A**. È il pattern corretto perché il codice prod ha `import pyarrow as _pa` a riga 736 di `query_executor.py` come module-level import — su prod fresh install romperebbe immediatamente.

#### Branch + scope

- **Branch**: `chore/deps-add-pyarrow-openpyxl-runtime`
- **Scope**: 1 file (`backend/requirements.txt`), +5 righe (2 deps + commento)
- **Effort**: ~15 min
- **Sblocca**: 13 test
- **Approvazione richiesta**: SÌ — questo è il **fix più importante del binario A** perché è gap reale di produzione, non solo CI noise.

---

### Riepilogo Binario A

| PR | Cluster | File | LOC delta | Effort | Sblocca | Tipo |
| --- | --- | --- | ---:| ---:| ---:| --- |
| **PR 5** | F5 | `.github/workflows/ci.yml` | +1 env | 10min | 4 test | env CI |
| **PR 6** | F4 | `tests2/services/test_connection_service_mssql_sanitize.py` | +2 | 10min | 3 test | importorskip |
| **PR 7** | F2+F3 | `backend/requirements.txt` | +5 | 15min | 13 test | **prod dependency gap** |

3 PR indipendenti, totale ~35 min effort, sblocca 20 test su 38 failed (53%).

**Approvazione richiesta** prima di patchare ognuna.

---

## Binario B — CI Taxonomy

Decidere **cosa deve girare in quale workflow** per evitare peeling infinito di failure unrelated al cambio.

### Stato attuale

`.github/workflows/ci.yml` ha 1 workflow `CI` con 2 job (`Backend pytest`, `Frontend vitest`), trigger su `push: main` + `pull_request: main`. Esegue **tutto** `pytest tests2/` indistintamente.

### Markers già presenti

Verificati in `backend/tests2/pytest.ini:19–36`:

```
markers =
    unit, functional, security, contract, regression, scheduler,
    arricchisci, carica, features, manipola, orchestrator,
    intermediate, eseguibili, estrai, shared, integration
```

Esistono già marker `unit`, `functional`, `integration` ma **non vengono usati per filtraggio nel workflow CI**. Il commento `:5–7` dice "i test che richiedono DB live si auto-skip via guard interno" → era il piano originale, non implementato a livello workflow.

### Problema

Il workflow attuale non distingue tra:
- **Test unit/contract**: girano dovunque, senza dipendenze esterne reali.
- **Test functional**: spawnano backend uvicorn locale, richiedono PG (cluster F1).
- **Test integration**: richiedono DB container reali (PG/MySQL/MSSQL/Oracle) — i driver opzionali sono già coperti da PR #4 importorskip.
- **Test performance**: benchmark tempo-sensibili.

Il job `Backend pytest` esegue il superset → ogni dependency reale di un cluster integration/functional fa fallire l'intero job.

### Proposta tassonomia

| Tier | Cosa | Run | Trigger | Marker pytest filter |
| --- | --- | --- | --- | --- |
| **T1 — Unit/Contract** | unit + contract + security + regression marcati | sempre, `push`/`pull_request` su `main` | ogni push/PR | `pytest -m "(unit or contract or security or regression) and not integration and not functional"` |
| **T2 — Functional** | functional marcati (spawn backend, harness) | nightly cron + manual `workflow_dispatch` | scheduled / on-demand | `pytest -m "functional"` con env extra (PG service container, ecc.) |
| **T3 — Integration** | integration marcati (DB container reali) | nightly + manual | scheduled / on-demand | `pytest -m "integration"` con DB matrix (PG/MySQL/MSSQL/Oracle services) |
| **T4 — Performance** | performance/bench (long running) | weekly cron + manual | scheduled / on-demand | `pytest tests2/performance/` |

### Workflow file proposti (struttura)

| File | Trigger | Tier |
| --- | --- | --- |
| `.github/workflows/ci.yml` | push/pull_request main | **T1** (rinominato eventualmente `ci-quick.yml`) |
| `.github/workflows/ci-functional.yml` | nightly + workflow_dispatch | T2 |
| `.github/workflows/ci-integration.yml` | nightly + workflow_dispatch | T3 |
| `.github/workflows/ci-performance.yml` | weekly + workflow_dispatch | T4 |

Workflow T2/T3/T4 falliscono ≠ blocker su PR. Workflow T1 deve essere verde-stable per ogni PR.

### Implementation gate

**Pre-requisito**: tutti i test esistenti devono avere marker `unit`/`functional`/`integration` corretto. Verifica preliminare richiesta:

```bash
# Read-only:
grep -rE "pytestmark|@pytest.mark" backend/tests2/ | grep -cE "(unit|functional|integration)"
```

Stima coverage marker: i test di `tests2/integration/` hanno `pytestmark = [pytest.mark.integration, ...]` (verificato 0.5d), così come `tests2/unit/` (impliciti via path). I `tests2/functional/` necessitano review.

### Cosa NON fare

- **NON** riorganizzare directory test (es. spostare file da `unit/` a `integration/` solo per matchare il marker). Lascia struttura, usa solo i marker.
- **NON** introdurre dipendenza tra workflow (es. T2 dipende da T1 verde): mantieni indipendenti.
- **NON** mergeare T2/T3 verde in critical path PR — quei tier sono diagnostic, non gate.

### Effort tassonomia

| Step | Effort | Output |
| --- | ---:| --- |
| Audit marker copertura tests2/ | ~1h | report file → marker |
| Patch T1 workflow (filter `-m "unit or contract or security"`) | ~30min | nuovo `ci.yml` filtered |
| Creazione T2/T3 workflow scheduled | ~1h | 2 nuovi `.github/workflows/*.yml` |
| Documentazione + onboarding | ~30min | `CONTRIBUTING.md` o `docs/ops/ci-tiers.md` |
| **Totale** | **~3h** | 3 workflow + doc |

### Branch proposto

`chore/ci-tiers-taxonomy` — singola PR (workflow files coesi).

**Approvazione richiesta**: SÌ. Decisione strategica più ampia delle 3 quick PR del binario A. Va aperta separatamente, idealmente DOPO che A1+A2+A3 hanno chiuso il rumore di base.

---

## Binario C — Bug Reali (NON CI noise)

### C1 — Cluster F6: Scheduler beat / missed-run / cron logic (12 failed)

#### Test interessati

| File | Test count | Cosa testa |
| --- | --- | ---:| --- |
| `tests2/scheduler/unit/test_beat_scheduler_catchup_contract.py` | 4 | beat scheduler emette task catchup |
| `tests2/scheduler/unit/test_beat_scheduler_catchup_no_premature_update_contract.py` | 3 | beat NON aggiorna `last_run` prematuramente |
| `tests2/scheduler/unit/test_detect_missed_run_contract.py` | 5 | detect missed run logic + grace window |
| `tests2/scheduler/unit/test_schedule_service_next_execution_contract.py` | 4 | next execution datetime calculation |

Failure pattern:
```
AssertionError: Expected 'send_task' to have been called once. Called 0 times.
AssertionError: nessuna INSERT su schedule_catchup_history
assert None == datetime.datetime(2026, 4, 16, 6, 0, tzinfo=datetime.timezone.utc)
assert None is not None
assert False
```

#### Tipologia

**Bug reale** — non dependency, non env, non skip. I contract test sono lì per blindare invarianti specifiche dello scheduler. Pattern dei failure suggerisce **regression temporale**:

- `assert None == datetime(2026, 4, 16, ...)` — il test si aspettava un datetime concreto e ha ricevuto `None`. Probabile drift: test assume "missed run rilevato entro grace window" ma al 2026-04-29 (data corrente run) la logica calcola che il missed run è **fuori** dalla grace window, quindi ritorna None.
- `Expected 'send_task' to have been called once. Called 0 times.` — il beat scheduler non ha emesso il task atteso. Stessa root cause possibile: il test assume condizioni di tempo che ora non valgono.

#### Hypothesis principale

I test usano `datetime.now()` o `datetime.utcnow()` non monkeypatchati e fixture che hardcodano `datetime(2026, 4, 16, ...)`. La distanza tra "now" (2026-04-29) e la data hardcoded è ~13 giorni, fuori dal grace_hours default (probabilmente 24h o 72h).

#### Patch minima raccomandata

**NON CI fix.** Va investigato come **bug regression dedicato**.

**Strategy**:
1. **Pre-step read-only**: leggere i 4 file di test e identificare i datetime hardcoded.
2. **Fix candidate**: usare `freezegun` o `pytest.fixture` che monkeypatch `datetime.now()` nei test, oppure rendere le date relative a `now` (es. `now() - timedelta(hours=2)`).
3. **Branch dedicato**: `fix/scheduler-time-relative-test-fixtures`.
4. **Effort**: 2-4 ore investigation + 1-2 ore patch.

**Approvazione richiesta**: SÌ, ma con **priorità più bassa** di binario A (non blocca il chiudere il rumore CI).

### C2 — Cluster F7: Assertion isolati (6 failed)

| # | File | Test | Errore | Hypothesis |
| ---:| --- | --- | --- | --- |
| 1 | `tests2/estrai/unit/test_query_executor_result_id_lock.py` | `test_execute_query_propagates_result_id_from_save_blob` | `Expected 'save_result_blob_from_bytes' to have been called once. Called 0 times.` | code/test drift su mock target |
| 2 | `tests2/intermediate/unit/test_full_artifact_contract.py` | `test_full_artifact_writes_parquet_and_manifest_to_runtime_object_storage` | `AssertionError: []` | downstream da F2 (pyarrow missing) → si fixa con A3 |
| 3 | `tests2/intermediate/unit/test_full_artifact_contract.py` | `test_full_artifact_parquet_payload_roundtrip` | `StopIteration` | downstream da F2 → si fixa con A3 |
| 4 | `tests2/performance/test_correctness_op_concat_columns.py` | `test_op_concat_columns_invariant[numeric+date+bool skip=False]` | `TypeError: sequence item 3: expected str instance, float found` | bug reale pandas dtype handling |
| 5 | `tests2/security/unit/test_files_router_security_contract.py` | `test_sanitize_upload_filename_reduces_to_safe_basename` | `assert '..\\nested\\report.csv' == 'report.csv'` | **platform bug**: il test usa Windows-style backslash che su Linux runner non viene parsato |
| 6 | `tests2/shared/test_cloud_backends.py` | `test_factory_unknown_provider_raises` | `Regex pattern did not match` | code/test drift su error message format |

#### Conseguenze del binario A

Dopo PR 7 (A3 pyarrow), test **#2 e #3** dovrebbero passare automaticamente (sono downstream del missing pyarrow). Quindi F7 reale = 4 test (#1, #4, #5, #6).

#### Tipologia

**Bug isolati**, ognuno richiede investigazione singola.

#### Raccomandazione

Trattare DOPO binario A. Possibile branching:
- **PR 9**: `fix/file-router-windows-path-sanitize` per #5 (bug platform-specific Windows path)
- **PR 10**: `fix/cloud-backends-error-message-regex` per #6 (regex message drift)
- **PR 11**: `fix/query-executor-result-id-mock-drift` per #1 (mock target drift)
- **PR 12**: `fix/concat-columns-dtype-handling` per #4 (pandas dtype bug)

**Effort cumulativo**: ~3-4 ore investigation + per-test fix.

**Approvazione richiesta**: SÌ, **dopo binario A chiuso**.

---

## Riepilogo finale 3 binari

### Binario A (quick CI unblock, ~35 min totali)

| PR | Sblocca | Approva? |
| --- | ---:| --- |
| **PR 5** F5 BACKUP_BUCKET | 4 | ⏳ in attesa |
| **PR 6** F4 pyodbc residuo | 3 | ⏳ in attesa |
| **PR 7** F2+F3 pyarrow/openpyxl in `requirements.txt` (gap prod reale) | 13 (+ 2 da F7 downstream) | ⏳ in attesa |

### Binario B (CI taxonomy, ~3h totali)

| PR | Decisione | Approva? |
| --- | --- | --- |
| **PR 8** `chore/ci-tiers-taxonomy` (workflow T1/T2/T3/T4) | strategica, post binario A | ⏳ in attesa |

### Binario C (bug reali, post-A)

| PR | Cluster | Effort |
| --- | --- | ---:|
| **PR 9** F6 scheduler time fixtures | 12 test | 3-6h |
| **PR 10** F7 file router windows path | 1 test | 1h |
| **PR 11** F7 cloud backends regex | 1 test | 30min |
| **PR 12** F7 query executor mock drift | 1 test | 1h |
| **PR 13** F7 concat columns dtype | 1 test | 1h |

### Cosa NON fare (consolidato)

1. **NON** PR aggregata gigante. Le 5+ PR vanno separate per cluster.
2. **NON** maskare gap prod reale (F2/F3) con `importorskip`. Vanno in `requirements.txt`.
3. **NON** installare `pyodbc` in `requirements*.txt` (richiede ODBC system libs apt). Pattern `importorskip` per F4 è il giusto.
4. **NON** trattare F6 scheduler come "tanto è scheduler". 12 contract test su catchup/missed-run sono bug reali.
5. **NON** patchare F7 prima di binario A: test #2, #3 sono downstream e si auto-fixano.
6. **NON** aprire binario B prima di binario A. Aggiungere markers + workflow files con CI ancora rosso = noise sopra noise.
7. **NON** test runtime locale prima di approvazione e applicazione patch.

---

## Decisioni esplicite richieste

### Binario A — quick unblock

1. ✅ Approvazione **PR 5** (BACKUP_BUCKET env, ~10min)?
2. ✅ Approvazione **PR 6** (pyodbc importorskip services, ~10min)?
3. ✅ Approvazione **PR 7** (pyarrow + openpyxl in `requirements.txt`, ~15min) — questo è gap prod reale, non solo CI?
4. **Ordine**: A1 → A2 → A3 sequenziale (più sicuro per attribution CI), oppure A1+A2+A3 in parallelo (più veloce)?

### Binario B — taxonomy

5. Approvazione apertura **PR 8** (`chore/ci-tiers-taxonomy`) post binario A?
6. Pre-step audit marker `tests2/` autorizzato (read-only)?

### Binario C — bug reali

7. F6 scheduler: priorità A → B → C, oppure parallel a B?
8. F7 sequenza: rinviata a fine A+B?

Resto in attesa. Niente file modificato. Niente test runtime. Niente deploy. Niente patch automatica.

---

*End of CI Red 3-Binary Plan.*
