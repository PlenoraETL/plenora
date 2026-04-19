# Backend — Piano di Riduzione Codice

Data audit: 2026-04-14
Autore nota: analisi post `cleanup_analysis_backend_detailed.md` + sessione
refactor (R#1-#5 + Plan C + Fase 1/2/3/4).

## Scopo

Valutare quanto del backend Python e' effettivamente riducibile senza
riaprire refactor architetturali grandi. Risponde alla domanda "si puo'
fare un piano di riduzione sensata delle righe di codice?".

Il documento e' **una stima concreta con numeri**, non un impegno a
eseguire. L'esecuzione dei singoli scaglioni e' decisione separata.

## Stato quantitativo del codebase

Snapshot `2026-04-14` post-sessione (HEAD a `bde8303`).

| Directory | Righe | File |
|---|---|---|
| `app/` | 56,265 | 134 |
| `workers/` | 3,540 | 10 |
| **Totale prod** | **59,805** | **144** |
| `tests2/` (safety net) | ~10,300 | ~105 |

I 20 file piu' grandi pesano ~27k righe (~45% del codebase prod).

### Top 20 file

    3041  app/core/arricchisci/api_connector.py
    3032  app/core/manipola/transforms/base.py
    2315  app/core/features/arcgis_client.py
    1974  app/core/arricchisci/enrichment_service.py
    1824  app/core/manipola/transforms/spatial.py
    1663  app/core/estrai/query_generator.py
    1556  app/db/sql_functions.py
    1512  app/services/file_service.py
    1456  app/services/schema_inference.py
    1288  app/core/imdf/arcgis_uploader.py
    1102  app/core/features/features_service.py
    1052  app/core/estrai/database_inspector.py
     970  app/core/manipola/executor.py
     963  app/core/carica/database_writer.py
     954  app/core/arricchisci/router.py
     917  app/utils/dataframe.py
     875  app/core/auth/auth.py
     815  app/core/carica/ddl_generator.py
     800  app/core/scheduler/execution_manager.py
     776  app/core/estrai/executor.py

## Verdetto realistico

**Riduzione fattibile: 3-6% del codebase prod (1800-3600 righe nette).**

**Non sara'** un -20% o piu'. La complessita' essenziale del dominio non
e' riducibile:
- multi-tenant + shared visibility
- artifact lifecycle tra DB / file system / worker / router
- scheduler + worker + orchestrator come sottosistemi distinti
- gestione geospaziale e CRS
- integrazioni esterne (ArcGIS, API custom, eseguibili)
- fail-closed core-level checks

Il risparmio vero viene **solo** da 3 categorie:
- A — duplicazione cross-file
- B — dead / near-dead code
- C — boilerplate worker/executor (refactor architetturale)

Il refactor strutturale (split di funzioni grandi, estrazione di helper
privati nello stesso file) **non riduce il totale** — lo ristruttura.
L'evidenza di questa sessione: dopo Plan C `api_connector.py` e' cresciuto
da 2362 a 3041 righe (+679) nonostante ogni monster function sia stata
dimezzata in lunghezza.

## Categoria A — Duplicazione cross-file identificata

Stima: **300-600 righe nette** risparmiabili.

### A1. Pattern `flat + data wrapper` ripetuto 3 volte

Le funzioni leggono un campo da un nodo canvas sia al root che
dentro `node["data"]` come fallback. Stessa logica in 3 posti:

- `app/core/arricchisci/api_connector.py:113` → `_nd(node, key, default=None)`
- `app/core/manipola/executor.py:33` → `_nf(node, key, default=None)`
- `app/core/arricchisci/executor.py:158` → `_nd` closure locale

Stima: -20 righe.

**Fix**: estrai `app/shared/node_fields.py::read_node_field(node, key, default)`,
aggiorna i 3 call site.

### A2. `update_columns_metadata` duplicato in 3 executor

Pattern "costruisci `_col_meta` dal DataFrame → chiama
`project_service.update_project_columns_metadata` → catch → warning":

- `app/core/estrai/executor.py:517-527`
- `app/core/manipola/executor.py:184-191`
- `app/core/arricchisci/executor.py:94-102`

Stima: -25 righe.

**Fix**: helper condiviso `app/services/project_service.py::update_columns_metadata_from_df(project_id, df)`
che centralizza la costruzione e il try/except.

### A3. Router try/except HTTPException boilerplate

10 router in `app/core/` hanno lo stesso pattern:

    try:
        ... logica
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRASH] ...: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")

Moltiplicato per ~5-10 endpoint/file significa ~200-400 righe di
boilerplate ripetuto.

**Fix**: decorator `@handle_route_errors(context="...")` in
`app/shared/http_errors.py`. Applicabile ai router esistenti con
invasione minima (1 decorator per endpoint).

Rischio: medio. Va fatto con cautela perche' i messaggi di errore
attualmente variano leggermente per endpoint. Lock test sui contratti
HTTP prima di toccare.

Stima: -200-400 righe.

### A4. OAuth2 token helpers

`app/shared/api_utils.py` ha due funzioni simili:
- `get_oauth2_token` (password grant)
- `get_oauth2_client_credentials_token` (client credentials grant)

Body molto simili: cache lookup, POST a token_url, parse response,
update cache. Differenza principale: payload del POST.

**Fix**: helper base + due wrapper thin. Stima: -30-50 righe.

### A5. `_mask_*` helpers

8 funzioni di mascheramento sparse:

- `app/core/arricchisci/api_connector.py::_mask_credentials` (ArcGIS config)
- `app/core/features/arcgis_client.py::_mask_secret` (ArcGIS client)
- `app/core/features/routes.py::_mask_secrets` (API response)
- `app/core/manipola/transforms/base.py::_mask_cf` / `_mask_email` /
  `_mask_phone` / `_mask_iban` / `_mask_custom` (PII transform)

I primi 3 sono **security masking** (oscura password/token in risposta
API). Gli altri 5 sono **data masking transforms** (PII anonymization
durante ETL). Sono domini diversi: non consolidabili direttamente.

MA i primi 3 possono diventare un unico `app/shared/secret_mask.py`.

Stima: -30 righe.

### Totale Categoria A: ~305-530 righe

## Categoria B — Dead / near-dead code

Stima: **200-500 righe nette** risparmiabili dopo audit mirato.

### B1. Fallback `[COMPAT-RESIDUE]` (gia' taggati)

Dopo il commit `bde8303` ci sono 3 blocchi marcati come compat residuo
con `logger.warning` attivo. Dopo 30 giorni di osservazione log di
prod:

    sudo journalctl -u plenora-backend -u plenora-celery --since '30 days ago' | grep COMPAT-RESIDUE

Se nessuno dei 3 scatta mai, sono rimovibili. Stima: -60-80 righe
(3 blocchi + commenti + logger call).

I 3 blocchi:
- `api_connector._filesystem_fallback_load_project`
- `api_connector._resolve_file_source_id_path` livello 2
- `manipola._carica_sorgenti_progetto` fallback pre-migrazione

### B2. File non auditati in questa sessione

Top 5 file grandi mai toccati, da auditare per dead code + commenti
bug history:

- `app/core/manipola/transforms/base.py` (3032 righe)
- `app/core/features/arcgis_client.py` (2315)
- `app/core/arricchisci/enrichment_service.py` (1974)
- `app/core/estrai/query_generator.py` (1663)
- `app/services/schema_inference.py` (1456)

Ipotesi conservativa: ~3-5% di rumore medio (commenti storici,
helper deprecati, wrapper doppioni). Stima: -200-400 righe totali sui
5 file.

Rischio: medio. Ogni file va auditato con lock test preventivi per i
contratti osservabili.

### B3. Import locali ridondanti

Pattern `from X import Y` sia al top-level che dentro una funzione.
Possono essere conseguenza di refactor incrementali dove l'import
lazy non e' piu' necessario.

Grep rapido:

    grep -rn "    from " app/core --include="*.py" | wc -l

Da fare audit per distinguere lazy-by-design (evita circular) vs
ridondante.

Stima: -30-80 righe.

### Totale Categoria B: ~290-560 righe

## Categoria C — Boilerplate worker/executor

Stima: **500-1000 righe nette** — MA e' refactor architetturale, NON
cleanup. Richiede decisione esplicita di aprire un nuovo refactor che
il piano originale sconsigliava ("non aprire un nuovo refactor grande").

### C1. Base class per gli executor

I 3 executor (`estrai`, `manipola`, `arricchisci` = `EnrichExecutor`)
implementano lo stesso pattern:

    init_security_context(project_id)
    open_run(run_id, project_id, group_id, module, triggered_by)
    try:
        load_sources(canvas)
        apply_transforms_or_enrichers(canvas)
        save_result_blob(project_id, output)
    except:
        append_step(status="failure", ...)
        return {success: False, ...}
    append_step(status="success", ...)
    close_run(run_id, success=True)
    return {success: True, result_id, ...}

Un `BaseExecutor` che centralizza `open_run`/`close_run`/`append_step`
e un `_build_response_dict()` helper eliminerebbero duplicazione
significativa fra i 3 file.

Stima: -300-600 righe nette su 3 executor.

Rischio: **alto**. Richiede lock test molto robusti sui contratti di
ciascun executor prima di toccare — equivalenti a quelli scritti per
`execute_enrichment` in Plan C (~24 test).

### C2. Workers tasks init/close pattern

`workers/tasks/pipeline_task.py`, `workers/tasks/carica_task.py` e
`workers/tasks/enrichment_task.py` hanno pattern simili di:

- creazione run_id
- open_run / set_run_context
- try block principale
- append_step
- close_run
- cleanup connessioni SQLite thread-local (il pattern fixato stamattina
  per il bug BUG-IMDF)

Stima: -200-400 righe nette su 3 task.

Rischio: alto. I task sono il codice meno testato del backend.

### Totale Categoria C: ~500-1000 righe — ma refactor

## Prerequisito obbligatorio: Scaglione 0 — Lock test preventivi

**Nessun cleanup parte senza safety net pre-esistente sui contratti
toccati.** E' la lezione principale di Plan C (sessione 2026-04-14):
per `execute_enrichment` volevo partire diretto col refactor ma ho
scoperto che la funzione aveva zero test in `tests2/`. Abbiamo scritto
24 lock test **prima** di toccarla. Quel lavoro ha catturato un
assunto sbagliato (ValueError in parallel branch e' non-blocking, non
propaga) che sarebbe diventato una regression silente senza i test.

Il protocollo e':

1. lock test sui contratti osservabili della zona target
2. verificare che tutti passino con il codice **attuale** (baseline)
3. refactor
4. `tests2/` deve restare verde; qualunque fallimento e' un segnale
   di contract break — investigare, non aggiornare il test per far
   passare il refactor
5. se un test va aggiornato e' **solo** perche' il cambiamento di
   contratto e' intenzionale e va documentato esplicitamente

Senza questo step, ogni ottimizzazione delle righe e' volare a vista.
Il costo dei test preventivi **non e' opzionale** — e' parte del costo
del refactor. Se non lo conti, sottovaluti il tempo totale.

### Scaglione 0 per Categoria A (quick wins)

I pattern di A1-A5 sono in gran parte gia' coperti dai lock test
esistenti in `tests2/`. Test preventivi aggiuntivi richiesti:

| Target | Test preventivo richiesto | Stima |
|---|---|---|
| A1 `_nd`/`_nf` → `read_node_field` | 3 casi: flat, data fallback, absent | 30 min |
| A2 `update_columns_metadata_from_df` | mock project_service, verifica chiamata post-run | 30 min |
| A3 `@handle_route_errors` decorator | per endpoint: 500 su Exception, HTTPException passthrough, logger.error emesso | **3-4 ore** (e' il target piu' rischioso) |
| A4 OAuth2 helpers consolidati | cache hit/miss, refresh su expiry, entrambi i grant | 1-2 ore |
| A5 `_mask_secret*` unificato | 3 funzioni → verifica che i campi target siano mascherati | 30 min |

**Totale test preventivi Scaglione 1: ~5-8 ore.**

### Scaglione 0 per Categoria B (audit file big)

I 5 file non toccati hanno copertura tests2 **molto parziale o
assente**. Prima di qualsiasi cleanup servono lock test baseline sui
contratti osservabili principali di ciascuno.

| File | Test baseline richiesti | Stima |
|---|---|---|
| `manipola/transforms/base.py` | per ogni trasformazione pubblica: 2-3 contract test (input/output) | 1 giorno |
| `features/arcgis_client.py` | auth, browse services/layers, query, write. Mock network | 4-6 ore |
| `arricchisci/enrichment_service.py` | executor contract: dedup, batch, rate limit, retry | 4-6 ore |
| `estrai/query_generator.py` | SQL generation per DB type, translation edge cases | 4-6 ore |
| `services/schema_inference.py` | detection per formato (csv, xlsx, gpkg, shp), edge case nullable | 4-6 ore |

**Totale test preventivi Scaglione 2: ~3-4 giorni.**

Considerare di NON audire tutti e 5 i file — scegliere i 2-3 con il
rapporto valore/tempo migliore dopo un grep preventivo dei pattern
di duplicazione/bug history.

### Scaglione 0 per Categoria C (refactor executor/worker)

I 3 executor sono gia' parzialmente coperti da `tests2/`:
- `manipola/executor.py` — 4 test security contract
- `arricchisci/executor.py` (EnrichExecutor) — test indiretti via api_connector lock
- `estrai/executor.py` — tests2 coverage limitata

I 3 worker task sono coperti parzialmente:
- `enrichment_task.py` — 10 lock test creati stamattina (tenant binding,
  result_id, task_id thread-local)
- `carica_task.py` — tests2 coverage limitata
- `pipeline_task.py` — tests2 coverage limitata

Test baseline **massicci** richiesti prima di BaseExecutor:

| Target | Test baseline | Stima |
|---|---|---|
| `EstraiExecutor` contract | init, load_sources, apply_query, save, response dict | 1 giorno |
| `ManipolaExecutor` contract (oltre ai security esistenti) | transform pipeline, GROUP BY, output save, snapshot intermediate | 1 giorno |
| `EnrichExecutor` contract (oltre a quelli esistenti) | delegation a api_connector, file_result vs dataframe, result_id | 4-6 ore |
| `pipeline_task` + `carica_task` baseline | task entry point, open_run/close_run, error propagation | 1 giorno |

**Totale test preventivi Scaglione 3: ~3-4 giorni.**

Questo e' il motivo per cui Scaglione 3 e' flagged "alto rischio":
non il refactor in se', ma il fatto che senza 3-4 giorni di test
preventivi il refactor creerebbe i bug a catena visti stamattina.

## Piano a 3 scaglioni

### Scaglione 1 — Quick wins (1 giorno, -500/-800 righe)

Target: Categoria A completa.

Operazioni:
1. Audit + unifica `_nd`/`_nf` → `app/shared/node_fields.py`
2. Estrai `update_columns_metadata_from_df` in `project_service`
3. Decorator `@handle_route_errors` + applicato ai 10 router
4. Consolida `get_oauth2_*` in api_utils
5. Unifica i 3 `_mask_secret*` in `app/shared/secret_mask.py`

Protocollo: audit → gate → lock test preventivi → apply → tests2 verde
→ commit per step → push.

Rischio: **basso**. Cambi localizzati, lock test gia' esistenti per la
maggior parte dei pattern.

Valore: **alto rapporto righe/tempo**.

### Scaglione 2 — Audit profondo 5 file big non toccati (2-3 giorni, -600/-1400 righe)

Target: Categoria B2 + B3 sui 5 file sopra.

Per ogni file:
1. Lock test baseline (dove mancano) in `tests2/`
2. `grep` pattern storici (BUG-XX, FIX, Refactor#, pre-refactor, legacy)
3. Classificare: dead / compat / vivo
4. `inspect.getsource` per trovare funzioni monster mai splittate
5. Applicare protocollo di Plan C (audit → gate → apply incrementale)
6. Commit per file, deploy quando significativo

Rischio: **medio**. File pesanti, lock test parziali o assenti.

Valore: dipende dal file. `manipola/transforms/base.py` e' probabilmente
pieno di trasformazioni specializzate poco duplicate → risparmio basso.
`arcgis_client.py` e `enrichment_service.py` hanno probabilmente piu'
boilerplate → risparmio alto.

### Scaglione 3 — Refactor architetturale (1 settimana, -500/-1000 righe)

**APERTURA ESPLICITA DI NUOVO REFACTOR.** Non e' cleanup.

Target: Categoria C (BaseExecutor + worker task pattern).

Protocollo:
1. Decidere prima se farlo. Leggere il warning del piano originale:
   > "non aprire un nuovo refactor grande sullo scheduler"
2. Se si: lock test massicci preventivi su tutti e 3 gli executor +
   tutti e 3 i worker task. Costo: 1-2 giorni di scrittura test.
3. Definire `BaseExecutor` con init / open_run / close_run /
   append_step / response builder.
4. Migrare i 3 executor uno alla volta, con deploy incrementale.
5. Stessa cosa per i worker task.

Rischio: **alto**. Tocca il cuore dell'esecuzione pipeline. I bug
latenti emersi in questa sessione (task_id thread-local, tenant
binding, result_id propagation) avvisano che questa zona e' fragile.

Valore: alto **se** fatto con la safety net giusta. Senza, e' un
disastro che ricrea i bug di stamattina a catena.

## Raccomandazione

**Scaglione 1 vale la pena.** Rapporto valore/rischio ottimo. 1 giorno
per ~500-800 righe eliminate, zero architettura aperta, lock test
esistenti coprono la maggior parte.

**Scaglione 2 considerabile caso per caso.** NON fare i 5 file in
blocco. Prima audit quantitativo di ciascuno (solo le metriche di
duplicazione pattern + dead code), poi decidere quali 2-3 vale la
pena auditare a fondo.

**Scaglione 3 non adesso.** Rispettare il warning del piano originale:
e' refactor, non cleanup. Aprilo solo se dopo Scaglione 1 + 2 c'e'
ancora motivazione forte a risparmiare righe, E hai la possibilita' di
fermare le altre feature per 1 settimana intera senza interruzioni.

## Stima cumulativa onesta

Il tempo totale include Scaglione 0 (test preventivi) + esecuzione.
Senza lo Scaglione 0 non si parte.

| Scaglione | Righe risparmiate | Test preventivi | Esecuzione | **Totale** | Rischio |
|---|---|---|---|---|---|
| 1 | -500 / -800 | ~5-8h | 1 giorno | **~1.5-2 giorni** | basso |
| 2 (2-3 file) | -400 / -900 | ~2-3 giorni | 2-3 giorni | **~4-6 giorni** | medio |
| 3 | -500 / -1000 | ~3-4 giorni | 1 settimana | **~10-12 giorni** | alto |
| **Totale max realistico** | **-1400 / -2700** | ~6-8 giorni | ~11-14 giorni | **~17-22 giorni** | misto |

Su 59,805 righe prod, il massimo realistico e' un **4.5% di riduzione**
con ~3 settimane di lavoro pieno, **1/3 del tempo** in scrittura di
test preventivi.

Se lo Scaglione 3 viene escluso (consigliato), il range diventa
**-900/-1700 righe in ~5-8 giorni**, di cui ~2-3 giorni di test.

Il rapporto valore/tempo:

- **Scaglione 1**: ~500 righe in ~1.5 giorni → ~330 righe/giorno. **Migliore.**
- **Scaglione 2**: ~650 righe in ~5 giorni → ~130 righe/giorno. Accettabile.
- **Scaglione 3**: ~750 righe in ~11 giorni → ~68 righe/giorno. **Peggiore**, anche senza contare il rischio architetturale.

Chi vendesse un "-20% senza rischio" starebbe mentendo — o starebbe
proponendo di rimuovere feature reali o gestire i casi edge meno.

## Cosa NON includere nel piano

Il piano originale `cleanup_analysis_backend_detailed.md` esplicitamente
sconsigliava:

- non aprire un nuovo refactor grande sullo scheduler
- non rifare `file_service.py` in questa fase
- non togliere subito la compatibilita' di `PipelineArtifact`
- non eliminare fallback legacy prima di averli classificati
  (gia' fatto in questa sessione con tag `[COMPAT-RESIDUE]`)

Questi warning restano validi anche per il piano di riduzione.

## Metriche di osservabilita' post-intervento

Per giustificare ogni scaglione:

1. **Righe prima/dopo** per file e per dir (via `git diff --stat`)
2. **Numero di helper condivisi introdotti** (nuovi in `app/shared/`)
3. **Lock test aggiunti** (safety net per il refactor successivo)
4. **Tempo tra commit di refactor** (protocollo: mai saltare i tests
   tra un fix e l'altro)
5. **Log `[COMPAT-RESIDUE]`** per giustificare le rimozioni di B1

## Azioni concrete se si procede con Scaglione 1

1. Creare branch `cleanup/code-reduction-s1`
2. Eseguire audit quantitativo preciso sui 5 target A1-A5 (`grep -c`
   per ogni pattern, contare righe per ciascuno)
3. Scrivere lock test preventivi per i 10 router (se non gia' coperti
   in `tests2/`)
4. Applicare i 5 fix uno alla volta:
   - commit isolato per ciascuno
   - `tests2/` verde tra uno e l'altro
   - push ma no deploy fino a quando l'intero Scaglione 1 non e'
     completo
5. Deploy in blocco del Scaglione 1 dopo test completi
6. Misurare risparmio effettivo e confrontarlo con la stima
