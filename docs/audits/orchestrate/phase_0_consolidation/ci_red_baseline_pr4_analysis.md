# CI Red Baseline — PR 4 — Backend Optional DB Drivers — Analysis & Patch Plan

Status: **analysis only — no patch applied, no test runtime, no deploy.**

Scope batch separato post PR #2/#3:
- 26 `ModuleNotFoundError` ancora rossi su Backend pytest dopo merge di PR #2 (`DATABASE_URL`).
- I test integration MySQL/MariaDB/MSSQL/Oracle non possono fare collection perché i driver DB optional non sono installati nel runner CI.
- Comportamento atteso da workflow comment (`.github/workflows/ci.yml:5–7`): "i test che richiedono DB live si auto-skip via `_<dialect>_reachable()` guard interno". **Il guard non scatta** perché l'`ImportError` esplode prima.

Input letti (read-only):
- `gh run view --log-failed 25091876136 --job 73519534164` (26 errors raw)
- `backend/tests2/integration/harness/{mariadb,mysql,oracle,sqlserver}.py` (module-level imports)
- `backend/tests2/integration/test_mysql_carica_stage_swap.py` (pattern guard tipico)
- `backend/tests2/integration/conftest.py` (fixture comuni, no skip helpers)
- `backend/requirements.txt`, `backend/requirements-dev.txt` (no driver presenti)

---

## 1. Enumerazione completa dei 26 errors

| # | Test file (in `backend/tests2/integration/`) | Modulo mancante | Import line origin |
| ---:| --- | --- | --- |
| 1 | `test_mariadb_connection_and_estrai_preview.py` | `pymysql` | `harness.mariadb:8 (import pymysql)` |
| 2 | `test_mariadb_execute_download_roundtrip.py` | `pymysql` | `harness.mariadb:8` |
| 3 | `test_mssql_carica_stage_swap.py` | `pyodbc` | `harness.sqlserver:8 (import pyodbc)` |
| 4 | `test_mssql_carica_stage_swap_schema.py` | `pyodbc` | `harness.sqlserver:8` |
| 5 | `test_mysql_carica_existing_table_failure.py` | `pymysql` | `harness.mysql:8 (import pymysql)` |
| 6 | `test_mysql_carica_roundtrip.py` | `pymysql` | `harness.mysql:8` |
| 7 | `test_mysql_carica_stage_swap.py` | `pymysql` | `harness.mysql:8` |
| 8 | `test_mysql_connection_and_estrai_preview.py` | `pymysql` | `harness.mysql:8` |
| 9 | `test_mysql_estrai_failure_contract.py` | `pymysql` | `test_mysql_connection_and_estrai_preview.py:7 (re-import test)` → harness.mysql |
| 10 | `test_mysql_execute_download_roundtrip.py` | `pymysql` | `harness.mysql:8` |
| 11 | `test_mysql_pipeline_carica_chain.py` | `pymysql` | `harness.mysql:8` |
| 12 | `test_oracle_carica_redef_schema.py` | `oracledb` | `harness.oracle:9 (import oracledb)` |
| 13 | `test_oracle_carica_roundtrip.py` | `oracledb` | `harness.oracle:9` |
| 14 | `test_oracle_carica_stage_swap.py` | `oracledb` | `harness.oracle:9` |
| 15 | `test_oracle_connection_and_estrai_preview.py` | `oracledb` | `harness.oracle:9` |
| 16 | `test_oracle_execute_download_roundtrip.py` | `oracledb` | `harness.oracle:9` |
| 17 | `test_project_source_cross_tenant_contract.py` | `pymysql` | `harness.mysql:8` |
| 18 | `test_project_source_latest_artifact_roundtrip.py` | `pymysql` | `harness.mysql:8` |
| 19 | `test_project_source_missing_latest_contract.py` | `pymysql` | `harness.mysql:8` |
| 20 | `test_replace_atomic.py` | `oracledb` | `harness.oracle:9` |
| 21 | `test_rollback_regression_write_modes.py` | `oracledb` | `harness.oracle:9` |
| 22 | `test_scheduler_failure_contract.py` | `pymysql` | `test_mysql_connection_and_estrai_preview.py:7 (re-import test)` → harness.mysql |
| 23 | `test_sqlserver_carica_roundtrip.py` | `pyodbc` | `harness.sqlserver:8` |
| 24 | `test_sqlserver_connection_and_estrai_preview.py` | `pyodbc` | `harness.sqlserver:8` |
| 25 | `test_sqlserver_execute_download_roundtrip.py` | `pyodbc` | `harness.sqlserver:8` |
| 26 | `test_sqlserver_pipeline_carica_chain.py` | `pyodbc` | `harness.sqlserver:8` |

**Totale per modulo mancante**:
| Modulo | Count | Test file count |
| --- | ---:| ---:|
| `pymysql` (MySQL/MariaDB) | 14 | 14 |
| `pyodbc` (MSSQL) | 6 | 6 |
| `oracledb` (Oracle) | 6 | 6 |

### 1.1 Pattern import a module-level

L'import dei driver DB **non avviene nei test** ma nei **moduli harness** (file in `backend/tests2/integration/harness/`):

| Harness file | Import a module-level | Riga |
| --- | --- | ---:|
| `mariadb.py` | `import pymysql` | 8 |
| `mysql.py` | `import pymysql` | 8 |
| `oracle.py` | `import oracledb` | 9 |
| `sqlserver.py` | `import pyodbc` | 8 |

I test integration importano dai harness con statement tipo:
```python
from tests2.integration.harness.mysql import seeded_mysql_table, mysql_conn, ...
```

Quando pytest collection import il test → import dell'harness → import del driver DB → `ModuleNotFoundError`.

### 1.2 Guard `_<dialect>_reachable()` esistenti

Pattern tipico (es. `test_mysql_carica_stage_swap.py:40–49`):

```python
pytestmark = [pytest.mark.integration, pytest.mark.carica, pytest.mark.contract]

def _mysql_reachable() -> bool:
    try:
        with mysql_conn():
            return True
    except Exception:
        return False

pytestmark.append(
    pytest.mark.skipif(not _mysql_reachable(), reason="MySQL non raggiungibile")
)
```

Il guard **funziona** quando `pymysql` è installato (la `try`/`except Exception` cattura il connection refused se il container non gira). Ma `_mysql_reachable()` chiama `mysql_conn()` che è importato dall'harness, che a sua volta importa `pymysql` → l'`ImportError` esplode **prima** di arrivare al guard.

Verificato:
- `_mysql_reachable` definito in 4 test (mysql_carica_stage_swap, oracle_carica_redef_schema, oracle_carica_roundtrip, oracle_carica_stage_swap, ecc.).
- `_mssql_reachable` definito in 2 test.
- `_oracle_reachable` definito in 4 test.
- Nessuna `pytest.importorskip("pymysql")` esistente in nessun file (verificato via `grep -rnE "pytest.importorskip" backend/tests2/integration/` → 0 match).
- `requirements.txt` e `requirements-dev.txt` non contengono `pymysql`, `pyodbc`, `oracledb` (verificato via `grep -nE "pymysql|pyodbc|oracledb" backend/requirements.txt backend/requirements-dev.txt` → 0 match).

---

## 2. Tabella test → modulo mancante → import line → guard

| Test file | Modulo mancante | Harness origin (line) | Guard `_reachable` esistente nel test? |
| --- | --- | --- | :---:|
| `test_mariadb_connection_and_estrai_preview.py` | pymysql | `harness/mariadb.py:8` | non verificato (probabile mancante o inefficace) |
| `test_mariadb_execute_download_roundtrip.py` | pymysql | `harness/mariadb.py:8` | idem |
| `test_mssql_carica_stage_swap.py` | pyodbc | `harness/sqlserver.py:8` | ✅ `_mssql_reachable` (line 36) |
| `test_mssql_carica_stage_swap_schema.py` | pyodbc | `harness/sqlserver.py:8` | ✅ `_mssql_reachable` (line 48) |
| `test_mysql_carica_existing_table_failure.py` | pymysql | `harness/mysql.py:8` | non verificato |
| `test_mysql_carica_roundtrip.py` | pymysql | `harness/mysql.py:8` | non verificato |
| `test_mysql_carica_stage_swap.py` | pymysql | `harness/mysql.py:8` | ✅ `_mysql_reachable` (line 40) |
| `test_mysql_connection_and_estrai_preview.py` | pymysql | `harness/mysql.py:8` | non verificato |
| `test_mysql_estrai_failure_contract.py` | pymysql | re-import via test_mysql_conn... | ereditato |
| `test_mysql_execute_download_roundtrip.py` | pymysql | `harness/mysql.py:8` | non verificato |
| `test_mysql_pipeline_carica_chain.py` | pymysql | `harness/mysql.py:8` | non verificato |
| `test_oracle_carica_redef_schema.py` | oracledb | `harness/oracle.py:9` | ✅ `_oracle_reachable` (line 56) |
| `test_oracle_carica_roundtrip.py` | oracledb | `harness/oracle.py:9` | ✅ `_oracle_reachable` (line 48) |
| `test_oracle_carica_stage_swap.py` | oracledb | `harness/oracle.py:9` | ✅ `_oracle_reachable` (line 48) |
| `test_oracle_connection_and_estrai_preview.py` | oracledb | `harness/oracle.py:9` | non verificato |
| `test_oracle_execute_download_roundtrip.py` | oracledb | `harness/oracle.py:9` | non verificato |
| `test_project_source_cross_tenant_contract.py` | pymysql | `harness/mysql.py:8` | non verificato |
| `test_project_source_latest_artifact_roundtrip.py` | pymysql | `harness/mysql.py:8` | non verificato |
| `test_project_source_missing_latest_contract.py` | pymysql | `harness/mysql.py:8` | non verificato |
| `test_replace_atomic.py` | oracledb | `harness/oracle.py:9` | ✅ `_oracle_reachable` (line 42) |
| `test_rollback_regression_write_modes.py` | oracledb | `harness/oracle.py:9` | non verificato |
| `test_scheduler_failure_contract.py` | pymysql | re-import via test_mysql_conn... | ereditato |
| `test_sqlserver_carica_roundtrip.py` | pyodbc | `harness/sqlserver.py:8` | non verificato |
| `test_sqlserver_connection_and_estrai_preview.py` | pyodbc | `harness/sqlserver.py:8` | non verificato |
| `test_sqlserver_execute_download_roundtrip.py` | pyodbc | `harness/sqlserver.py:8` | non verificato |
| `test_sqlserver_pipeline_carica_chain.py` | pyodbc | `harness/sqlserver.py:8` | non verificato |

**Osservazione chiave**: i guard `_reachable()` esistono in molti test (verificati 8 esplicitamente), ma sono **inefficaci** perché l'`ImportError` da harness esplode in collection prima che il guard scatti.

---

## 3. Confronto pattern di soluzione

L'utente ha proposto 4 pattern. Valutati uno per uno:

### A. Aggiungere driver optional a `requirements-dev.txt`

```
pymysql
pyodbc
oracledb
```

| Pro | Contro |
| --- | --- |
| Soluzione più semplice (1 file, 3 righe) | **Contraddice intent dichiarato del workflow** (`.github/workflows/ci.yml:5–7`): "subset locale-friendly", "test integration si auto-skip" |
| Niente cambio test/harness | `pyodbc` richiede librerie ODBC di sistema (libreria C via apt: `unixodbc-dev` + `msodbcsql18`). Senza queste, install fallisce su runner Ubuntu fresco |
| | `oracledb` ~5–10 MB di dipendenze + binari Oracle Instant Client per modalità thick (modalità thin OK ma con limitazioni) |
| | Aumenta tempo install dipendenze in CI |
| | Installa driver mai usati (CI non ha container DB MySQL/MSSQL/Oracle running, i guard `_reachable` skipperanno comunque a runtime) |

**Verdetto**: scartato. Installa peso inutile per soddisfare l'import; rompe l'intent originale del workflow ("subset locale-friendly").

### B. Spostare import dentro test/helper dopo `pytest.importorskip()`

Modificare ogni `harness/{mariadb,mysql,oracle,sqlserver}.py` per spostare le funzioni in factory lazy o convertire i moduli in pacchetti più ristrutturati.

| Pro | Contro |
| --- | --- |
| Risolve la causa root | Refactor pesante: ~4 file harness modificati + tutte le `from harness.x import ...` nei 26 test rotte |
| | Scope ampio, rischio regressione |
| | Possibile race tra import del modulo test e prima chiamata harness function |

**Verdetto**: scartato. Effort sproporzionato per il valore.

### C. Aggiungere `pytest.importorskip("driver")` a module-level nei test integration

Pattern canonico Python: in cima a ogni file harness aggiungere:

```python
# harness/mysql.py
"""Helper MySQL reali per gli integration test."""
from __future__ import annotations

import pytest

# Skip pulito a collection time se pymysql non e' installato (CI runner
# senza driver DB). I test integration che dipendono da questo harness
# vengono saltati automaticamente, non fallisce piu' la collection.
pytest.importorskip("pymysql")

# (resto degli import e funzioni invariati)
import pymysql
from contextlib import contextmanager
...
```

| Pro | Contro |
| --- | --- |
| **Allineato con intent workflow** (auto-skip pulito) | Aggiunge `import pytest` nei 4 file harness (era già presente in `sqlserver.py:9`) |
| 4 file modificati, ~6 righe per file (incluso commento) | Cambio lieve di ordine import |
| **Niente nuove dipendenze** installate in CI | |
| Effetto: i 26 test passano da `errors during collection` a `skipped` (cleano `s`/SKIPPED) | |
| Pattern usato spesso in repo Python (es. SQLAlchemy, Pandas, ecc.) | |
| Reversibile a costo zero (1 commit revert) | |
| Locale dev con driver installati: comportamento invariato | |

**Verdetto**: **raccomandato**. Effort minimo, intent allineato.

### D. Escludere integration dal CI unit workflow

Es. modificare `.github/workflows/ci.yml`:
```yaml
run: pytest tests2/ --ignore=tests2/integration -c tests2/pytest.ini
```

| Pro | Contro |
| --- | --- |
| 1 riga modificata, niente cambio test | **Perde copertura**: anche i pochi integration test che potrebbero girare in CI (es. test che usano solo Redis fakeredis o PG via service container) vengono saltati |
| Niente cambio test/harness | Conflitto con commento workflow `:5–7`: "i test che richiedono DB live si auto-skip via guard interno" — l'intent era avere i test in CI che si auto-skippano, non escluderli a livello workflow |
| | Riduce visibilità su test che potrebbero essere fixati in futuro |

**Verdetto**: scartato come strategia primaria, ma può servire come **fallback** se opzione C scopre edge case non gestiti.

---

## 4. Patch minima raccomandata (opzione C)

### 4.1 File da modificare (4)

| File | Modifica | Effetto |
| --- | --- | --- |
| `backend/tests2/integration/harness/mariadb.py` | `+ pytest.importorskip("pymysql")` prima di `import pymysql` | mariadb test skip se pymysql mancante |
| `backend/tests2/integration/harness/mysql.py` | `+ pytest.importorskip("pymysql")` prima di `import pymysql` | mysql test skip se pymysql mancante |
| `backend/tests2/integration/harness/oracle.py` | `+ pytest.importorskip("oracledb")` prima di `import oracledb` | oracle test skip se oracledb mancante |
| `backend/tests2/integration/harness/sqlserver.py` | `+ pytest.importorskip("pyodbc")` prima di `import pyodbc` | sqlserver test skip se pyodbc mancante |

### 4.2 Outline patch per file (esempio mysql.py)

```python
"""Helper MySQL reali per gli integration test."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest

# Skip pulito a collection time se pymysql non e' installato (es. CI runner
# senza driver DB). I test integration che dipendono da questo harness
# vengono saltati con messaggio esplicito invece di rompere la collection
# con ModuleNotFoundError. Allineato con intent del workflow .github/
# workflows/ci.yml:5-7 ("test integration si auto-skip via guard interno").
pytest.importorskip("pymysql")

import pymysql

from tests2.integration.harness.db import make_it_id
...
```

Stessa logica per gli altri 3 file. `harness/sqlserver.py` ha già `import pytest` (riga 9) → modifica ancora più piccola.

### 4.3 Diff sommario stimato

| File | LOC delta |
| --- | ---:|
| `harness/mariadb.py` | +7 (import pytest + commento + importorskip) |
| `harness/mysql.py` | +7 (idem) |
| `harness/oracle.py` | +7 (idem) |
| `harness/sqlserver.py` | +6 (commento + importorskip; pytest già importato) |
| **Totale** | **+27 / 0** |

Effort dev: ~10 min. Effort review + verifica CI: ~10 min. **~20 min totali.**

### 4.4 Comportamento atteso post-patch

| Caso | Pre-patch | Post-patch |
| --- | --- | --- |
| CI Ubuntu runner senza driver | 26 errors during collection, exit 2 | 26 SKIPPED, exit 0 |
| Dev locale con driver + container DB running | Test passano | Test passano (invariato) |
| Dev locale con driver, no container DB | `_reachable()` skipa, test marked SKIPPED | Idem (invariato) |
| Dev locale senza driver | 26 errors during collection | 26 SKIPPED |

### 4.5 Verifica diff piano

Anti-regression: `grep -rnE "import (pymysql|pyodbc|oracledb)" backend/tests2/` deve mostrare solo i 4 file harness post-patch (i moduli driver vengono tirati solo da harness, mai direttamente dai test).

---

## 5. Out-of-scope per questo batch

- ❌ Aggiungere driver a `requirements.txt` o `requirements-dev.txt` (alternativa A, scartata)
- ❌ Refactor harness in lazy factory (alternativa B, scartata)
- ❌ Modifica `.github/workflows/ci.yml` per escludere integration (alternativa D, scartata salvo fallback)
- ❌ Touch a deploy.sh, profiles, backend app code
- ❌ Test runtime locale (vincolo)
- ❌ Aggiunta guard `_reachable()` ai test che ne sono privi (debt separato, non blocca CI verde)
- ❌ Refactor test che fanno re-import di altri test (`test_mysql_estrai_failure_contract.py:7` importa da `test_mysql_connection_and_estrai_preview.py`) — pattern fragile ma non blocca CI

---

## 6. Decisione richiesta

1. **Approvazione patch C** (4 file harness con `pytest.importorskip`)?
2. **Branch proposto**: `chore/ci-fix-backend-optional-db-drivers`?
3. **Scope**: solo 4 file in `backend/tests2/integration/harness/`. Niente touch a `requirements*.txt`, niente touch a workflow, niente touch a test files.
4. **Test plan post-merge**: CI run su main deve mostrare:
   - Frontend vitest: success (invariato)
   - Backend pytest: success (26 errors → 26 skipped, exit 0)
   - Esito `pytest -q` mostra messaggio `s` per ognuno dei test integration skip-ati con reason `could not import 'pymysql' (or pyodbc / oracledb)`.

Resto in attesa. Niente file modificato. Niente test runtime. Niente deploy. Niente patch automatica.

---

*End of CI Red Baseline — PR 4 — Backend Optional DB Drivers Analysis & Patch Plan.*
