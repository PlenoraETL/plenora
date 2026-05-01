# CI Red Baseline — Analysis & Patch Plan

Status: **analysis only — no code change, no test rerun, no deploy.**

Scope batch separato post Step 0 batch 1: capire perché `main` è rosso
da almeno 10 push consecutivi e proporre patch minima per riportare
la baseline verde, o classificare esplicitamente i failure se non
risolvibili nel batch.

Input letti (read-only):
- `.github/workflows/ci.yml`
- `backend/tests2/conftest.py`
- `backend/tests2/pytest.ini`
- `backend/app/db/pg_connection.py:111–113`
- `backend/.env.example` (riga 96)
- `frontend/tests/setup.js`
- `frontend/vite.config.js`
- `frontend/tests/pages/ProjectList.contract.test.jsx`
- `frontend/src/pages/ProjectList.jsx:851`
- ultimi 10 run CI su main + run su PR #1 (`gh run view --log-failed`)

---

## 1. Failure #1 — Backend pytest: `DATABASE_URL non configurato`

### 1.1 Sintomo

47 test file falliscono in fase di **collection** (non di execution):

```
ERROR tests2/connections/test_delete_connection_409_shape.py - RuntimeError:
  DATABASE_URL non configurato. Imposta la variabile d'ambiente.
[... 46 altri file con stesso messaggio ...]
!!!!!!!!!!!!!!!!!!! Interrupted: 47 errors during collection !!!!!!!!!!!!!!!!!!!
2 warnings, 47 errors in 8.00s
##[error]Process completed with exit code 2.
```

Pytest si ferma prima di eseguire qualunque test.

### 1.2 Causa root

`backend/app/db/pg_connection.py:109–113` legge `DATABASE_URL` come
**module-level**:

```python
_DATABASE_URL = os.getenv("DATABASE_URL")
if not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL non configurato. Imposta la variabile d'ambiente.")
```

Quando un test file importa codice applicativo che a sua volta importa
`pg_connection`, l'eccezione viene sollevata a **import time**, prima
che pytest possa istanziare i fixture o applicare lo stub di `conftest.py`.

`backend/tests2/conftest.py` ha 2 stub difensivi:

1. **Riga 57–90**: stub `init_db`/`init_all_dbs`/`apply_pending_migrations`
   a no-op se `URBY_TEST_REAL_DB_INIT != "1"`. **Ma** questi stub
   richiedono che `pg_connection` sia *già importato* — nel codice si
   fa `import app.db.pg_connection as _pg_conn` (riga 60), che a sua
   volta esegue `_DATABASE_URL = os.getenv(...)` ed eventualmente
   solleva.

2. **Riga 247–282**: fixture autouse che monkeypatcha `pg_connection.execute`
   a no-op. Anche questa richiede `import app.db.pg_connection`.

Il workflow CI (`.github/workflows/ci.yml:37–44`) imposta solo:
```yaml
env:
  STORAGE_POLICY: db
  CELERY_BROKER_URL: redis://localhost:6379/0
  CELERY_RESULT_BACKEND: redis://localhost:6379/0
```

**Manca completamente `DATABASE_URL`**. Quindi al momento della prima
import che tira `pg_connection`, il fail è inevitabile e copre tutti
i 47 file dipendenti.

### 1.3 Classificazione

- **Tipologia**: pre-esistente + infrastrutturale (CI config gap).
- **Dipendenza dalla PR #1**: zero (il diff non tocca nessuno di:
  workflow, conftest, pg_connection, env example).
- **Riproducibile**: sì, deterministico. Failure bit-identico fra
  run di main e run di PR #1 (verificato in pre-merge investigation).

### 1.4 Patch minima proposta

Aggiungere `DATABASE_URL` dummy all'env del job `Backend pytest` in
`.github/workflows/ci.yml:37–44`:

```yaml
env:
  STORAGE_POLICY: db
  DATABASE_URL: postgresql://nobody:nopass@127.0.0.1:1/test_unit_no_real_db
  CELERY_BROKER_URL: redis://localhost:6379/0
  CELERY_RESULT_BACKEND: redis://localhost:6379/0
```

Razionale:
- **Niente DB reale** richiesto: il valore è una stringa formato URL
  valida ma irraggiungibile (porta 1, host loopback). Il `psycopg2.connect`
  reale non viene mai invocato perché:
  - `conftest.py` stub-pa `init_db` e `init_all_dbs` a no-op (riga 73–85).
  - fixture autouse stub-pa `pg_connection.execute` a no-op (riga 278–281).
- **Bypassa la check module-level** di `pg_connection.py:111–113`.
- **Niente cambio codice applicativo**: solo CI workflow.
- **Niente fix conftest**: il pattern stub esistente è corretto e già
  testato in batch B closeout (commenti riga 26–55 spiegano la storia).

Effort: ~10 min edit + push + verify CI verde.

### 1.5 Alternative considerate

| Alternativa | Pro | Contro |
| --- | --- | --- |
| **a. Lazy `_DATABASE_URL` in pg_connection.py** | Fix architetturale, niente più crash a import-time | Tocca codice applicativo, scope più ampio, rischio regressioni runtime, fuori da batch CI red |
| **b. PG container service in CI con DB reale** | Test integration veri funzionano | Tempi CI +30s, complessità maggiore, scope batch diverso (CI matrix integration tier) |
| **c. setup.cfg / pytest.ini env**: `env = DATABASE_URL=postgres://...` | Workflow invariato | Richiede plugin `pytest-env`, non in `requirements-dev.txt`, scope cambio dependency |
| **d. Lasciare rosso + classificare** | Zero cambio | Perde regression detection, `main` resta ufficialmente rotto |

**Raccomandazione**: opzione **patch minima** (1.4). Evita di toccare
codice applicativo e di introdurre dipendenze nuove. Allineata col
pattern degli stub conftest esistenti.

---

## 2. Failure #2 — Frontend vitest: `ProjectList.contract` querySelectorAll

### 2.1 Sintomo

```
FAIL tests/pages/ProjectList.contract.test.jsx > ProjectList >
  creates an estrai project with the selected connection
TypeError: Cannot read properties of undefined (reading 'querySelectorAll')
Test Files  1 failed | 48 passed (49)
     Tests  1 failed | 273 passed (274)
```

1 test su 274 fallisce — ratio 0.36% — ma sufficiente a far esitare
job vitest in fail.

### 2.2 Causa root

Il test (riga 96–115 di `tests/pages/ProjectList.contract.test.jsx`)
fa:

```javascript
mockDashboardData({
  connections: [{ id: 'conn-1', name: 'Main DB', database: 'urby' }],
});
apiMock.createProject.mockResolvedValueOnce({ metadata: { id: 'proj-1' } });

render(<ProjectList />);

await screen.findByRole('button', { name: /nuovo progetto/i });
await userEvent.click(screen.getByRole('button', { name: /nuovo progetto/i }));

await userEvent.clear(screen.getByPlaceholderText(/catasto campania q2/i));
await userEvent.type(screen.getByPlaceholderText(/catasto campania q2/i), 'Orders ETL');
await userEvent.selectOptions(screen.getAllByRole('combobox')[1], 'conn-1');   // <-- LINE 109
await userEvent.click(screen.getByRole('button', { name: /crea progetto/i }));
```

L'errore arriva a riga **109**: `screen.getAllByRole('combobox')[1]`.
`getAllByRole` ritorna un array; `[1]` accede al **secondo** combobox.
Se nel modal "Nuovo Progetto" il numero di combobox renderizzati è
**< 2**, `[1]` è `undefined`. `userEvent.selectOptions(undefined, ...)`
chiama `.querySelectorAll` su `undefined` → crash.

Possibili cause sottostanti (richiede ispezione del DOM renderato per
confermare; non eseguito qui per attenersi al "no test runtime"):

1. **Drift modal struttura**: il modal "Nuovo Progetto" oggi renderizza
   solo 1 combobox (es. tipo progetto è radio invece di select; oppure
   la connessione è un pulsante invece di select).
2. **Modal non ancora montato**: timing race — il `findByRole` aspetta
   il bottone "nuovo progetto", click lo apre, ma il modal interno
   carica le connessioni async (la chiamata `mockDashboardData` setta
   `apiMock.listConnections.mockResolvedValue(...)` che è async). Il
   `screen.getAllByRole('combobox')` cattura solo il/i select già
   visibili, non quello che apparirà dopo il microtask.
3. **Render condizionale**: il modal mostra il select connessione solo
   quando `newProjectType === 'estrai'`. Se il default è `estrai` (lo
   è — `useState('estrai')` in `ProjectList.jsx`), dovrebbe esserci.
   Ma se React 18 strict mode fa double-render in test env, il primo
   render senza connessioni potrebbe lasciare il select non popolato.

Il pattern del codice in `ProjectList.jsx:493+` (modal) usa
`<select>` per `newProjectType` (sempre 1) e un `<select>` per
`newProjectConnId` solo nel ramo `estrai` (1 ulteriore = 2 totali
quando type=estrai).

Hp più probabile: **timing race async**. Il test non aspetta che le
opzioni di connection siano caricate dentro il `<select>` prima di
chiamare `selectOptions`. Ratio 273/274 passing suggerisce che il
problema è specifico a questo test, non sistemico.

### 2.3 Classificazione

- **Tipologia**: pre-esistente + bug determistico (non flaky — fallisce
  identico fra run).
- **Dipendenza dalla PR #1**: zero.
- **Riproducibile**: sì.

### 2.4 Patch minima proposta

Modificare il test per:

1. **Aspettare la presenza del select connessione** prima di chiamarlo,
   con `findByRole`/`findByLabelText` o equivalente.
2. **Targetare il combobox per name/label** invece che per indice
   array, eliminando la fragilità di `[1]`.

Outline (da dettagliare al momento del commit, **non in questo plan**):

- Cambiare `screen.getAllByRole('combobox')[1]` con
  `await screen.findByRole('combobox', { name: /connessione/i })` o
  similare (richiede label sul `<select>` connessione).
- Verificare che il `<select>` connessione abbia `aria-label` /
  `<label>` associato in `ProjectList.jsx` modal — se manca, aggiungerlo.
- Se serve, aggiungere `await waitFor(() => expect(screen.getByRole('option', { name: 'Main DB' })).toBeInTheDocument())` prima del select.

Effort: ~30–60 min (dipende da quanto del modal va labellato).
Tocca:
- `frontend/tests/pages/ProjectList.contract.test.jsx` (test fix)
- **eventualmente** `frontend/src/pages/ProjectList.jsx` (a11y label
  per `<select>` connessione, se manca).

### 2.5 Alternative considerate

| Alternativa | Pro | Contro |
| --- | --- | --- |
| **a. Skip del test temporaneamente** (`it.skip`) con TODO link | CI verde subito, scope minimo | Perde copertura "create estrai project" — il flusso più importante della pagina. Debt esplicito. |
| **b. Patch test + label form** (1.4) | CI verde + a11y migliorato | Effort 30–60 min |
| **c. Refactor modal struttura** | Test più stabile in futuro | Fuori scope batch CI red |

**Raccomandazione**: opzione **b** (patch test + eventuale label).
Effort piccolo, valore aggiunto su a11y (gap noto da Phase 0.5g §12.5).

---

## 3. Sequenza patch consigliata

### 3.1 Ordine di applicazione

1. **Patch backend**: aggiungi `DATABASE_URL` dummy a
   `.github/workflows/ci.yml`. Effort ~10 min. Sblocca 47 test file.
2. **Patch frontend**: fix `ProjectList.contract` test + eventuale
   label. Effort 30–60 min. Sblocca l'ultimo test failing.

Le 2 patch sono **indipendenti** (toccano stack diversi). Possono
essere applicate in 2 PR separate o 1 PR singola — preferenza per
**2 PR separate**: review più semplice, rollback granulare.

### 3.2 Branch + PR strategy

| Patch | Branch proposto | Scope file |
| --- | --- | --- |
| Backend | `chore/ci-fix-backend-database-url` | `.github/workflows/ci.yml` (1 file, +1 riga env) |
| Frontend | `chore/ci-fix-frontend-projectlist-test` | `frontend/tests/pages/ProjectList.contract.test.jsx` + eventuale `ProjectList.jsx` |

### 3.3 Verifiche post-merge

Dopo entrambe le PR mergeate:
- Trigger CI su main → entrambi i job verdi.
- Verificare che PR future ereditino la baseline verde (ratio fail/total
  cala a 0).
- Aggiornare `BUG.md` o equivalente changelog con: "CI red baseline
  fixed: backend DATABASE_URL CI env + frontend ProjectList test
  stabilization".

---

## 4. Cosa NON fare in questo batch

- ❌ Refactor `pg_connection.py` per lazy DATABASE_URL (alternativa 1.5.a, più ampia)
- ❌ Aggiungere PG container al workflow (alternativa 1.5.b, scope diverso)
- ❌ Riscrivere modal "Nuovo Progetto" (alternativa 2.5.c, scope diverso)
- ❌ Skip test in massa per nascondere altri failure latenti
- ❌ Touch a `deploy.sh` o `deploy/profiles/*` (Step 0 chiuso, non si tocca)
- ❌ Test runtime locali del backend pytest o frontend vitest (vincolo)
- ❌ Modifiche al codice prima di approvazione patch plan

---

## 5. Stato batch

**Status: analisi + patch plan documentati. Niente codice modificato.**

Output:
- Cause root identificate per entrambi i failure (riga e file precisi)
- Patch minima per ognuno (1.4 + 2.4)
- Alternative considerate ed escluse (1.5 + 2.5)
- Sequenza di applicazione (§3)
- Out-of-scope esplicito (§4)

Decisione esplicita richiesta:
1. Approvazione patch backend (CI env DATABASE_URL dummy) → procedere a
   branch `chore/ci-fix-backend-database-url`?
2. Approvazione patch frontend (test fix + eventuale a11y label) →
   procedere a branch `chore/ci-fix-frontend-projectlist-test`?
3. Preferenza: 2 PR separate vs 1 PR aggregata?

Resto in attesa. Niente file modificato, niente test runtime, niente
deploy.

---

*End of CI Red Baseline Analysis & Patch Plan.*
