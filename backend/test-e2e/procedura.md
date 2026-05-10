# Plenora E2E — procedura operativa

Come passare dai ~340 test catalogati in `test-e2e.md` a una test
suite eseguibile e in CI. 3 fasi distinte con ruoli e tempi diversi.

## TL;DR

| Fase | Cosa | Chi guida | Test coperti | Tempo stimato |
|---|---|---|---|---|
| **1 — Interattivo** | smoke critici, scrittura+verifica visiva | utente + AI | ~15-20 test | ~3 ore |
| **2 — Batch** | scrittura massiva, verifica solo via asserzioni | AI | ~320 test | ~10-15 ore AI + ~1 ora utente review |
| **3 — Regression CI** | esecuzione automatica su ogni PR | nessuno | tutti | continuo |

---

## Fase 1 — Interattivo (smoke critici)

**Obiettivo**: validare che il **runner pytest funzioni** e che le
assunzioni di setup (URL, payload, schema) siano corrette. La prima
volta queste cose esplodono, ed è meglio scoprirle 1 alla volta che 200.

### Test scelti (~20)

| Famiglia | Test | Perché |
|---|---|---|
| Setup | `PREP-001..023` (23 test) | tutti — sono il fondamento di ogni altro test |
| Estrai | `EXT-001` (postgres), `EXT-021` (REST api_key), `EXT-028` (FTP), `EXT-030` (S3) | 1 happy path per categoria sorgente |
| Manipola | `MAN-002` (join 3 tabelle) | trasformazione tipica |
| Carica | `LOAD-001` (DB target), `LOAD-005` (REST sink batch) | scrittura DB + scrittura REST |
| Auth | `AUTH-001` (cleanup cascade) | teardown affidabile |
| Sicurezza | `SEC-001` (SSRF) | gate critico |

### Loop per ogni test

```
1. AI scrive il pytest (dipende da fixture esistenti o ne aggiunge)
2. Utente lancia:
     pytest -k <test_id_lower> -v -s --no-cov
3. AI legge l'output del runner (passed/failed + assertions)
4. Utente apre il frontend plenora e verifica visivamente:
   - se un canvas è stato creato, lo vede in lista?
   - se un run è completato, il preview mostra i dati attesi?
   - se un cleanup è scattato, le risorse sono sparite?
5. Utente dice OK / NOK
   - OK   → AI marca DONE nel `test-e2e.md`, commit `Add <test_id>`
   - NOK  → AI capisce cosa è sbagliato (asserzione? URL? payload?), aggiusta, ritorna a (2)
```

### Output Fase 1

- Runner pytest validato (fixture chain funziona)
- 20 test in stato `DONE` nel catalogo
- ~20 commit individuali (1 per test) o ~5 commit raggruppati (1 per famiglia, scelta utente)
- Lista di **assunzioni corrette** per la Fase 2 (es. "il login è su `/api/auth/login`", "il response del run ha campo `run_id`, non `id`")

---

## Fase 2 — Batch (scrittura massiva)

**Obiettivo**: trasformare i ~320 test rimanenti in pytest senza
verifica visiva per ogni singolo test.

### Pre-requisiti

- Fase 1 chiusa con i 20 smoke verdi.
- Le assunzioni della Fase 1 sono in `conftest.py` come fixture.
- L'utente si fida che il runner sia affidabile.

### Loop per famiglia

```
1. AI scrive 5-10 test della stessa famiglia (es. tutti gli EXT-DB)
2. AI lancia pytest internamente (background) e legge esito
3. Per ogni test:
   - passed → marca DONE, prossimo
   - failed → AI tenta fix da sola (asserzione, payload, fixture)
              Dopo 3 tentativi, sospende il test e chiede all'utente
4. AI commit "Add <famiglia> tests" con i passed
5. Utente review del commit (~5-10 min per commit)
```

### Quanto chiede all'utente

- **Niente verifica visiva** per ogni test.
- **Solo decisioni di scope**: se un test fallisce in modo non ovvio,
  l'AI scrive un report breve all'utente e aspetta una scelta:
  > "PREP-009 list-tables non torna `events_log` ma 8 tabelle. Il seed
  > ha caricato `02_volume.sql`? Posso skippare il test o vuoi
  > investigare?"
- Review del PR/commit quando una famiglia è chiusa.

### Output Fase 2

- ~320 test in stato `DONE` nel catalogo
- ~30 commit (uno per famiglia)
- Suite pytest completa, eseguibile end-to-end con `pytest`
- Eventuali test marcati `@pytest.mark.skip` con motivazione (gap di
  feature plenora, non gap di test)

---

## Fase 3 — Regression CI

**Obiettivo**: eseguire la suite su ogni PR senza intervento umano.

### Setup

- GitHub Actions workflow `.github/workflows/e2e.yml`
- Job `e2e-core` su ogni PR (~5-10 min):
  - Avvia profilo `core` del compose
  - Attende healthcheck
  - Lancia plenora app in container
  - `pytest -m profile_core`
- Job `e2e-full` notturno (~30-60 min):
  - Profilo `full` (incluso Oracle 4 GB)
  - Tutti i test inclusi i `slow`

### Output Fase 3

- Diff visibili su ogni PR: se un test rompe, il PR è red
- Trend nel tempo: numero test verdi sale, regressioni catturate

---

## Convenzioni durante la scrittura

### Naming

- File: `tests/test_<area>_<topic>.py` (es. `test_ext_db.py`, `test_man_join.py`).
- Funzione: `test_<id_lower>_<slug>` (es. `test_prep_001_stack_healthy`).
- Docstring obbligatoria con ID dal catalogo:
  ```python
  def test_prep_004_create_postgres_connection(...) -> None:
      """PREP-004 — Crea connection PostgreSQL nel gruppo di test."""
  ```

### Marker

- `@pytest.mark.profile_<name>` per il profilo docker minimo.
- `@pytest.mark.requires_app` se serve l'app plenora (oltre al docker).
- `@pytest.mark.smoke` per i test Fase 1.
- `@pytest.mark.slow` per test che superano 30s (skipped di default,
  `--run-slow` per attivarli).
- `@pytest.mark.interactive` per test che richiedono umano (skipped di
  default).

### Fixture

- Tutto session-scope dove possibile: lo stack docker viene verificato
  una sola volta per run.
- Teardown idempotente: ogni `DELETE` ignora 404.
- Skip graceful: se una dipendenza manca, `pytest.skip()` con messaggio
  chiaro, mai un fail silenzioso.

### Asserzioni

- **Numeri concreti**: `assert len(rows) == 22`, non "verifica che ci
  siano abbastanza righe".
- **Status code esatti**: `assert r.status_code == 201`, non `r.ok`.
- **Error code mappati**: per i test negative path, asserisci il
  codice errore strutturato (`r.json()["error_code"] == "ssrf_blocked"`).
- **No flakiness**: niente `time.sleep()` arbitrari. Polling con
  timeout esplicito.

### Commit

- 1 commit per famiglia (Fase 2): `Add EXT-001..EXT-009 estrai DB tests`.
- 1 commit per test (Fase 1): `Add PREP-004 create postgres connection`.
- Messaggio sempre descrittivo del **cosa**, non del **come**.

---

## Quando fermarsi

I test e2e hanno **rendimento marginale decrescente**: i primi 50
catturano il 70% dei bug, gli ultimi 50 catturano il 5%. Decidi quando
fermarti in base al budget, non al "fare tutti".

Indicatori che la suite è "abbastanza":
- Tutti i flussi business principali (estrai/manipola/arricchisci/carica)
  passano in CI per giorni senza regressioni.
- Le ultime N feature shipped sono state catturate dalla suite (un
  bug che la suite ha trovato prima del prod).
- Gli sviluppatori hanno fiducia di rilasciare in prod basandosi sulla
  suite.

A quel punto investi in altre forme di test (unit, contract, load),
non in più e2e.

---

## Anti-pattern da evitare

- **Mai** verificare a mano e basta — se non scrivi l'asserzione, la
  prossima regressione passa inosservata.
- **Mai** scrivere test con `@pytest.mark.skip` "per ora": diventano
  permanenti. Meglio non scrivere il test che scriverlo skipped.
- **Mai** asserire output testuale di prosa (es. error message
  italiano) — è instabile. Asserisci `error_code` strutturato.
- **Mai** dipendere dall'ordine di esecuzione dei test (`pytest -p
  no:randomly` deve dare lo stesso risultato di esecuzione casuale).
- **Mai** committare un test che non è mai passato. Solo `DONE`.
