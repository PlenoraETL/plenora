# Blocco A — A3 — rapporto del 4 novembre 2026

## Stato della fase

**Completamento ancora parziale: A3 NON completata.** P1 (matrice negativa esaustiva delle 71 operazioni e guardia enumerativa dei vincoli generici) e P2 (unificazione integrale delle regole) restano aperti. Questo secondo intervento corregge il dialetto SQL, amplia il preflight, verifica la riproduzione a 96 colonne e corregge l'identità dell'Output Arricchisci e le mappe dei tipi derivate. I test di questi percorsi non sostituiscono P1. La tabella di completamento in fondo è lo stato corrente; i numeri della prima sessione restano documentati come storico.

Modifiche soltanto in `backend/` e in questo rapporto. Nessuna modifica frontend, nessuna rigenerazione dello snapshot OpenAPI frontend, nessun commit, nessun sotto-agente avviato. Riferimento del confronto: **3c8609fe**. Estrazioni, sonde e log esclusivamente in:

`C:/Users/Marco/AppData/Local/Temp/claude/C--Users-Marco-Desktop-plenora/855a9a5f-29c2-43d0-94ce-65c98f738527/scratchpad/`

## 1. Preparazione condivisa e Valida

`PluginExecutor.prepare` condivide normalizzazione dei parametri, binding della firma e preparazione con `execute`/`execute_with_params`. Il validatore usa anche il risolutore dei riferimenti del plugin, incluse le espressioni per ID. I controlli aggiunti non valutano formule su righe sintetiche: il frame vuoto fornisce soltanto nomi e dtype alle funzioni di preparazione, non certifica compatibilità dei valori.

- **Formula:** `prepare_formula` verifica forma degli elementi, AST e namespace delle colonne/funzioni. La usa anche `op_formula`. Coperto `valore_catastale_L / vc_tot_contratto_Romi_L` con secondo nome assente; stesso messaggio pubblico dell'esecuzione.
- **Join:** `prepare_join` è estratto dal percorso reale, inclusi normalizzazione chiavi, selezioni, piano colonne e verifica separata SX/DX. Valida riceve gli ingressi ordinati per handle, invece della sola unione dei nomi.
- **Multi-Join:** estratta `prepare_multi_join`, mantenendo `_esegui_multi_join`. Valida risolve le coppie con la stessa funzione e non segnala più semplicemente l'assenza di `transform_op`.
- **Filtra:** preparazione condivisa di struttura, riferimenti, operatori delle condizioni e valori di `in/not_in`, con gli stessi errori del runtime.
- **Output:** Valida richiama `result_group_plan`, anche quando non è presente una selezione delle colonne.
- **Estrai:** `validate_canvas` compila il SQL con `QueryGenerator`, quando il canvas contiene tabelle. Ora usa `sql_connection_options`, condivisa con esecuzione e preview, e la connessione del contesto risolto. Se la connessione non è leggibile emette `sql.not_guaranteed` (warning) e non compila con un dialetto inventato.
- **Arricchisci:** lettura della configurazione API corrente e riuso di `build_enrichment_executor`/`validate_input`; metodo HTTP/body e dialetto CSV passano dai validatori del runtime tramite `prepare_api_request`.

**Non coperto integralmente:** gli altri vincoli delle operazioni tabellari, testuali e geografiche della raccolta classe 2; vincoli dei dati, CRS, cardinalità e memoria; tutti i percorsi generator e REST-file; unificazione completa del precedente validatore dichiarativo con la nuova preparazione. I controlli precedenti non sono stati tutti rimossi. Non è quindi vera, per l'intero registry, l'affermazione «nessuna regola duplicata».

## 2. Manifest e valori runtime

Valida compila `compile_manifest`; il test sullo scalare **Input 1** senza binding verifica l'errore «non è collegato a nessun campo». Il parametro `for_project=True` evita l'adattatore di pubblicazione legacy degli eseguibili, che inventa un input file e legge le API dal DB: quell'adattatore non appartiene alla preparazione di un normale progetto. Il comportamento preesistente di pubblicazione degli eseguibili resta il default.

`POST /projects/{id}/validate` accetta facoltativamente:

```json
{"canvas":{"nodes":[],"edges":[]},"inputs":{"anno":2026}}
```

Con valori forniti chiama `apply_project_runtime_inputs`, come l'avvio, e valida il canvas risolto. Con canvas non salvato applica il controllo dei riferimenti/gruppi e non persiste `canvas_validated`. La risposta conserva `valid`, `has_errors`, `issues`, aggiungendo `manifest` e:

```json
{"checks":{"configuration":"verified","runtime_inputs":"verified","data":"not_checked","external_dependencies":"not_guaranteed","resources":"not_guaranteed"}}
```

`verified` riguarda solo i controlli eseguiti; **non equivale a certificazione di tutte le regole A3**. I file runtime restano soggetti alla policy dell'avvio progetto: sostituzione disponibile negli eseguibili. Non è stata aggiunta una preparazione completa degli upload degli eseguibili.

## 3. Rinomina con nomi duplicati

Valida segnala la destinazione e i nomi sorgenti quando la rinomina produce collisioni, comprese quelle con colonne non rinominate. Test: `name` e `city_id` verso `Comune`; messaggio contenente tutti e tre i nomi.

Il transform continua a restituire le due colonne, senza cambiare i dati del runtime. Nel codice di riferimento il salvataggio finale Manipola normalizza già i duplicati: lo schema dell'Output ora usa lo stesso piano di nomi, estratto in `shared/execution/output_names.py`. `dataframes.tabular.make_unique_column_names` rimane un delegatore compatibile: nessun chiamante storico è stato eliminato.

**Riproduzione aggiunta nella seconda sessione: 96 = 96.** Sorgente con `name`, `city_id` e altre 94 colonne; Sort, Distinct, Rinomina finale di entrambe su `Comune`; Output senza selezione. POST `/schemas`, DataFrame passato al salvataggio, CSV serializzato e contratto coincidono: `Comune`, `Comune_2`, altre 94 colonne. È una riproduzione costruita, non una lettura del progetto reale `SC_scheda_manipola`; HTTP/DB/storage sono simulati, esecutore e serializzazione CSV sono reali.

## 4. Pivot: certezza dei nomi e opzionalità

Con mapping esplicito:

- le colonne indice conservano nome e opzionalità ereditata;
- le destinazioni del mapping sono opzionali con nome noto;
- le altre colonne dell'ingresso sono opzionali, per rappresentare il ramo «nessuna corrispondenza → input invariato»;
- l'ordine rimane incerto; l'incertezza dei tipi resta indipendente dai nomi.

Il test esegue due frame con lo stesso schema, uno con valore corrispondente e uno senza: tutte le colonne obbligatorie esistono in entrambi, nessuna uscita esce dall'insieme dei nomi possibili. Senza mapping i nomi restano dinamici. Corretto anche l'indice composto parzialmente mancante, che a runtime lascia invariato l'ingresso.

## 5. Preflight Arricchisci

Nuovo **POST `/projects/{id}/preflight`** (sotto il prefisso API applicativo). Body:

```json
{"inputs":{"anno":2026},"canvas":{"nodes":[],"edges":[]},"force_recalculate":false}
```

`canvas` è facoltativo: in assenza usa quello salvato. Compila il manifest, applica gli input, prepara il canvas con il controllo gruppi, legge le connessioni correnti autorizzate e stima ciascun nodo API sul suo ingresso nel DAG. Non usa l'ultimo output del progetto come sostituto dell'ingresso.

Esempio illustrativo della risposta per un nodo con dati disponibili:

```json
{
  "valid":true,
  "manifest":{"project_type":"arricchisci","source_project_id":"p","inputs":[],"bindings":[]},
  "preparation_fingerprint":"<sha256>",
  "nodes":[{
    "node_id":"api-1","input_rows":4,"input_rows_status":"exact",
    "sampled":false,"sampled_rows":4,"effective_rows":3,
    "status":"estimated","api_calls":1,"valid":true,
    "reason":"Stima richieste senza retry, cache esterna o pagine aggiuntive",
    "external_dependencies":"not_guaranteed"
  }],
  "checks":{"runtime_inputs":"verified","external_dependencies":"not_guaranteed","resources":"not_guaranteed"}
}
```

Sono riutilizzati opzioni del builder reale, template, costruzione parametri, condizione `execute_if`, skip null e chiavi di deduplicazione. Il generator ha una richiesta iniziale stimata, non zero richieste per zero righe. I nodi successivi a un'API hanno quantità `unknown`, `api_calls: null`: non si inventano le risposte esterne. I contatori sulle sorgenti campionate non sono quantità esatte dell'intera esecuzione.

`SourceLoader.load_in_memory` legge blob in modalità bytes, senza spool o file temporanei. Sono ammessi CSV, JSON, XLSX/XLS e Parquet; gli altri formati sono dichiarati non conoscibili senza materializzazione. I progetti sorgente non sono ricalcolati. Le trasformazioni locali non pubblicano risultati né checkpoint. Le sorgenti vengono campionate a 10.000 righe **dopo la lettura del blob**: non è una garanzia di memoria limitata al campione.

L'avvio `/projects/{id}/enrich-async` accetta `preparation_fingerprint`; se fornita, prepara nuovamente il canvas e confronta l'impronta prima di creare il task. Sono inclusi canvas risolto, contesto/input, gruppo, connessioni, revisione e opzione ricalcolo. La compatibilità senza impronta rimane. Esempio 409:

```json
{"detail":{"error":"validation_error","reason":"preparation_changed","message":"Preparazione cambiata: canvas, input, connessioni o opzioni diversi dal preflight"}}
```

Validate/estimate compatibili riferiti al progetto passano dal preflight e richiedono `node_id` se l'API non identifica un nodo univoco; accettano anche `inputs` e `canvas`. Il percorso legacy con `file_source_id` esplicito rimane: la validazione con frame riusa l'executor, la stima standalone file ora delega a `estimate_api_input`, come il preflight canvas (seconda sessione).

**Limiti residui:** non sono coperti tutti i vincoli request/response generator/REST-file, batch e paginazione; le dipendenze da rete e sorgenti remote sono dichiarate sconosciute. L'impronta non blocca una variazione dei dati sorgente a schema/configurazione invariati. Non c'è ancora una prova completa dell'assenza di divergenze tra tutti i rami di preparazione e avvio. Il punto 5 non è integralmente chiuso.

## 6. Valori distinti Lookup

**POST `/projects/{id}/nodes/{node_id}/distinct`** riceve `canvas`, `column`, `column_id`, `limit`. Richiede il canvas fornito, usa la stessa preparazione/autorizzazione di POST schemas, risolve l'ID sull'ingresso del nodo e legge l'ingresso effettivo. `limit` è compreso fra 1 e 1000. Non usa l'output del nodo Lookup.

```json
{"canvas":{"nodes":[],"edges":[]},"column":"a","column_id":"col-a","limit":100}
```

```json
{"values":[{"value":"1","is_null":false,"count":2}],"column":"a","column_id":"col-a","truncated":true,"sampled":false,"sampled_rows":3,"reason":null}
```

`lookup_value_text` centralizza la rappresentazione testuale, usata anche nella normalizzazione numerica del Lookup. I conteggi delle voci mantenute proseguono anche dopo il raggiungimento del tetto. Null e testo sono distinti. Nome ambiguo/assente o ID non risolto producono 422. File runtime:

```json
{"values":[],"truncated":false,"sampled":false,"sampled_rows":0,"reason":"Input file runtime: dati non disponibili nel canvas"}
```

Le sorgenti remote non supportate e i formati che richiedono file temporanei danno un motivo esplicito, non un campione inventato.

## 7. Versione del canvas

PUT canvas e POST ripristino accettano **`base_updated_at`** ISO; il parsing Pydantic rifiuta date malformate. Non è stato aggiunto il percorso alternativo `If-Unmodified-Since`.

Il controllo è nel medesimo UPDATE SQL: `WHERE id = ... AND updated_at <= ...`. Una base superata non modifica canvas, revisione o validazione, né avvia la cascata. Senza base resta la scrittura compatibile. Entrambe le risposte espongono `updated_at`; viene trasportato il timestamp di quella scrittura, evitando di usare una revisione successiva ottenuta da una rilettura concorrente.

```json
{"status":"ok","project_id":"p","updated_at":"2026-11-04T14:00:00+00:00"}
```

```json
{"detail":{"error":"validation_error","reason":"canvas_version_conflict","message":"Il canvas sul server è più recente della versione di base","updated_at":"2026-11-04T14:00:00+00:00"}}
```

Il ripristino restituisce `{"canvas": {...}, "updated_at": "..."}`. Test: conflitto atomico, due schede con stessa base (solo la prima scrive), propagazione della base e della nuova revisione nel ripristino. Non è un test di concorrenza su PostgreSQL reale.

## 8. Prestazioni e tipi

`non_null_head_values` legge blocchi da 1024 elementi e si ferma appena ha lo stesso campione dei primi N non nulli usato prima. Non taglia il campione alle prime N righe: prefissi nulli lunghi conservano il comportamento. `TypeInferrer` riusa i dieci valori raccolti, evitando due/tre `dropna` sull'intera colonna object.

Microbenchmark isolato, **295.782 valori object × 96 invocazioni**:

| Versione | Tempo | Tipo |
|---|---:|---|
| HEAD 3c8609fe | 1,134827 s | str |
| Working tree | 0,009603 s | str |

È una sonda del classificatore, non dell'intera pipeline. Non conferma né spiega da sola il passaggio dal vivo da circa 2,5 a 4,5 minuti. Le colonne interamente nulle richiedono ancora la scansione, necessaria a conservare il risultato. Tutti i **546 contratti** della matrice A2 confrontata hanno nomi e tipi identici.

## Test e confronti

Nuovo `backend/tests2/contracts/test_a3_preparation.py`: **28 casi**. Nove configurazioni negative parametrizzate su Formula, Filter, Join e Lookup; Group by rimosso, manifest scalare, Multi-Join incoerente, collisione Rename, due rami Pivot, tipi/prefissi nulli, dimensione dei blocchi, versioni, template/dedup/execute_if, file runtime, distinct con cardinalità, lettura bytes senza spool ed esecuzioni complete Manipola/Estrai fino al salvataggio e contratto.

**Manca la guardia che fallisce quando una delle 71 operazioni non ha casi negativi.** Non è stata sostituita con casi artificiali identici per tutte le operazioni.

Confronti isolati HEAD/working tree:

- **60 SQL identici, 36 DataFrame identici, 546 contratti verificati**; ulteriore confronto esplicito di tutti i nomi e tipi dei 546 contratti.
- **36 DataFrame API identici, 144 contratti × tre pubblicazioni** per Estrai, Manipola, EnrichExecutor e worker: nomi, continuità ID e assenza di rimozioni. Le API, il DB e lo storage sono simulati.
- Esecuzioni complete **Estrai e Manipola**, con compilatore/plugin e salvataggio reali fino al boundary di persistenza simulato: frame e contratti identici, escluso soltanto `updated_at`.
- Nessuna funzione storica eliminata. Sono mantenuti `_esegui_multi_join`, i metodi del salvataggio e il delegatore `make_unique_column_names`; ricerca dei chiamanti e guardia `test_self_method_calls_exist.py` verde nelle verifiche mirate.

Verifiche intermedie: 1.504 passati/8 fallimenti iniziali; 12.989 passati/42 saltati/14 fallimenti di guardie architetturali, poi correzioni senza aumentare baseline; 6.615 passati/42 saltati/un'aspettativa del validatore da aggiornare, corretta; ultimo file A3 **28 passati**. Due tentativi della verifica estesa mirata sono stati interrotti dopo aver diagnosticato l'accesso DB dell'adattatore legacy del manifest. Non erano la suite completa richiesta.

Ruff F401/F811/F821 sui file modificati/nuovi: verde prima della suite finale. `git diff --check` verificato durante il lavoro. Nessuna baseline architetturale aumentata.

### Suite completa della prima sessione (storico)

Avviata una sola volta dal venv richiesto, cwd `backend/`, log `scratchpad/a3-full.log`:

```powershell
& 'C:/Users/Marco/AppData/Local/Temp/claude/C--Users-Marco-Desktop-plenora/855a9a5f-29c2-43d0-94ce-65c98f738527/scratchpad/venv-backend/Scripts/python.exe' -m pytest tests2/manipola tests2/projects tests2/contracts tests2/contract tests2/shared tests2/orchestrator tests2/estrai tests2/arricchisci tests2/eseguibili tests2/services -q -p no:cacheprovider -o addopts=
```

Esito dell'unica suite completa: **14.833 passati, 43 saltati, 3 falliti, 6 warning in 429,73 secondi**, exit code 1. Nessuna modifica al codice durante l'esecuzione.

Fallimenti e verifica successiva:

- `test_runtime_node_types_have_executable_cases`: la scoperta comprende ora `project_input` nella preparazione in memoria; aggiunto alla tabella il caso eseguibile A3 già presente, che verifica esplicitamente l'assenza di dati e il motivo per un file runtime.
- `test_pivot_mapping_outputs_index_and_declared_columns`: aggiornata l'aspettativa per includere le colonne originali del ramo senza corrispondenze; aggiunte asserzioni sull'indice certo e sulle altre colonne opzionali.
- `test_openapi_snapshot_matches_or_update`: disallineamento atteso per i nuovi endpoint `/api/projects/{project_id}/preflight` e `/api/projects/{project_id}/nodes/{node_id}/distinct`. Lo snapshot è in `frontend/tests/fixtures/openapi.snapshot.json`: lasciato invariato come richiesto, senza indebolire o saltare il test.

Dopo la suite sono stati modificati soltanto i due test backend sopra e questo rapporto. Riesecuzione mirata dei due casi, dell'intero file A3 e di `test_self_method_calls_exist.py`: **33 passati, 1 warning in 1,69 secondi**. Non è stata eseguita una seconda suite completa. Il risultato completo resta quindi quello sopra; il fallimento OpenAPI resta aperto fino alla rigenerazione dello snapshot da parte dell'utente.


## Seconda sessione — richiesta di completamento P1–P7

### Stato puntuale

| Punto | Stato corrente | Evidenza e limite |
|---|---|---|
| P1 | **APERTO** | Il registry resta di 71 operazioni. Non è stata aggiunta la matrice negativa esaustiva con eccezioni motivate né la guardia enumerativa richiesta. Non dichiaro parità statica esaustiva. |
| P2 | **PARZIALE** | Eliminata la ripetizione dei controlli `in/not_in` del Filter; il runtime consuma i valori normalizzati da `prepare_filter`. L'AST delle operazioni Formula non è ricontrollato dopo `prepare_formula`. Guardia AST sui due messaggi Filter. Restano le regole dichiarative generali del validatore e manca la protezione completa contro tutte le duplicazioni. |
| P3 | Implementato e verificato | `sql_connection_options` risolve provider e schema sia dalla connessione runtime sia dalla configurazione letta da Valida. Il router trasporta il default_connection_id risolto dagli input, anche nella catena di progetti. Test SQLite/PostgreSQL con compilazione reale e fallback non garantibile. |
| P4 | Ampliato, con limiti espliciti | Canvas e file standalone usano `estimate_api_input`. Generator riusa i gate e i parametri base reali; paginazione riusa il parsing numerico del ciclo. REST-file usa `_parse_rest_file_config`. Batch usa `prepare_batch_rows`, la stessa lista preparata dal runtime: niente dedup nel batch nativo. Non è una certificazione di ogni ramo request/response o della disponibilità dei file remoti. |
| P5 | Verificato sulla riproduzione | **96 = 96**, nomi identici fino al CSV e al contratto; suffissi `Comune`, `Comune_2`. |
| P6 | Implementato e verificato | Output Arricchisci risolve l'ID sulle colonne inferite del predecessore. Inferenza ed esecuzione usano `resolve_output_column_refs`/`unique_column_names`. Rinomina a monte per alias con ID stabile: colonna esclusa tanto nella previsione quanto nel risultato. `inputColumns` obsoleto non influenza l'esito; ID non risolto conserva il fallback al nome della funzione condivisa. |
| P7 | Implementato e verificato | Manipola, Estrai e controllo geo consultano `infer_canvas_columns`, basata su `SchemaInferenceService`. Per transform/api_enrich/multi_join/result prevalgono le colonne inferite. Test transform e api_enrich senza colonne e con colonne obsolete; GeoJSON reale identico per dati, geometria e tipi con metadati assenti/corretti/obsoleti. Escluso dal confronto soltanto il nome casuale del layer temporaneo GDAL. |

### Modifiche e regole delegate

- `prepare_filter` restituisce anche la lista normalizzata per `in/not_in`: i messaggi di lista non valida e valori non numerici hanno un solo proprietario, verificato via AST. `op_filter` applica il piano restituito.
- `ManipolaNodeValidator` non ripete la gate AST per `transform_op=formula`, già eseguita dalla preparazione. Il controllo legacy di campi formula presenti su altre operazioni è conservato.
- I gate URL mancante e incompatibilità async/paginazione del generator sono estratti in `prepare_generator_request`, invocata sia dall'esecuzione sia dal preflight. Conversione page_size/max_rows/start_page condivisa con il ciclo di paginazione.
- La preparazione delle righe batch è estratta in `prepare_batch_rows`, utilizzata dall'esecutore e dalla stima. La stima standalone non costruisce più un executor con opzioni artificiali: usa il builder e il percorso del canvas.
- La quantità delle richieste resta **stimata** quando input e configurazione sono noti: retry, cache, fallback del batch, polling e rete impediscono di garantire il totale fisico. Per generator paginato: `status=unknown`, `api_calls=null`, `initial_api_calls=1`. L'endpoint compatibile conserva `null`, senza sostituirlo con zero. Il numero delle righe lette è `exact` o `estimated` (campione) nel ramo tabellare; i risultati esterni non sono prevedibili.
- REST-file preflight prepara la configurazione con lo stesso parser runtime; numero previsto di invii = 1, disponibilità/leggibilità dei file e risposta non garantibili.
- `CanvasGraph` ed `EnrichExecutor` serializzano i modelli Pydantic con `exclude_unset=True`: i default non diventano campi espliciti che nascondono `data`. Gli oggetti compatibili che espongono soltanto `model_dump()` mantengono il vecchio percorso.
- Rimosso `_new_transform_types`: era la seconda propagazione locale dei tipi, ora sostituita dall'inferenza canonica. Prima della rimozione ricerca `rg` su tutto `backend/`: nessun chiamante residuo oltre alla definizione dopo la sostituzione dell'unico call site. Conservati tutti i metodi di salvataggio degli esecutori. Guardia `test_self_method_calls_exist.py` verde.

### Verifiche della seconda sessione

Nuovo `tests2/contracts/test_a3_completion.py`: **18 casi**. Comprende due dialetti, connessione SQL illeggibile, riproduzione 96 colonne attraverso POST schemas, esclusione Output per ID dopo rinomina, geometria transform/API senza metadati derivati, serializzazione GeoJSON, batch/standalone, generator/paginazione e tre configurazioni negative contro il generator reale, REST-file contro il parser reale, guardia AST e pipeline Arricchisci fino a CSV/contratto.

Verifiche intermedie:

- 2.064 passati e 2 falliti nella prima verifica per domini; corretti compatibilità degli oggetti canvas e null nella risposta estimate.
- 12.911 passati, 43 saltati, 10 falliti nella verifica di guardie/contratti/servizi/Arricchisci. Corrette annotazioni, import locali, broad catch, compatibilità model_dump, helper senza chiamanti e tabella dei tipi runtime. Nessuna baseline aumentata.
- Riesecuzione mirata dopo le correzioni: **200 passati**; guardia dead code separata: **1 passato**.
- Alcune sonde mirate sono state interrotte per un accesso DB non simulato di `canvas_access.lookup_api_type`; corretta la fixture API. Non erano suite complete.

Confronti ripetuti contro **HEAD 3c8609fe**, tutti in scratchpad:

- **60 SQL, 36 DataFrame e 546 contratti** verificati dalla matrice runtime.
- **36 DataFrame API identici, 144 contratti × 3 esecuzioni**: nomi esatti, ID stabili, nessuna rimozione.
- **Estrai e Manipola**: esecuzioni complete, frame e contratti identici al riferimento, escluso `updated_at`.
- **Arricchisci**: ciclo canvas, generatore, parser della risposta, salvataggio CSV e contratto; DataFrame, **byte CSV** e contratto identici a HEAD, escluso `updated_at`. Simulati HTTP e persistenza esterna, non il motore.

Log: `a3-completion-targeted.log`, `a3-completion-guards.log`, `a3-completion-guard-fixes.log`, `a3-completion-matrix.log`, `a3-completion-api.log`, `a3-completion-save-wt.log`, `a3-completion-arr-head.log`, `a3-completion-arr-wt.log` nello scratchpad autorizzato.

### Suite completa della seconda sessione

Una sola invocazione del comando integrale richiesto, dal venv e da `backend/`; log `scratchpad/a3-completion-full.log`. Nessuna modifica al codice durante questa suite. **14.867 passati, 43 saltati, 1 fallito, 6 warning in 503,36 secondi**, exit code 1. Unico fallimento: `tests2/contract/test_openapi_snapshot.py::test_openapi_snapshot_matches_or_update`, atteso per i nuovi endpoint e lo schema nullable delle stime non conoscibili. Snapshot frontend lasciato invariato. Dopo la suite: rimosso soltanto un import inutilizzato da un test backend e aggiornato questo rapporto; nessun cambiamento runtime. Riesecuzione mirata del file interessato, dei 18 nuovi casi e della guardia self-method: **157 passati, 2 warning in 2,41 secondi**. Nessuna seconda suite completa.

Nessun commit, nessun sotto-agente avviato, nessuna modifica frontend o comando npm/vitest. CRS effettivi, cardinalità dei dati, memoria e dipendenze esterne restano non garantibili. **La fase non è completata: P1 e P2 restano aperti e P4 non è una prova esaustiva di tutti i rami.**


## Terza sessione — esecuzione a vuoto

### Regola (per costruzione)

**Valida di un passo Manipola = esecuzione a vuoto della stessa operazione** (`app/core/manipola/dry_run.py`). Per ogni ingresso, nell'ordine delle maniglie usato dall'esecuzione, si costruisce un frame con **zero righe** e con colonne, nomi (anche duplicati) e dtype dello schema inferito. Poi si chiama lo stesso percorso dell'esecuzione:

- transform: `PluginExecutor.execute_node`, cioè il corpo di `execute` senza log, che `execute` ora richiama. Comprende risoluzione dei riferimenti per ID da `inputColumns` (costruiti come li costruisce l'esecutore), `prepare_params`, `prepare` e la funzione dell'operazione;
- Multi-Join: `ManipolaExecutor._esegui_multi_join`.

Qualunque eccezione diventa un issue `preparation.invalid` con **lo stesso messaggio** del runtime.

- **Dtype:** si ottengono da un'unica funzione, `canvas_type_to_pandas_dtype`, che ora restituisce `"str"` (il dtype stringa di pandas 3, lo stesso che `build_columns_with_types` riporta come `str`) invece di `object`. Le colonne geometriche diventano `GeoSeries` vuote con il CRS `geometry:<sub>:<srid>` dello schema; il frame è un GeoDataFrame con geometria attiva `geometry`, oppure la prima disponibile. La costruzione sta nel boundary dataframe: `geodata.empty_frame_from_schema`.
- **Severità:** un rifiuto è `warning` se i nomi a monte sono `indeterminate`. È `warning` anche quando il rifiuto sparisce rilassando ciò che lo schema non garantisce (`data_dependent`): tipi incerti (colonne non geometriche `object`) o CRS ignoto (EPSG:4326 assunto). In tutti gli altri casi è `error`. I tipi incerti non degradano gli errori sui nomi, come stabilito in A1.
- **Errori legati ai dati:** cardinalità, valori, CRS reali e memoria non compaiono su zero righe. Restano non garantibili e non diventano errori di Valida.
- **I/O:** nessuna delle 71 operazioni fa chiamate esterne. La tabella dichiarativa `CON_IO_ESTERNO` esiste ed è vuota; un'operazione con I/O non verrebbe mai eseguita a vuoto.

### Tabelle (guardia `tests2/contracts/test_a3_dry_run_parity.py`)

| Tabella | Operazioni | Contenuto |
|---|---:|---|
| `CASES` | **50** | 57 negativi veri: l'esecuzione reale con righe li rifiuta, Valida a vuoto li rifiuta con lo stesso messaggio |
| `VUOTO_NON_GARANTIBILE` (runtime, `dry_run.py`) | **8** | transpose, reorder_columns, string_pad, string_length, window_function, uuid_generator (scorciatoia `len(df)==0 → return df` prima di leggere i parametri); geo_nearest e geo_count_points_in_polygons (con un ingresso vuoto restituiscono il sinistro o i poligoni prima di leggere i parametri). Esito `not_guaranteed`, mai errore |
| `SENZA_VINCOLI` (test) | **13** | concat (un solo booleano), cross_join (nessun parametro), 10 operazioni geo con parametri solo geometrici risolti sulla geometria attiva, geo_overlay (`how` non valido ricondotto al default) |
| **Totale** | **71 = registry** | Un'operazione nuova, mancante o presente in due tabelle fa fallire il test |

Le 6 operazioni con scorciatoia restano in `VUOTO_NON_GARANTIBILE` perché proprio quella scorciatoia è la prova runtime su cui poggia l'incertezza dichiarata in A1 (`test_a1_uncertainty_witnesses`). Toglierla obbligherebbe a cambiare anche l'inferenza, fuori dal perimetro di A3.

Contenuto della guardia:

- **(a)** enumerazione e 57 negativi veri;
- **(b)** 258 varianti generiche su tutte le 71 operazioni: parametro sconosciuto, ogni obbligatorio mancante, ogni parametro colonna o geometria inesistente. Stesso esito, errore o nessun errore, e stesso messaggio;
- **(c)** 105 configurazioni valide A1 (`inference_runtime_cases` CASES+EXTRA) controllate due volte: sullo schema esatto, dove il verdetto deve essere `verified` senza errori; sul canvas inferito con `validate_canvas`, senza errori sul passo.

A questi si aggiungono il controllo che le operazioni dichiarate non garantibili non producano mai errori e il test di costo su 60 passi. In tutto **535 test**: 533 passati e 2 saltati, cioè due configurazioni A1 che l'esecuzione rifiuta anche con righe e che quindi non sono casi positivi.

Raccolta esaustiva prima della correzione: per ogni parametro di ogni operazione sono stati provati 12 valori anomali (`scratchpad/a3dry/fuzz.py`). L'esecuzione reale ne rifiuta **722**. Valida a vuoto ne rifiuta **546 con messaggio identico**. Restano 5 rifiuti con messaggio diverso, tutti valori di tipo sbagliato che l'interfaccia non produce (lista o dict su un parametro scalare): geo_distance `output_column=[{...}]`, geo_line_builder e geo_polygon_builder `group_column=[]` (2 ciascuno). Gli altri rifiuti dipendono dai dati o vengono saltati dalle scorciatoie sul vuoto: non sono errori di Valida.

### Regole rimosse o cambiate nel validatore (P2)

| Regola precedente | Ora |
|---|---|
| `prepare` + frame vuoti senza esecuzione della funzione | Sostituita dall'esecuzione a vuoto completa |
| `formula.invalid` su un campo `formula` presente in operazioni diverse da Formula | **Rimossa**: il runtime ignora il parametro e non lo valuta mai. Una formula invalida su Formula = rifiuto dell'esecuzione |
| `select_column_plan` → `columns.invalid` per lo step Select | **Rimossa**: la rifiuta l'esecuzione con lo stesso messaggio. Resta per l'Output |
| `TRANSFORM_PARAM_REQUIRED` come errore | **Avviso di qualità**: il runtime tollera il parametro mancante; quando lo rifiuta, l'errore è quello dell'esecuzione |
| `column.missing` come errore | **Avviso di qualità**: la maggior parte delle operazioni ignora la colonna; quelle che la rifiutano producono l'errore del runtime |
| `TRANSFORM_OP_UNKNOWN` letto dal JSON con `_transforms_registry` / `_valid_transform_ops` | Registry dell'esecutore; helper eliminati (unico chiamante esterno: un test, riscritto) |
| Uso di tutto l'upstream per operazioni a ingresso singolo | Le regole di schema leggono il primo ingresso, come il runtime |

Restano soltanto regole che non sono vincoli di esecuzione:

- strutturali: `TRANSFORM_OP_MISSING`, `TRANSFORM_OP_UNKNOWN`;
- di schema a monte, che zero righe non possono portare:
  - `TRANSFORM_GEOMETRY_TYPE_INVALID`, che resta solo con un'altra geometria attiva (senza geometria il caso lo rifiuta l'esecuzione);
  - `aggregation.unsupported`, la tabella generata eseguendo con righe;
  - la collisione della rinomina (P5), ora con codice proprio `rename.duplicate`;
- per l'Output: `result_group_plan` e la selezione delle colonne.

### Falsi positivi trovati e corretti

1. **bin**: pandas rifiutava di tagliare un array vuoto anche con parametri validi (`Cannot cut empty array`). `cut_numeric_series` ora verifica bins e labels su un valore neutro del dtype effettivo e restituisce una colonna categoriale vuota.
2. **formula su testo** (`s + 'x'`): su zero righe la conversione numerica delle colonne testo, un'euristica che decide sui valori, sceglieva il numerico. Ora a vuoto vale la prima lettura che l'espressione accetta (numerica, poi testo); con righe non cambia nulla.
3. **dtype `str`**: la mappatura restituiva `object` mentre pandas 3 usa `str`. Corretta l'unica funzione di mappatura; aggiornati i due test che ne fissavano il valore.
4. **rinomina con due ingressi**: le collisioni venivano calcolate su tutti gli ingressi e non solo sul primo.

Correzioni di **falsi negativi**, cioè casi in cui Valida passava e l'esecuzione rifiutava:

- **replace** senza valore da cercare: con righe pandas lo rifiuta sempre (`TypeError`), a vuoto lo saltava. Ora entrambi i percorsi danno «Sostituisci: valore da cercare non configurato.».
- Rimosse le scorciatoie sul vuoto di **dedup_advanced** e **sample**, che non fanno da prova in A1. Aggiornati i due test che verificavano l'identità dell'oggetto restituito.

### Numeri e confronti con HEAD 3c8609fe

- **Dati con righe invariati.** 232 esecuzioni (CASES+EXTRA A1, con righe e a vuoto) passate dal dispatcher reale `_esegui_trasformazione`, HEAD contro working tree. 229 identiche. Unica differenza con righe: `sample {"n": 1}`, che è casuale e differisce anche fra due esecuzioni di HEAD. Due differenze a vuoto, entrambe volute: bin e formula su testo, che prima erano errori spurî e ora hanno uno schema coerente.
- **Esecuzione completa Manipola ed Estrai fino al salvataggio** (sonda `a3-full-save-probe.py`): frame e contratto identici a HEAD, escluso `updated_at`.
- **Costo** (`scratchpad/a3dry/timing.py`, 60 passi in catena, 20 operazioni diverse incluse geo_buffer, centroid e area, mediana di 5 esecuzioni): validatori Manipola **107 ms per 61 nodi**, circa 1,8 ms a passo; `validate_canvas` completo 322 ms. Lo stesso canvas su HEAD: 106 ms, con validatore dichiarativo da 1 ms.
- **`except Exception`:** budget invariato. Nuovo catch nell'esecuzione a vuoto, tolto quello di `_transforms_registry`; `execute` ne ha ancora uno solo.
- **Guardie verdi:** boundary pandas e geopandas (la costruzione del frame è in `dataframes/geodata`), dict tipizzati (`StringMap`, `JsonRows`, `JsonObject`), import locali entro baseline, `test_self_method_calls_exist.py`, ruff F401/F811/F821/F841 sui file toccati. Unico F841 residuo: preesistente in `test_inference_runtime_parity.py`, non introdotto qui.
- **Funzioni tolte:** `_transforms_registry`, `_valid_transform_ops`, `_SELECTION_FIELDS`, `_REGISTRY_PATH` del validatore. Ricerca su `app/` e `tests2/`: unico uso esterno in un monkeypatch di `test_column_plans_parity`, riscritto. `execute_node` è nuovo; `execute` lo delega e conserva firma e log.

Test aggiornati perché codificavano le regole rimosse:

- `test_manipola_node_validator_contract.py`: 5 test aggiornati e 3 aggiunti;
- `test_column_plans_parity.py::test_selection_rejection_comes_from_execution`;
- `test_a1_second_correction.py::test_invalid_aggregation_is_reported`, che ora accetta anche il rifiuto dell'esecuzione;
- `test_inference_runtime_parity.py`: rimosso il ramo «bin vuoto solleva»;
- `test_tx_aggregation_behavior.py` e `test_tx_analysis_behavior.py`: verificano lo schema invece dell'identità dell'oggetto;
- `test_column_types_adapters.py` e `test_type_dispatch_contract.py`: `str → "str"`.

`test_a3_preparation.py` e le parità A1/A2 restano verdi.

### Suite completa

Una sola invocazione completa del comando richiesto, dal venv e da `backend/`, senza modifiche al codice durante l'esecuzione; log `scratchpad/a3dry/a3-dryrun-full.log`. Un primo avvio è stato interrotto da me perché avevo corretto `cut_numeric_series` mentre girava; quel run non conta.

Esito: **15.410 passati, 45 saltati, 1 fallito, 10 warning in 474,70 secondi**, exit code 1. Unico fallimento: `tests2/contract/test_openapi_snapshot.py::test_openapi_snapshot_matches_or_update`, quello atteso per i nuovi endpoint. Snapshot frontend lasciato invariato. Dopo la suite è stato modificato soltanto questo rapporto.

### Stato P1/P2

- **P1 chiuso.** La guardia copre tutte le 71 operazioni del registry: 50 con negativi veri, 8 non garantibili con motivo, 13 senza vincoli con motivo. Su tutte valgono i casi generici e sulle 63 non dichiarate vale il caso positivo.
- **P2 chiuso per i vincoli di esecuzione Manipola.** Nessuna regola di configurazione duplicata: quelle rimaste sono elencate sopra e nessuna è un vincolo di esecuzione.

Residui dichiarati:

- le 8 operazioni non garantibili diventano verificabili a vuoto solo togliendo la scorciatoia insieme all'incertezza A1 corrispondente;
- 5 rifiuti su valori di tipo sbagliato hanno un messaggio diverso fra vuoto e righe;
- per un'operazione non garantibile Valida non emette un issue dedicato: l'esito `not_guaranteed` resta nel `DryRunOutcome`.

Nessun commit, nessuna modifica frontend, nessun sotto-agente.


## Correzione dopo la conferma

Correzione unica dei difetti 1–8 dell'elenco di conferma. **Regola madre:** la preparazione condivisa non rifiuta né cambia ciò che l'esecuzione di HEAD 3c8609fe eseguiva con successo; Valida può essere più severa dell'esecuzione solo come Valida. Tutti i confronti sono contro HEAD estratto con `git archive 3c8609fe backend` in `scratchpad/a3fix/head/` (sonde in `scratchpad/a3fix/`). Nessuna modifica frontend oltre alla rigenerazione dello snapshot OpenAPI; nessun commit; nessun sotto-agente. Il lavoro è stato interrotto da un riavvio del PC a metà: alla ripresa tutti i file modificati sono stati ricompilati (`py_compile`) e le modifiche verificate complete prima di proseguire.

### Regola applicata per punto

| Punto | Regola (per costruzione) | Dove |
|---|---|---|
| 1 Formula | `prepare_formula` fa solo forma degli elementi e gate AST, come HEAD prima di ogni eval. **Nessuna risoluzione dei nomi fuori dal valutatore**: lo spazio dei nomi è esattamente quello dei risolutori reali di `pandas.eval` (colonne, `index`, nome dell'indice, `ilevel_0`, `inf`/`Inf`, funzioni matematiche) e del fallback `eval`. Un nome irrisolto lo rifiuta la valutazione stessa, anche quella a vuoto di Valida. Rimosso `dataframe_math_names` (unico chiamante). | `transforms/formula.py`, `dataframes/tabular.py` |
| 2 Zero righe | Ripristinate le scorciatoie di HEAD su `sample` e `dedup_advanced` (`len(df)==0 → df`) e il comportamento HEAD di `replace(None)` su zero righe (i tre file sono identici a HEAD). `sample` e `dedup_advanced` passano in `VUOTO_NON_GARANTIBILE` (esito `not_guaranteed`, mai falso errore). `replace` va nella nuova tabella di test `SOLO_REGOLA_VALIDA`: il suo unico rifiuto (valore da cercare mancante) lo copre `TRANSFORM_PARAM_REQUIRED`. Correzioni volute mantenute (HEAD falliva, ora riesce): `bin` e formula su testo a zero righe. | `transforms/analysis.py`, `aggregation.py`, `cleansing.py`, `dry_run.py` |
| 3 Arricchisci HTTP | `prepare_api_request` tolto dal costruttore di `EnrichmentExecutor`: a runtime metodo/body/dialetto CSV non supportati restano `RowError` per riga (come HEAD). Valida (`validate_canvas`) e preflight chiamano esplicitamente la stessa funzione e ne fanno un errore. Per il generator il runtime esegue solo i gate di HEAD (`prepare_generator_request`: URL, async+paginazione); i controlli aggiuntivi sono in `prepare_generator_preflight`, usata solo dal preflight. | `enrichment_service.py`, `request_preparation.py`, `canvas_preflight.py`, `canvas_validation.py` |
| 4 Severità Valida | `column.missing` e `TRANSFORM_PARAM_REQUIRED` tornano **errori** come HEAD (avviso con nomi a monte incerti). Restano avvisi solo dove il runtime dà davvero il risultato voluto: parametro obbligatorio con default non vuoto nel registry che il runtime applica (`_runtime_default`), e la selezione Select che per contratto salta le colonne assenti. Le due regole sono dichiarate solo-Valida (`DECLARATIVE_ERROR_CODES`). | `manipola/node_validator.py` |
| 5 validate/estimate | Senza `node_id` né `canvas` (il frontend attuale): codice e risposta di HEAD, compresi `required_mapping_error`, `generator_validate_response`, `build_validate_response`/`build_estimate_response` (`enrichment_preflight.py` identico a HEAD) e campi di risposta (i nuovi `status`/`reason` non vengono emessi: `response_model_exclude_unset`). Con `node_id`/`canvas`: `required_mapping_error` di HEAD, poi preflight sul solo sottografo nodo + antenati; nodo non univoco, assente o non preparato → 422 tipizzato con `reason`, mai `next()` senza default. | `arricchisci/router.py` |
| 6 Errori 500 | Un unico mappatore: tabella dichiarativa `PREPARATION_ERROR_FAMILIES` (sorgente non disponibile → 400 `source_unavailable`; I/O, Unicode, parsing pandas, DB, memoria, RuntimeError → 422 `data_unreadable`; ValueError/KeyError/TypeError/... → 422 `invalid_configuration`) e un solo helper HTTP `raise_preparation_failure`, usato da distinct, `/preflight` e percorso per nodo di validate/estimate. Anche la costruzione del canvas del preflight è dentro il mappatore. Nessun `except Exception` aggiunto (budget invariato). | `manipola/canvas_data_preparation.py`, `projects/router.py`, `arricchisci/router.py` |
| 7 Versione canvas | Token opaco **`canvas_version`** = `c1:` + sha256 del JSON canonico del canvas *persistito*: cambia solo quando cambia il canvas. Valida, invalidazione a valle e metadati toccano `updated_at` ma non il token. Confronto per **uguaglianza esatta** nella stessa transazione dell'UPDATE, riga bloccata `FOR UPDATE`; il nuovo token si calcola rileggendo il canvas scritto. `base_updated_at` sostituito da `base_canvas_version` (PUT canvas e ripristino); le risposte restituiscono `canvas_version` e anche `updated_at`; il GET del progetto espone `canvas_version`. 409 con `reason: canvas_version_conflict` e il token corrente. Anche l'impronta del preflight Arricchisci usa il token invece di `updated_at` (stesso falso positivo). | `canvas_revision.py`, `db.py`, `canvas_service.py`, `service.py`, `projects/router.py`, `arricchisci/router.py` |
| 8 Arricchisci completo | Verificato senza modifiche: `model_dump(exclude_unset=True)`/`_plain_dict` non altera l'esecuzione. | sonda `p_arr.py` |

**Campo per la F3 (frontend non modificato ora):** l'editor legge `canvas_version` dal GET `/projects/{id}` e lo rimanda tale e quale come `base_canvas_version` nel body di PUT `/projects/{id}/canvas` e POST `/projects/{id}/versions/{vid}/restore`; dopo ogni scrittura usa il `canvas_version` restituito. Senza il campo la scrittura resta quella compatibile, senza controllo.

### Avvisi che restano (punto 4) e perché

- `TRANSFORM_PARAM_REQUIRED` con default del registry applicato dal runtime: `formula.new_column` (`calculated`), `concat_columns.output_column`, `geo_buffer.distance` (100), `geo_simplify.tolerance`, `geo_reproject.target_crs` (EPSG:4326), `type_cast.target_type` (`str`), `string_pad.width` (5). La guardia verifica sul runtime con righe che l'uscita senza parametro sia identica a quella con il default esplicito.
- `column.missing` nella Select: il contratto della selezione salta le colonne assenti (come HEAD).
- Qualunque regola con nomi a monte `indeterminate` (come HEAD).
- Resta la rimozione (terza sessione) di `formula.invalid` su un campo `formula` in operazioni diverse da Formula: parametro sconosciuto all'operazione, mai valutato; l'operazione dà il suo risultato normale.

Nessun caso in cui HEAD dava errore e il runtime fa un no-op è diventato avviso: lo verifica `test_head_validation_errors_are_not_demoted` sull'intero corpus.

### Test aggiunti o cambiati

- `tests2/contracts/a3_runtime_corpus.py` (nuovo): corpus deterministico di **728 voci** su tutte le 71 operazioni: configurazioni valide A1, ogni parametro opzionale significativo (colonne esistenti come `stratify_column`, ogni opzione dei select, booleani, numeri) e tutti insieme, varianti generiche (obbligatorio mancante, colonna assente), **88 formule × 2 indici** (anonimo e con nome `idx`): test esistenti, `a + inf`, `a * Inf`, `index * 2`, `idx + 1`, `ilevel_0`, costanti e funzioni matematiche.
- `tests2/contracts/a3_head_outcomes.json` + `generate_a3_head_outcomes.py` (nuovi): **fixture generata sul riferimento 3c8609fe**, mai dal working tree (procedura nel docstring).
- `tests2/contracts/test_a3_head_parity.py` (nuovo, **2.185 test**): per ogni voce, con righe e su zero righe, HEAD riuscita ⇒ WT riuscita con stesse colonne, dtype, righe e valori; HEAD rifiuto ⇒ stesso messaggio, salvo `CORREZIONI_VOLUTE` (bin, formula su testo a vuoto); errori di Valida di HEAD mai declassati salvo default del registry. Prova di sensibilità: reintroducendo il controllo dei nomi della formula falliscono oltre 40 casi (`inf`, `Inf`, `index`, `idx`, funzioni); togliendo la scorciatoia di `sample` falliscono i 3 casi stratificati a vuoto.
- `tests2/contracts/test_a3_dry_run_parity.py`: (b) riscritto secondo la regola 4 (rifiuto del runtime ⇒ errore di Valida; no-op silenzioso o risultato senza il parametro ⇒ errore; avviso solo se il runtime applica il default, verificato eseguendo); tabella `SOLO_REGOLA_VALIDA` e relativo test; `sample`/`dedup_advanced` fra i non garantibili. **529 passati, 2 saltati.**
- `tests2/contracts/test_a3_after_confirmation.py` (nuovo, **32 test**): punto 3 (tre configurazioni HTTP: costruttore senza rifiuto, RowError `request_config_error` per riga, stesso messaggio in `prepare_api_request`, preflight e `validate_canvas`); punto 5 (percorso per nodo limitato a nodo e antenati con un altro ramo rotto, 422 tipizzati per nodo assente/non univoco/rotto, ramo `required_mapping_error`); punto 6 (enumerazione di tutte le famiglie della tabella più parsing pandas e Unicode → 4xx con `reason`).
- `tests2/contracts/test_a3_preparation.py`: test della versione riscritti su una riga `projects` in memoria con transazione: conflitto atomico che non tocca il canvas; Valida, invalidazione e metadati fra apertura e salvataggio senza 409; due schede (la seconda 409); round-trip del token; indipendenza del token dall'ordine delle chiavi; ripristino che inoltra base e token.
- Aggiornati: `test_manipola_node_validator_contract.py` (riferimento assente = errore, come HEAD), `test_a3_completion.py` (stima standalone legacy = forma HEAD), `test_project_router_typed_boundary_contract.py` (campo `canvas_version`).

### Confronti con HEAD (sonde in `scratchpad/a3fix/`)

- **Runtime Manipola, corpus 728 voci:** con righe 728/728 identiche (colonne, dtype, righe, valori, messaggi di errore); su zero righe 721 identiche e 7 differenze tutte nel verso consentito (HEAD falliva, ora riesce): bin (4) e formula su testo (3).
- **Arricchisci per riga** (`p_arr.py`): metodo TRACE, body xml, delimitatore CSV, batch con TRACE, configurazione valida: risultati, RowError e numero di chiamate identici a HEAD.
- **Esecuzione Arricchisci completa** (punto 8; generator + arricchimento per riga, API simulate, fino a salvataggio CSV e contratto): DataFrame, byte CSV e contratto **identici a HEAD**.
- **validate/estimate legacy** (`p_compat.py`, FastAPI TestClient): 90 scenari (3 API × 3 frame × 5 corpi × 2 endpoint, con due nodi della stessa API e progetto non salvato): **90/90 risposte identiche a HEAD** (codice HTTP e corpo).
- **Estrai e Manipola complete fino al salvataggio** (`a3-full-save-probe.py` su HEAD e WT): frame e contratti identici, escluso `updated_at`.

### Funzioni tolte o rinominate e chiamanti

- `dataframe_math_names`: unico chiamante `prepare_formula`, rimosso con esso.
- `base_updated_at` → `base_canvas_version` in `update_project_row`, `update_canvas`, `restore_canvas_version` (canvas_service e service) e nei body; `revision_datetime` rimossa. Ricerca su `app/` e `tests2/`: nessun residuo.
- `prepare_generator_request` ridotta ai gate di HEAD; nuova `prepare_generator_preflight` per il preflight.
- `build_validate_response`/`build_estimate_response` riportate a HEAD (unico chiamante il router legacy).
- `PREPARATION_ERRORS` riesportato da `projects.router` per non creare l'arco `arricchisci → manipola` (guardia della matrice delle dipendenze).
- Guardie verdi nelle verifiche mirate: `test_self_method_calls_exist.py`, matrice dipendenze, monkeypatch ineffettivi, budget `except Exception`. Ruff F401/F811/F821/F841 sui file toccati verde, salvo due F841 preesistenti a HEAD (`schema_reshape_handlers.py:164`, `test_inference_runtime_parity.py:85`).

### OpenAPI

Snapshot rigenerato con `UPDATE_OPENAPI_SNAPSHOT=1 pytest tests2/contract/test_openapi_snapshot.py` (`frontend/tests/fixtures/openapi.snapshot.json`, unico file frontend toccato): `base_canvas_version` in `CanvasWithPlugins` e `CanvasRestoreRequest`, `status`/`reason` facoltativi nelle risposte validate/estimate, insieme agli endpoint A3 già presenti.

### Suite unica

`pytest tests2/{manipola,projects,contracts,contract,shared,orchestrator,estrai,arricchisci,eseguibili,services}`: **17.626 passati, 45 saltati, 0 falliti** (6 min 46 s).

## Conferma finale

Giro di conferma unico sulla correzione: punti 1-8 chiusi, dati di esecuzione identici a HEAD, fixture `a3_head_outcomes.json` rigenerata da un `git archive 3c8609fe` nuovo identica byte per byte, token `canvas_version` coerente fra GET, PUT e ripristino, frontend committato compatibile. Un difetto residuo, corretto: `_runtime_default` declassava ad avviso anche un parametro obbligatorio **presente ma vuoto** (`""`/`null`, come lo manda l'interfaccia svuotando un campo), mentre il runtime applica il default solo con la chiave assente (`string_pad.width`, `formula.new_column`, `concat_columns.output_column`). Regola: avviso solo con la chiave assente e default non vuoto; la guardia (b) enumera ora anche i valori vuoti di ogni parametro obbligatorio (prova di sensibilità: con la regola precedente falliscono 8 casi). Suite completa: **17.808 passati, 45 saltati, 0 falliti**.

## Controllo dal vivo (server, 406655be)

Sul canvas reale di SC_scheda_manipola (copia e originale) Valida dava due errori bloccanti:

1. **Falso errore sulla Formula** `ct_date_due.dt.year * 10000 + ...`: `canvas_type_to_pandas_dtype` riconosceva solo il vocabolario minimo, quindi `datetime` (tipo dell'inferenza) diventava `object` e `.dt` falliva nell'esecuzione a vuoto. Regola: tipo -> famiglia con la stessa tabella dell'inferenza (`type_families.json`) -> dtype. Test di enumerazione su tutti i tipi della tabella (`test_a3_live_findings.py`).
2. **Rinomina con nomi duplicati**: per decisione dell'utente diventa un **avviso** (Esegui non si blocca; il messaggio dice che la seconda colonna esce con il suffisso `_2`).

E una differenza previsione/file: con la selezione dell'Output salvata per nome ("Comune" una volta) e due colonne "Comune" a monte, l'esecuzione (`df[[nomi]]`) le prende entrambe e salva `Comune`/`Comune_2`, la previsione ne teneva una. Regola unica `expand_column_plan` (shared/execution/column_selection.py), usata dall'Output e da Seleziona. Test: previsione == file salvato su tre selezioni. Prova di sensibilita': con il codice precedente 21 casi falliscono. Suite completa: **17.840 passati, 45 saltati, 0 falliti**.

Fuori perimetro (Fase 5): 49 avvisi `JOIN_COLUMN_NOT_FOUND` preesistenti, da campi `source_column`/`target_column` rimasti su archi del canvas.

Avvisi `JOIN_COLUMN_NOT_FOUND` falsi (visti dal vivo su Estrai e Manipola): dalla A1 il controllo usava `names_status` invece dello stato complessivo e quindi non veniva piu' saltato; confrontava le colonne dell'arco con le sole colonne VISIBILI della tabella. Regola: il controllo usa le colonne che l'esecuzione usa. Solo Estrai legge i campi di join dell'arco (`estrai/query_joins.py`, alias.colonna fisica): si confrontano con TUTTE le colonne fisiche del nodo tabella; negli altri progetti i campi sono residui non letti dall'esecuzione e non si controllano. Test parametrico per tipo di progetto e visibilita'. Suite completa: **17.845 passati, 45 saltati, 0 falliti**.

## F3 — correzione dopo la conferma

Correzione unica sull'elenco del giro di conferma F3 (9 punti). Una regola per punto, chiusa per costruzione e coperta da un test che la enumera.

### Regola applicata per punto

1. **Lookup «confronta come testo»** (decisione utente). UNA funzione, `lookup_value_text` (`backend/app/shared/execution/lookup_mapping.py`), dà la forma di testo di un valore; la usano l'esecuzione (`op_lookup`: chiavi della mappa con `lookup_text_mapping` e valori della colonna, qualunque tipo), `POST /distinct` e `lookup_is_numeric`. Forma unica, documentata nel modulo: null -> `None` (resta null, non corrisponde a nessuna chiave, mai "None"/"nan"; distinct risponde `value: null, is_null: true`, un solo null anche se la colonna ha None e NaN); testo invariato; booleano `True`/`False`; intero `5`; decimale intero `5` (5.0 -> "5"); decimale `2.5`; data/ora a mezzanotte senza fuso `2024-01-05`; data/ora `2024-01-05 10:30:00`; data `2024-01-05`. Il modulo resta senza pandas (riconoscimento per protocollo: `numbers`, `datetime`). La conversione numerica dell'USCITA (null/value numerici) non è un confronto e resta quella storica: i dtype del risultato non cambiano.
2. **Le righe di «Riempi» sono proposte neutre.** `mergePresentValues` aggiunge `{from, to: '', proposed: true}` (segnaposto "da tradurre"); `toSavedRows` esclude le proposte finché «diventa» non è utilizzabile (`isPendingProposal`); cancellare «diventa» riporta la riga a proposta. Riempire non cambia mai la mappa salvata, quindi né il risultato né `lookup_is_numeric`, per ogni «Valori non mappati». Testi aggiornati (`utils/lookupFill.js`, `ValueMappingInput.jsx`, glossario `fill_values`).
3. **Snapshot unico della scrittura.** `writeConditioned` non rilegge lo store: progetto, canvas e base arrivano dallo snapshot del chiamante, preso prima di ogni attesa (colonne derivate). Il ripristino prende progetto e base insieme, dopo l'attesa del salvataggio in volo, e non scrive se il progetto è cambiato.
4. **Risposta senza token** (`tokenAfterWrite`, righe nuove della tabella in `utils/canvasVersion.js`). Regola sicura: si rilegge il progetto (GET). GET senza token = server che non versiona, scrittura compatibile come all'apertura; GET con token adottato SOLO se descrive la stessa scrittura (stesso `updated_at` della risposta); altrimenti, o se il GET fallisce, si tiene il token precedente: superato, la scrittura dopo dà un 409 visibile con le scelte. Mai controllo spento in silenzio, mai sovrascrittura silenziosa.
5. **Stima fallita: «Riprova» e messaggi in italiano.** `BeforeRunModal` ha una sola funzione di stima (`runEstimate`) usata dal cambio degli input e da «Riprova» (con o senza input runtime). Messaggi con `requestErrorMessage` (nuovo in `utils/errorMessages.js`: messaggio umano del backend se c'è, altrimenti `humanizeError`; mai "Network Error" o "Request failed..."), usato da stima, avvio e «Riempi».
6. **Finestra aperta = runState 'starting'.** L'unico punto che apre «Prima di avviare» è `openWindow` in `PipelineRunner`, che mette 'starting' nello stesso momento, anche dopo un 409 `preparation_changed` quando l'esecuzione rifiutata ha già riportato lo stato a 'idle'.
7. **Merge sulle righe correnti.** `useDraftList.update` accetta una funzione `(correnti) => nuove` che riceve le righe correnti al momento della chiamata; «Riempi» applica il merge al ritorno della lettura, le modifiche fatte durante l'attesa restano.
8. **Valore utilizzabile, regola unica.** `isUsableValue` (testo non vuoto dopo il trim) usata da `mergePresentValues`, `isEmptyRow`, validazione e `toSavedRows`. I valori vuoti o di soli spazi non diventano righe: si contano (valori e righe) e si dicono all'utente come il null.
9. **Test non tautologico.** 'Valida / Anteprima' passa davvero da `PipelineRunner` (onValidate e previewWithValidation, «Salva e continua») contro il server finto che segue la regola del backend (Valida non cambia `canvas_version`); aggiunti i test dei punti 3 e 4.

### Test aggiunti o cambiati

- `backend/tests2/manipola/unit/test_lookup_compare_as_text.py` (107 casi): tabella della forma unica (27 valori), mappa con chiavi null o non testo, `lookup_is_numeric`; **parità con HEAD 1c654091 sulle colonne di testo** (corpus `lookup_text_corpus.py`, 4.320 configurazioni: 3 frame x 6 famiglie di valori della mappa, comprese virgola decimale, vuoto, null, numeri veri x 3 forme della mappa x unmatched assente/""/keep/null/value x 4 default x 4 colonne di uscita; fixture `lookup_text_head_outcomes.json` generata su `git archive 1c654091`, procedura nel modulo: 0 differenze); colonne int/float/Int64/date/datetime/bool/object misto x 4 unmatched x 2 uscite: le righe si applicano; il null non corrisponde mai; giro completo distinct -> mappa -> esecuzione su 10 tipi di colonna.
- `backend/tests2/contracts/test_a3_head_parity.py`: tabella `CORREZIONE_VOLUTA` (esito riuscito su HEAD che ora cambia per decisione esplicita, con motivo; la guardia verifica che la differenza ci sia davvero e che la voce sia nel corpus): `valid:lookup` su colonna intera con chiave "1".
- `frontend/tests/components/LookupFill.contract.test.jsx`: proposte neutre (nessuna scrittura finché non si compila «diventa»; cancellarlo ripristina il canvas); per ogni «Valori non mappati» (assente/keep/null/value) canvas serializzato identico dopo Riempi; merge sulle righe correnti durante la lettura; enumerazione della regola del valore utilizzabile (10 forme, 4 consumatori); vuoti contati e detti; errore di rete in italiano.
- `frontend/tests/components/CanvasVersion.contract.test.jsx`: righe nuove della tabella «cambio progetto durante il salvataggio», «risposta senza token, GET della stessa scrittura», «risposta senza token, GET non dimostra la scrittura»; «progetto senza canvas_version» ora con GET senza token; «Valida / Anteprima» dal PipelineRunner vero. Prova di sensibilità del punto 3: con la base riletta dallo store il test fallisce.
- `frontend/tests/components/BeforeRun.contract.test.jsx`: stima fallita rete/4xx/5xx senza input runtime -> messaggio in italiano, «Riprova», Avvia di nuovo attivo; enumerazione dei 3 percorsi che aprono la finestra (motivi, 409 dopo avvio diretto, 409 dalla finestra) con un `onExecute` che fa come useExecution (running -> idle): sempre 'starting', Esegui non riparte, Annulla torna a 'idle'; guardia statica: un solo `setRequest(` di apertura, dentro `openWindow`.
- `DraftTabs`, `SaveRules`, `BeforeRun`: il server finto che non versiona risponde anche al GET (senza token), come chiede la regola del punto 4.

### Osservazione fuori perimetro (raccolta, non corretta)

La previsione dei tipi del Lookup era già approssimata su HEAD e resta tale: su una matrice di 5 tipi di colonna x 6 mappe x 4 unmatched x 2 uscite x righe/vuoto, 380 casi danno un tipo previsto diverso da quello eseguito, **identici su HEAD e sul working tree** (la correzione non cambia nessun tipo). I tipi del Lookup sono dichiarati `indeterminate` (`schema_inference.py`), quindi la parità A1 non li verifica; renderli esatti è un lavoro a sé (Fase 5).

### Numeri

- `npx eslint src tests`: 0 problemi.
- `npx vitest run`: **142 file, 3.204 test passati**, 0 falliti.
- `npx vite build`: riuscito.
- Suite backend unica (`tests2/{manipola,projects,contracts,contract,shared,orchestrator,estrai,arricchisci,eseguibili,services}`): **17.952 passati, 45 saltati, 0 falliti** (11 min 10 s). OpenAPI invariato (distinct risponde un `JsonObject`; cambia solo `value: null` per il null).

### Seconda correzione

Correzione unica sull'elenco del secondo giro di conferma F3 (7 punti; sonde in `scratchpad/f3conf2/`: `probe_lk.py` + `cmp.py` contro `head/` = `git archive 1c654091`, `probe.test.jsx`). Una regola per punto.

1. **+ 2. Confronto scelto dal tipo della colonna, UNA funzione** (`lookup_match_as_text(dtype)` in `backend/app/shared/execution/lookup_mapping.py`, usata da `op_lookup`). Colonna di testo (object, str, string, category e ogni dtype con `kind` fuori da `iufcbmM`, es. period): **esattamente il percorso di HEAD 1c654091** (`map`/`isin` con le chiavi originali, nessuna conversione), quindi le chiavi JSON numeriche/booleane/null (`{"from": 5}`, `{"from": true}`, `{"from": null}`, collisione 1/True in entrambi gli ordini) si comportano come prima. Colonna numerica, booleana, data/ora o durata (anche nullable Int64/Float64/boolean e date con fuso): «confronta come testo» con `lookup_value_text` (decisione utente). distinct resta testo. Conseguenza dichiarata nel modulo: in una colonna object il numero 5 non corrisponde alla chiave "5" (come a HEAD). `lookup_is_numeric` decide come a HEAD su ogni valore JSON (test di enumerazione 22 x 22 x 5 unmatched).
3. **Valori temporali numpy/pandas prima dei numeri.** `lookup_is_null` (pubblico, UNICO riconoscimento del null: `lookup_value_text` è `None` se e solo se `lookup_is_null`, quindi lo condivide distinct) riconosce anche `np.datetime64('NaT')`/`np.timedelta64('NaT')`; `lookup_value_text` tratta le durate (np.timedelta64, che per numpy è un intero, `datetime.timedelta`, `pd.Timedelta`) con la forma di `str(pd.Timedelta)` (`5 days 00:00:00`, `-1 days +23:59:59`) e `np.datetime64` come le date (forma di `pd.Timestamp`), prima del ramo numerico: niente più `int(NaT)` -> TypeError né 5 giorni -> "5". Il modulo resta senza pandas (confine di `shared`, 4 guardie di import): la forma si calcola dai nanosecondi e un test la confronta con pandas su 13 durate e 7 date numpy (unità D/W/h/ms/us/ns/generica, negative, frazioni, nanosecondi).
4. **Valore utilizzabile = stringa non vuota, senza trim** (`isUsableValue` in `utils/lookupFill.js`), perché il backend confronta le chiavi esattamente. Una sola regola per merge, riga vuota, validazione e salvataggio: una mappa salvata con chiave " " resta modificabile e salvabile; Riempi aggiunge anche " " se il server lo restituisce; solo il testo vuoto si conta e si dice come il null.
5. **`useDraftList.update` legge solo ref correnti**: righe, chiave dell'ultimo salvato (scritto da qui o arrivato da fuori) e `validate`/`toSaved`/`write` dell'ultimo render; nessuna scrittura se il salvato non cambia rispetto all'ultimo salvato. Un `update` preso al clic e chiamato dopo l'attesa di Riempi non riscrive più la stessa mappa.
6. **Proposte legate alla colonna.** Ogni proposta porta `source` (`fillSourceKey`: id della colonna, altrimenti il nome); al cambio di colonna del Lookup `dropOrphanProposals` toglie le proposte non compilate (le righe compilate restano, nessuna scrittura); una lettura che torna dopo il cambio non aggiunge niente e lo dice. Documentato in `lookupFill.js`: le proposte non compilate vivono solo nelle righe del pannello, quindi non entrano nella configurazione, né nella bozza locale né nella copia di sicurezza (sono suggerimenti, si rigenerano con Riempi).
7. **'starting' per ogni preparazione dell'avvio.** In `PipelineRunner` l'UNICO punto che mette 'starting' è `beginPreparation`, chiamato all'inizio di Esegui, all'inizio della stima rifatta dopo un 409 `preparation_changed` (avvio diretto o dalla finestra, quando l'esecuzione rifiutata ha già riportato 'idle') e da `openWindow`. Lo stato resta 'starting' fino all'apertura della finestra o all'avvio ('running').

#### Test aggiunti o cambiati

- `backend/tests2/manipola/unit/lookup_text_corpus.py` esteso: frame `string`, `category`, object misto (testo, 5, 5.0, True, 1, None, NaN, False, 0, 2.5); famiglie di mappa `numeric_keys`, `bool_keys`, `collision_1_true`, `collision_true_1`, `null_key`, `null_key_numeric`, in forma dizionario, `from/to`, `old_name/new_name` (mappe come liste di coppie). **17.280 configurazioni** (6 frame x 12 famiglie x 3 forme x 5 unmatched x 4 default x 4 uscite); fixture `lookup_text_head_outcomes.json` rigenerata su `git archive 1c654091`: **0 differenze**.
- `backend/tests2/manipola/unit/test_lookup_compare_as_text.py` (224 casi): forma unica con 9 righe temporali nuove; `lookup_is_null` = (`lookup_value_text` is None) su tutta la tabella; tabella dtype -> confronto (16 dtype); forma delle durate e delle date numpy uguale a quella di pandas (20 casi); `lookup_is_numeric` uguale al riferimento HEAD su valori JSON; colonne tipizzate + boolean/Float64/date con fuso/durate (object misto tolto: è una colonna di testo, ora coperta dal corpus e da `test_object_column_keeps_head_path`); giro distinct -> mappa -> esecuzione anche su string e category; distinct su colonna object con np.timedelta64/np.datetime64/NaT.
- Sonda `probe_lk.py`/`cmp.py` (2.100 combinazioni contro HEAD): differenze solo sulle colonne bool/float/int (volute), 0 sulle colonne di testo (spazi, numeri come testo, "nan"/"None", "True", object misto, category, string).
- `frontend/tests/components/LookupFill.contract.test.jsx` (35 casi): enumerazione del valore utilizzabile riscritta (" ", "   ", "\t" utilizzabili); mappa legacy con chiave " " modificabile senza errore; Riempi aggiunge "   " e conta solo il vuoto; Riempi + modifiche durante la lettura con lo store vero -> esattamente 3 scritture, nessuna al ritorno; cambio colonna -> proposte non compilate tolte, compilate restano, nessuna scrittura; lettura tornata dopo il cambio -> niente aggiunto; `dropOrphanProposals`/`fillSourceKey`. Prove di sensibilità: con il confronto sulla `savedKey` del render il test delle scritture fallisce; senza `dropOrphanProposals` fallisce quello del cambio colonna.
- `frontend/tests/components/BeforeRun.contract.test.jsx`: enumerazione delle 3 preparazioni (Esegui; stima rifatta dopo 409 da avvio diretto; dalla finestra) con la stima tenuta in attesa: niente finestra, 'starting', Esegui non riparte, poi finestra aperta e 'starting'; guardia statica: un solo `setRunState('starting')`, in `beginPreparation`, chiamato da `openWindow`. Prova di sensibilità: senza `beginPreparation` nel ramo 409 falliscono i 2 percorsi 409.

#### Numeri

- `npx eslint src tests`: 0 problemi.
- `npx vitest run`: **142 file, 3.212 test passati**, 0 falliti.
- `npx vite build`: riuscito.
- Suite backend unica (stesse 10 cartelle): **18.069 passati, 45 saltati, 0 falliti** (6 min 41 s). Un primo giro aveva preso 4 guardie di confine (`pandas` importato in `shared`, anche locale): la forma di durate e date numpy è stata riscritta senza pandas e confrontata con pandas nei test. Un secondo giro si è fermato al 79% per il crash sporadico dell'interprete già noto su questa macchina (0x80000003 in `test_architecture_legacy_patterns`, file non toccato); il terzo è quello riportato. OpenAPI invariato.
