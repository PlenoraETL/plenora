# Blocco A — raccolta difetti backend (Codex, 04/11/2026)

Sola lettura, analisi del codice (nessuna riproduzione sperimentale). Percorsi relativi a `backend/`.

La causa è confermata: **non esiste un unico contratto eseguibile per colonne e validazione**. Alcune funzioni sono condivise, ma inferenza, preparazione dei parametri, esecuzione e pubblicazione applicano ancora regole diverse.

Nessuna modifica al repository. Branch verificato; `git status --short` non mostra modifiche. Il test mirato non è partito perché il sandbox non dispone di temporanei scrivibili; inoltre `tests2/conftest.py:30` forza temporanei nel repository. Il rapporto è quindi basato sull’analisi del codice, non su una riproduzione del canvas da 295.782 righe. **Non posso certificare l’esaurimento sperimentale della classe**, né attribuire individualmente le sei colonne di differenza senza il canvas effettivo.

Tutti i percorsi sotto sono relativi a `C:\Users\Marco\Desktop\plenora\backend`. Gravità: **alta** = colonne/dati errati, esecuzione bloccata o validazione fuorviante; **media** = tipi, ordine o metadati incoerenti.

**CLASSE 1 — Colonne previste diverse dalle colonne eseguite**

1. **Join legacy: chiavi destre aggiunte dall’inferenza ma eliminate dall’esecuzione — alta.**  
   Runtime: `app/core/manipola/transforms/joins.py:629` elimina la chiave destra quando ha nome diverso dalla corrispondente sinistra, salvo che appartenga anche alle chiavi sinistre. Inferenza: `app/shared/execution/schema_column_plans.py:6` esclude soltanto le chiavi omonime accoppiate.  
   Scenario: `left.id = right.codice`; lo schema contiene `codice`, il risultato legacy no. Le trasformazioni successive possono propagare e suffissare questa colonna fantasma.

2. **Join legacy: deduplicazione e collisioni non simulate — alta.**  
   Runtime: `joins.py:199` rinomina occorrenze duplicate con `_1`, `_2`, `_dup2`; `:277` applica i suffissi; `:303` elimina duplicati residui; `:541` usa anche il suffisso di merge `_dup`.  
   Inferenza: `schema_column_plans.py:12` applica direttamente `_L`/`_R`, conservando le occorrenze. `app/core/projects/schema_join_handlers.py:43` conserva esplicitamente i duplicati.  
   Scenario: duplicati o collisioni fra una chiave già suffissata e una colonna da suffissare producono nomi e quantità diversi. È compatibile con le catene `pcv_cd_id_L_L_…` segnalate.

3. **Anche il Join nuovo non condivide tutta la preparazione delle colonne — alta nei casi duplicati.**  
   `joins.py:159` e `schema_column_plans.py:27` condividono `join_column_plan`; tuttavia il runtime deduplica successivamente le colonne a `joins.py:222`, mentre `join_output_plan` conserva le occorrenze sorgenti.  
   Scenario: input con nomi duplicati; il piano è calcolato prima della rinomina runtime e non descrive necessariamente il frame ottenuto.

4. **Join: tipi delle chiavi non aggiornati nell’inferenza — media.**  
   `joins.py:225` converte le chiavi discordanti a stringa o numerico. `schema_join_handlers.py:46` copia il tipo sorgente.  
   Scenario: chiave intera a sinistra e stringa a destra; il risultato contiene stringhe ma lo schema mantiene il tipo intero.

5. **Avvisi Join sugli edge: confronto con lo schema sbagliato — alta.**  
   `app/core/projects/canvas_validation.py:440` confronta `source_column` con l’output della sorgente e `:447` confronta `target_column` con **l’output del target**. Non distingue adeguatamente i riferimenti di ingresso di un transform dai nomi prodotti dopo il Join; non risolve qui gli ID.  
   Scenario: `target_column=id` è valida nell’ingresso destro, ma l’output del target l’ha rimossa/rinominata. Il validatore segnala una colonna mancante pur con runtime valido. È una spiegazione strutturale dei 42 avvisi, non la verifica individuale di quei 42 casi.

6. **`multi_join` eseguito ma senza handler di inferenza — alta.**  
   Runtime: `app/core/manipola/executor.py:995`, con chiavi comuni, coppie esplicite e merge successivi. Registry dell’inferenza: `app/core/projects/schema_inference.py:232`, senza `multi_join`.  
   Risultato: schema vuoto, potenzialmente considerato certo, mentre il nodo produce colonne.

7. **`staticValues` e `datasource_node` senza handler di inferenza — alta.**  
   Il registry precedente non comprende questi nodi. Il runtime li carica rispettivamente in `executor.py:722` e `app/core/manipola/datasource_node_loading.py:70`; Arricchisci gestisce `staticValues` in `app/core/arricchisci/canvas_orchestrator.py:294`.  
   Scenario: una sorgente statica valida produce colonne che il validatore e l’Output non vedono.

8. **Sorgenti file: alias, selezione vuota e metadati obsoleti divergono — alta.**  
   Inferenza: `schema_inference.py:486` usa `name`, ignora l’alias e scarta le colonne invisibili. Runtime: `app/core/dataframes/tabular.py:315` applica alias, ma restituisce **tutto il frame** quando la lista è vuota, tutte le colonne sono invisibili oppure nessun nome configurato esiste. Chiamanti: `source_loading.py:244`, `datasource_node_loading.py:243`.  
   Scenario: `a` con alias `b` viene previsto come `a` ed eseguito come `b`; metadata completamente obsoleti possono prevedere colonne inesistenti e lasciare passare quelle reali.

9. **`project_source`: selezione e alias non derivati dallo stesso piano — alta.**  
   Inferenza: `schema_inference.py:455` usa i nomi fisici del contratto e filtra per ID/nomi visibili; non riproduce la proiezione con alias e la successiva deduplicazione. Runtime: `source_loading.py:117` applica `filter_visible_columns`, poi `rename_duplicate_columns`.  
   Inoltre una selezione senza nomi/ID visibili può lasciare nell’inferenza tutte le colonne del contratto.

10. **`select_columns`: piano condiviso, ma ingressi duplicati trattati diversamente — alta.**  
    Runtime: `transforms/columns.py:300` passa tutte le colonne reali a `select_column_plan`. Inferenza: `schema_transform_handlers.py:837` costruisce prima un dizionario per nome, perdendo duplicati e relativa identità. Il validatore usa un insieme di nomi in `node_validator.py:95`.  
    Scenario: input duplicato; runtime può rifiutare i nomi non univoci, mentre inferenza e validazione li hanno già collassati.

11. **Output: supporto agli ID presente, ma mappa non preparata — alta.**  
    `app/core/dataframes/output_columns.py:8` accetta già `input_columns` e risolve ID→nome. Manipola lo chiama a `executor.py:389` e `:595`, leggendo `result.inputColumns`.  
    La mappa viene però costruita a `executor.py:892` e inserita in una **copia locale del transform** a `:910`; non viene trasferita al Result utilizzato nella proiezione finale. Arricchisci legge analogamente `inputColumns` a `canvas_orchestrator.py:310`, senza costruirla in quel ramo.  
    Scenario: nome salvato `vecchio`, ID ancora valido, upstream rinominato `nuovo`: la configurazione non viene applicata alla colonna corretta. Precisazione: `skip_missing=True` salta la voce configurata; il piano può poi riaggiungere `nuovo` come colonna upstream non configurata. Quindi possono perdersi soprattutto esclusione, etichetta e ordine, non necessariamente la colonna fisica.

12. **`compute_result_columns`: personalizzazioni conservate per nome, non per identità — media/alta.**  
    `app/shared/execution/canvas_columns.py:18` indicizza gli override per nome; `:115` li recupera usando il nome inferito.  
    Scenario: rinomina upstream con ID stabile; `alias` e `visible` non seguono necessariamente la colonna. Il commento che promette sopravvivenza alla rinomina non corrisponde al criterio effettivo.

13. **Aggregate: nomi e ordine divergono con aggregazioni multiple — alta.**  
    Runtime: `transforms/aggregation.py:112` raggruppa le funzioni per colonna e a `:127` appiattisce il MultiIndex Pandas. Inferenza: `schema_table_handlers.py:73` decide il suffisso separatamente per ogni colonna, mantenendo l’ordine della lista di configurazione.  
    Scenario: `a:sum`, `b:sum`, `a:mean`. Il runtime raggruppa le uscite di `a`; quando compare un MultiIndex anche `b` può diventare `b_sum`, mentre l’inferenza lascia `b`. `avg→mean` e la funzione Python di `concat` introducono ulteriori differenze nei suffissi e negli alias applicabili (`aggregation.py:83`, `:147`).

14. **Aggregate: configurazioni supportate solo dall’inferenza — alta.**  
    `schema_table_handlers.py:30` supporta `aggs`; la funzione runtime `aggregation.py:31` e il registry usano `aggregations`.  
    Scenario: configurazione legacy con `aggs`: schema di aggregazioni specifiche, runtime senza `aggregations` produce `count`.

15. **Group by dell’Output: collisioni degli alias non rappresentate — alta.**  
    Il piano `app/shared/execution/result_grouping.py:6` è condiviso. Tuttavia `schema_inference.py:344` aggiunge una colonna per aggregazione; `executor.py:1305` assegna per nome.  
    Scenario: due aggregazioni con alias `totale`, oppure alias uguale a una chiave: inferenza con colonne duplicate, runtime con sovrascrittura. Il piano non impedisce la collisione.

16. **Formula: tipo convenzionale e sovrascritture — media/alta.**  
    Runtime: `transforms/formula.py:193` valuta e assegna il risultato; non accetta `output_type`. Inferenza: `schema_formula_handlers.py:40`/`:55` usa `output_type` o `real`. Nel formato singolo, `:58` non aggiorna una colonna già esistente.  
    Scenario: formula booleana o testuale prevista come `real`; formula che sostituisce un intero con testo mantenendo il vecchio tipo inferito.

17. **Case when/`conditional`: colonne fantasma e tipo errato — alta/media.**  
    Runtime: `transforms/filtering.py:170` non modifica il frame senza colonna valida o condizioni; `:222` converte a numerico quando risultati e default sono numerici. Inferenza: `schema_formula_handlers.py:80` aggiunge sempre l’output `str`, salvo nome già presente.  
    Scenario: condizioni vuote → colonna prevista ma assente; condizioni numeriche → tipo previsto errato.

18. **Rename/drop e conversione dei parametri non uniformi — alta.**  
    `schema_transform_handlers.py:235` accetta `columns`, `renames`, `old_name/new_name`; il registry runtime di `rename` espone `renames`, elaborato da `plugin_executor.py:288`. Una configurazione legacy può dunque essere interpretata dall’inferenza ma ignorata dal runtime.  
    `schema_transform_handlers.py:265` tratta direttamente `columns` come iterabile, mentre `plugin_executor.py:443` converte liste testuali separate da virgole. Scenario: `drop_columns.columns="abc"` → inferenza sulle lettere, runtime sulla colonna `abc`. La stessa differenza generale riguarda configurazioni JSON testuali e booleani normalizzati solo dal plugin.

19. **Concat verticale: `inner` previsto ma non eseguito — alta.**  
    `schema_table_handlers.py:120` implementa `join="inner"` come intersezione; `transforms/joins.py:645` accetta soltanto `ignore_index` e concatena con allineamento esterno.  
    Inoltre l’inferenza prende il tipo della prima occorrenza (`schema_table_handlers.py:140`), senza la promozione dei tipi del concat reale.

20. **Split: numero e nomi dipendono dai valori — alta.**  
    `transforms/columns.py:282` produce solo le parti realmente presenti; senza nomi espliciti genera `<column>_partN`. `schema_transform_handlers.py:289` aggiunge tutti e soli i nomi configurati.  
    Scenario: tre nomi configurati ma una sola parte nei dati, oppure nessun nome configurato.

21. **Pivot: mapping non equivale a schema garantito — alta.**  
    Runtime: `transforms/reshape.py:130` restituisce l’input se nessun valore corrisponde; `:173` conserva solo i valori effettivamente presenti. Inferenza: `schema_reshape_handlers.py:64` annuncia tutte le destinazioni del mapping. Senza mapping restituisce lo schema originale (`:61`), mentre il runtime crea colonne dai dati.  
    Anche indici parzialmente mancanti divergono: l’inferenza filtra quelli mancanti, il runtime torna all’intero input. `mean` può promuovere il tipo che l’inferenza conserva.

22. **Melt: collisioni risolte soltanto dal runtime — alta.**  
    `transforms/reshape.py:369` genera `variable_1`, `value_1`, ecc.; `schema_reshape_handlers.py:108` mantiene i nomi configurati. Il tipo dell’output misto viene inoltre fissato a `str`, mentre il runtime può effettuare una promozione numerica.

23. **Transpose: `output_columns` descrive una rinomina parziale, non lo schema finale — alta.**  
    Runtime: `transforms/reshape.py:447` crea una colonna per riga, mantiene la prima colonna e rinomina solo le successive disponibili; a `:441` non fa nulla sul frame vuoto.  
    Inferenza: `schema_reshape_handlers.py:175` ricostruisce lo schema dalla lista dichiarata; può omettere `col_0`, annunciare nomi in eccesso e perdere colonne non rinominate.

24. **Flatten JSON: significato incompatibile di `output_columns` — alta.**  
    Runtime: `transforms/analysis.py:285` richiede nomi **già prefissati**, applica `max_level` e concatena le colonne estratte. Inferenza: `schema_reshape_handlers.py:151` aggiunge nuovamente il prefisso e non applica il limite di profondità.  
    Scenario: `column=j`, `output_columns=["j.a"]`: runtime `j.a`, inferenza `j_j.a`. Senza lista il runtime scopre nuove colonne, l’inferenza mantiene solo l’input. Le collisioni runtime possono creare duplicati che l’inferenza scarta.

25. **Table diff: schema diverso quando il risultato è vuoto — alta.**  
    Inferenza: `schema_join_handlers.py:165` include colonne esclusive di entrambi i lati. Runtime: `app/core/dataframes/tabular.py:1215` nel risultato vuoto conserva solo chiavi, colonne comuni e metadati `_diff_*`.  
    Inoltre `transforms/reshape.py:280` converte a stringa le chiavi discordanti, senza corrispondente aggiornamento inferito.

26. **Lookup: struttura abbastanza allineata, tipo non sempre allineato — media.**  
    Sono condivisi normalizzazione del mapping e riconoscimento numerico. Ma `schema_join_handlers.py:98` aggiorna il tipo solo nel caso numerico, altrimenti conserva il tipo sorgente/esistente.  
    Runtime: `transforms/analysis.py:141` può sostituire numeri con stringhe o valori misti. Scenario: `{1: "uno"}` su una colonna intera.

27. **Colonne calcolate che sovrascrivono nomi esistenti: famiglia di tipi obsoleti — media.**  
    Il runtime assegna per nome; molti handler aggiungono soltanto se il nome non esiste. Coinvolti: `concat_columns`, `add_row_number`, `uuid_generator`, `string_length`, `date_extract`, `string_extract`, `bin`, `statistics`, `window_function`; riferimenti:  
    `schema_transform_handlers.py:438,467,491,531,590,646,695`; `schema_table_handlers.py:223,261`.  
    Runtime corrispondente: `transforms/columns.py:241`; `utility.py:63,135,170`; `strings.py:107,169`; `analysis.py:201,431`; `aggregation.py:346`.  
    Caso distinto: `md5_hash` aggiunge sempre una nuova voce nello schema (`schema_transform_handlers.py:319`), anche quando il runtime sovrascrive (`security.py:128`): diverge anche il numero di colonne.

28. **No-op e frame vuoti non riprodotti dagli handler — alta per lo schema.**  
    Esempi concreti: `reorder_columns` non riordina frame vuoti (`columns.py:132`); `window_function` e `uuid_generator` non aggiungono colonne (`aggregation.py:326`, `utility.py:166`); `string_pad` e `string_length` hanno ritorni anticipati (`strings.py:49`, `:96`).  
    `concat_columns` e `md5_hash` non producono l’output senza colonne valide (`columns.py:183`, `security.py:54`), mentre i rispettivi handler possono annunciarlo. `mask_data`/`text_normalize` possono annunciare destinazioni di colonne sorgenti assenti (`schema_transform_handlers.py:343,409`).

29. **Statistics raggruppate: collisioni e tipi diversi — alta/media.**  
    `transforms/analysis.py:413` unisce le statistiche al frame tramite merge; collisioni con nomi già presenti possono produrre suffissi `_x/_y`. L’inferenza a `schema_table_handlers.py:224` salta invece il nome già presente. Tutte le statistiche diverse da `count` sono dichiarate `real`, anche `min/max` testuali.

30. **Regex con gruppi nominati e anonimi: colonne non previste — alta.**  
    Runtime: `transforms/strings.py:169` copia **tutte** le colonne dell’estrazione, comprese quelle dei gruppi anonimi. Inferenza: `schema_transform_handlers.py:646` copia solo i gruppi nominati.  
    Scenario: `(?P<nome>...)(...)`.

31. **Join spaziali: schema sostanzialmente diverso — alta.**  
    `schema_geo_handlers.py:14` conserva tutte le colonne sinistre, aggiunge tutte le destre, suffissa solo quelle destre e aggiunge `index_right`. Runtime: `transforms/spatial_overlay.py:77` delega a GeoPandas e a `:79` **rimuove** `index_right`; la geometria destra e le collisioni seguono le regole di GeoPandas.  
    `geo_nearest` ripete la simulazione asimmetrica dei suffissi (`schema_geo_handlers.py:837`); inoltre con un lato vuoto il runtime restituisce solo il sinistro (`spatial.py:631`).

32. **Overlay e dissolve: inferenza non rappresentativa del runtime — alta.**  
    `schema_geo_handlers.py:299` ignora `overlay_type`: anche `difference` aggiunge attributi destri; sulle collisioni conserva il nome sinistro e suffissa solo il destro. Runtime: `spatial_overlay.py:141,186,231,276,424`, tramite overlay GeoPandas.  
    `geo_dissolve`: inferenza soltanto gruppo+geometria (`schema_geo_handlers.py:191`); runtime aggrega anche gli altri attributi e può aggiungere `index` con `reset_index()` (`spatial.py:330,347`).

33. **Altre operazioni geografiche: sovrascritture, CRS e uscite condizionali — media/alta.**  
    Gli handler di area, distanza, lunghezza, perimetro, conteggio vertici, bounds e conversione WKT non aggiornano sempre il tipo di un output preesistente: `schema_geo_handlers.py:137,351,385,438,464,489,513`. Buffer/centroid/hull/envelope/point-on-surface/boundary gestiscono la sostituzione della geometria sorgente, ma non tutte le collisioni con un’altra colonna esistente (`:75`, `:117`, `:213`, `:253`, `:680`, `:719`).  
    `geo_reproject` è un passthrough inferito (`schema_transform_handlers.py:122`), anche per metadata con SRID. `geo_explode`, `geo_make_valid`, `geo_clean_topology` ricadono nel fallback, non descrivendo cambiamenti del sottotipo geometrico.  
    `geo_count_points_in_polygons`: con input vuoto il runtime aggiunge solo il conteggio (`spatial_overlay.py:357`), mentre l’inferenza può aggiungere anche l’aggregato (`schema_geo_handlers.py:576`).  
    `geo_line_builder`/`geo_polygon_builder`: l’handler legge `output_column` (`:653`), ma il registry non lo passa alle funzioni; un valore configurato diverso da `geometry` viene previsto ma non applicato.

34. **Estrai: inferenza del Result non costruita dalla proiezione SQL — alta.**  
    `schema_inference.py:330` prende il primo schema entrante; una sorgente SQL viene inferita dalle proprie colonne (`:353`), senza incorporare la catena di tabelle collegate.  
    SQL: `app/core/estrai/query_projection.py:42` seleziona le colonne di **tutte** le tabelle raggiunte.  
    Scenario: tabelle A→B→Output: lo schema può rappresentare B, il SELECT produce A+B.

35. **Estrai: aggregazioni, colonne calcolate e duplicati divergono — alta.**  
    SQL group by: alias predefinito `<funzione>_<colonna>` (`query_projection.py:149`); inferenza generica Result: `<colonna>_<funzione-normalizzata>` (`result_grouping.py:16`).  
    Colonna calcolata incompleta: SQL la salta (`query_projection.py:101`), inferenza la annuncia (`schema_inference.py:373`).  
    Nomi duplicati: esecuzione li rende univoci (`estrai/executor.py:96`, `query_execution/query_session.py:194`), inferenza no.  
    `output_columns` è applicato dall’inferenza generica del Result, ma `generate_select` non usa quella selezione: legge le colonne delle sorgenti.

36. **Arricchisci: mapping del canvas diverso dal mapping effettivo — alta.**  
    Inferenza: `schema_inference.py:581` legge soltanto `node.output_mapping`.  
    Runtime: `enrichment_service.py:346` usa la configurazione API corrente, aggiorna i nomi per `path`, applica fallback alla connessione e deduplica per destinazione.  
    Scenario: rinomina della colonna nella connessione API senza aggiornamento del canvas; previsione vecchia, esecuzione nuova. L’inferenza accetta anche `target_column`/`name`, mentre il runtime qui risolve `column`/`path`.

37. **Arricchisci: generator, risposta dinamica e sovrascritture — alta.**  
    L’inferenza assume sempre input+mapping (`schema_inference.py:569`). Il generator produce invece il frame della risposta (`api_connection_generator_enricher.py:445`), e senza mapping può scoprire colonne dinamicamente. Anche l’enricher supporta auto-flatten (`enrichment_response_processing.py:58`).  
    Mapping su un nome esistente: inferenza mantiene il tipo precedente; runtime scrive i valori API. Il tipo dichiarato nel mapping non equivale necessariamente al tipo rilevato dalla risposta.

38. **Arricchisci: Output e ingressi multipli seguono regole diverse — alta.**  
    `canvas_orchestrator.py:303` sceglie il primo edge nell’ordine del canvas; l’inferenza ordina gli edge (`schema_inference.py:622`). Inoltre l’inferenza generica applica `result_group_plan`, ma l’Output Arricchisci a `canvas_orchestrator.py:299` applica solo la selezione colonne.

39. **`canvas_schema_runtime`: tipi globali per nome, non per ramo — media/alta.**  
    `canvas_schema_runtime.py:50` aggrega tipi di tutti i nodi nella stessa mappa, inventando anche varianti `_L/_R`; l’ultimo nome omonimo vince. La propagazione per transform viene attivata solo da alcune configurazioni nuove (`:79`), poi confluisce nuovamente in una mappa globale (`:212`).  
    Non risolve gli ID del config come `SchemaInferenceService`; il Result è un passthrough in quella propagazione. `:98` privilegia questi metadata rispetto al dtype reale.  
    Scenario: due rami con `valore` di tipi diversi, oppure cast/formula sullo stesso nome: preview/esportazione possono usare il tipo dell’altro ramo o quello precedente.

40. **`output_contract`: ulteriore normalizzazione e recupero dell’identità per nome — alta/media.**  
    `app/core/output_contract/service.py:88` rende univoci i nomi, mentre inferenza e `compute_result_columns` possono conservarli duplicati. Manipola recupera gli ID tramite `by_name` dopo l’esecuzione (`executor.py:462`), perdendoli proprio dove il runtime ha cambiato il nome rispetto all’inferenza.  
    Il contratto pubblicato deriva dai dtype reali (`executor.py:474`), quindi può differire dai tipi inferiti/preview. Il controllo del consumer verifica colonne mancanti, ma non uguaglianza completa di ordine, quantità e duplicati; i tipi discordanti producono soltanto warning (`output_contract/service.py:307,336`).

41. **Schemi dinamici o non supportati dichiarati “certain” — alta.**  
    `schema_inference.py:207` assegna `certain` quando non è stato esplicitamente registrato un errore. I passthrough di pivot/flatten/transpose senza schema dichiarato e i tipi-nodo non gestiti non marcano necessariamente indeterminate.  
    Conseguenza: uno schema incompleto viene utilizzato come prova di assenza di una colonna, generando falsi errori a valle.

I piani già condivisi sono:

| Piano/helper | Runtime | Inferenza | Validatore |
|---|---|---|---|
| `select_column_plan` | select e Output | select e Result | sì, ma dopo perdita dei duplicati |
| `join_column_plan` | Join nuovo | tramite `join_output_plan` | **non chiamato** |
| `legacy_join_plan` | **non usato** | Join legacy | indirettamente tramite schema |
| `join_output_plan` | **non usato come piano completo** | Join nuovo | non chiamato |
| `result_group_plan` | Output Manipola | Result generico | **non chiamato direttamente** |
| `aggregation_output_type` | non determina i dtype runtime | Result | no |
| normalizzazione lookup | sì | sì | solo controlli generici |

`tests2/contracts/test_column_plans_parity.py:16` verifica che fixture e regole Python coincidano; `:40` confronta nuovamente i piani con la fixture. **Questa parità non dimostra la parità con `op_join` o con tutti i DataFrame prodotti.**

Non ho individuato una divergenza sistematica dei nomi nel caso ordinario di `filter`, `sort`, `distinct`, `sample`, `dedup_advanced`, `drop_columns` normalizzato, `cross_join` senza collisioni anomale e nelle operazioni geografiche che filtrano soltanto righe. Restano applicabili i problemi trasversali di parametri, tipi, duplicati e schema sorgente.

**Causa comune e regola proposta — classe 1.**  
Compilare ogni nodo in un **unico piano ordinato di colonne**, con identità, provenienza, nomi fisici, tipi, proiezione e collisioni. Runtime, inferenza, validazione, preview e contratto devono consumare quel piano, dopo la stessa normalizzazione dei parametri. Le uscite dipendenti dai dati devono essere esplicitamente indeterminate oppure vincolate a uno schema dichiarato effettivamente imposto dal runtime. L’Output deve ricevere la mappa ID→nome del proprio ingresso effettivo; il supporto in `apply_output_columns` esiste già.

**CLASSE 2 — Valida non segnala ciò che fa fallire l’esecuzione**

Il controllo corrente è delimitato da `canvas_validation.py:56`: struttura, alcune regole per tipo e validatori registrati. Il validatore Manipola verifica riferimenti del config, sicurezza AST e obbligatorietà da registry; **non esegue la preparazione completa del nodo**.

1. **Formula: riferimenti e compatibilità dell’espressione non controllati — alta.**  
   Runtime: `transforms/formula.py:311,326`; validazione: `node_validator.py:166`, limitata a `_validate_formula_ast`.  
   Copertura: sintassi e operazioni vietate **sì**; nomi di colonne inesistenti, namespace disponibile, incompatibilità di tipi e risultato assegnabile **no**. Include esattamente lo scenario `valore_catastale_L / vc_tot_contratto_Romi_L`.

2. **Formula multipla: struttura degli elementi non validata con la stessa funzione — alta.**  
   Runtime: `formula.py:216` richiede lista ed elementi con `new_column`/`formula` stringhe non vuote. Validatore: `node_validator.py:168` estrae solo le formule dagli elementi dict.  
   Elementi incompleti possono non produrre il medesimo errore; shape non iterabili possono far fallire la validazione stessa invece di restituire un Issue.

3. **Join: controlli bilaterali sostituiti da un’unione dei nomi — alta.**  
   Runtime: chiavi presenti su ciascun lato (`joins.py:181`), cardinalità delle coppie (`:166`), policy/collisioni/selezioni (`column_selection.py:55`).  
   Validatore: unisce gli schemi upstream (`canvas_validation.py:529`) e verifica l’appartenenza globale (`node_validator.py:159`).  
   Scenario: chiave sinistra presente soltanto a destra → valida, runtime fallisce. Non coperti compiutamente: coppie di lunghezza diversa, chiavi omonime incrociate, `name_conflict` invalido, collisioni del piano e selezione sul lato sbagliato.

4. **Multi-join: validatore di un tipo di nodo diverso — alta.**  
   Registrato allo stesso validatore transform (`node_validator.py:37,219`), che cerca `transform_op` e `config`; runtime legge anche `join_keys`, `join_key_ids`, `left_keys/right_keys`, `join_pairs`, `multi_join_type` (`executor.py:1008`).  
   Le incoerenze delle coppie falliscono a `executor.py:1037`; il validatore non esegue quel controllo e può emettere invece un improprio “operazione mancante”.

5. **Filter: struttura, operatori e coercizione valori incompleti — alta.**  
   Runtime: `transforms/filtering.py:50,53,55,63,74,81,138`. Controlla lista di condizioni, campi, operatore, esistenza colonna, forma di `in/not_in`, valori numerici e compatibilità del confronto.  
   Validatore: copre parte dei riferimenti, non richiama questi controlli.

6. **Output: group by non validato — alta.**  
   `node_validator.py:92`/`:115` ritorna dopo la gestione della selezione. Non chiama `result_group_plan`.  
   Runtime: `result_grouping.py:22` rifiuta l’assenza di gruppi validi; `:26` rifiuta l’assenza di aggregazioni valide.  
   Scenario: `group_by_enabled=True` con colonne rimosse a monte passa senza il corrispondente errore dell’Output.

7. **Preparazione dei parametri del plugin non condivisa — alta.**  
   Runtime: `plugin_executor.py:263` applica default, `depends_on`, conversioni e parsing JSON; `:421` può rifiutare una mappa valori malformata.  
   Validatore: `node_validator.py:185` controlla soprattutto presenza e tipo geometrico; non convalida sistematicamente enum, forma delle liste/dizionari, numeri e dipendenze. Ciò riguarda tutte le operazioni del registry.

8. **Controlli delle operazioni tabellari demandati a Pandas, assenti dal preflight — alta.**  
   Per completezza della famiglia, i punti di esecuzione e la copertura sono:

   | Operazioni | Controllo/errore runtime non riprodotto da POST `/validate` |
   |---|---|
   | `aggregate` | Funzioni di aggregazione, shape degli elementi, compatibilità dei tipi, collisioni con chiavi in `aggregation.py:94,125`; riferimenti solo parzialmente coperti |
   | `pivot` | Compatibilità della funzione coi valori, collisioni nell’indice/proiezione in `reshape.py:148,169`; il semplice riferimento esiste/non esiste non basta |
   | `melt` | Collisioni residue, configurazione e compatibilità della proiezione in `reshape.py:369,396` |
   | `transpose` | Intestazioni/forme prodotte dai valori in `reshape.py:447` |
   | `table_diff` | Collisioni dopo rinomina delle chiavi e nomi tecnici `_merge`, `_OLD/_NEW` in `reshape.py:270`, `dataframes/tabular.py:1166` |
   | `concat`, `cross_join` | Indici/nomi duplicati e collisioni del merge in `joins.py:665,744` |
   | `sort`, `distinct`, `dedup_advanced` | `ascending`, `keep`, ordinabilità/hashabilità dei valori in `aggregation.py:189,219,279` |
   | `window_function` | Compatibilità della funzione coi dati e dell’offset in `aggregation.py:353`, `dataframes/tabular.py:715` |
   | `sample` | Parametri di campionamento, frazioni e stratificazione in `analysis.py:37` |
   | `lookup`, `bin` | Mapping e valori compatibili; intervalli/etichette validi in `analysis.py:141,201` |
   | `statistics` | Aggregazioni numeriche su valori non compatibili e merge con collisioni in `analysis.py:413,431` |

   Il controllo generico dei riferimenti non sostituisce questi controlli. Alcuni dipendono dai valori effettivi e richiedono una fase distinta dalla validazione statica.

9. **Fill/cast/replace: valori e opzioni non verificati — alta.**  
   `cleansing.py:41` rifiuta riempimenti incompatibili con colonne numeriche, inclusi valori frazionari per interi; `:192` esegue regex; `:258,277` esegue conversioni numeriche/date, eventualmente con `errors="raise"`.  
   POST `/validate` non prova questi contratti. Per `fill_na` e `replace` l’inferenza passthrough non certifica neppure il tipo risultante.

10. **Operazioni testuali/privacy/utility: shape e parametri non verificati — media/alta.**  
    Punti runtime: `columns.py:234,282` (`concat_columns`, `split_column`); `strings.py:62,168,287` (`string_pad`, `string_extract`, `text_normalize`); `security.py:98,239,260` (`md5_hash`, `mask_data`); `utility.py:63,113` (`add_row_number`, `date_extract`).  
    Esempi: separatore non testuale, fill character non valido, configurazione masking malformata, partizione o valori non utilizzabili. `string_length` e `uuid_generator` non aggiungono un proprio controllo semantico significativo oltre alle assegnazioni; restano i controlli comuni.  
    Una regex non valida di `string_extract` viene invece gestita come no-op (`strings.py:156`): non è un caso di esecuzione fallita.

11. **Operazioni geografiche: controllo del tipo dichiarato troppo debole — alta.**  
    Il validatore controlla che il tipo inizi per `geometry` (`node_validator.py:206`). Il runtime verifica anche presenza/conversione della geometria e disponibilità di geometrie valide (`dataframes/geodata.py:241,288,294`).  
    Vale per buffer, centroid, area, clip, dissolve, simplify, hull, reproject, envelope, join/overlay, distance/nearest, length/perimeter, explode/make-valid, within, point-on-surface, boundary, Voronoi, WKT, topology, bounds, vertex-count e builder.  
    Controlli specifici non coperti: tolleranza positiva (`spatial.py:382`), CRS sorgente necessario (`:475`), CRS destinazione/metrico utilizzabile, predicati/how di join/overlay, distanza massima, aggregazione dissolve, geometrie puntuali richieste dai builder (`spatial_construction.py:55`) e da Voronoi (`spatial.py:1040`). La sola dichiarazione `geometry` non verifica questi requisiti.

12. **Limiti di memoria/cardinalità non rappresentati nell’esito “valido” — alta, dipendente dai dati.**  
    Runtime: Join `joins.py:383`; cross join `:720`; multi-join `executor.py:1108,1136,1177,1213,1249`; pivot/melt/spatial `transform_memory.py:317,364,446,499,507`; Voronoi `spatial.py:1029`; topology `:1175,1188`.  
    POST `/validate` non li valuta. Non è possibile garantirli dal solo canvas: l’esito dovrebbe distinguere “configurazione valida” da “dimensionamento verificato”.

13. **Input progetto scalare: manifest non compilato durante la validazione — alta.**  
    Runtime: `eseguibili/manifest.py:142` rifiuta lo scalare senza binding; `canvas_validation.py:182` esclude lo scalare dai controlli delle sorgenti. Non esiste una chiamata a `compile_manifest` nel percorso POST `/validate`.  
    È confermato il caso “Input 1 non è collegato a nessun campo”.

14. **Altri vincoli statici degli input runtime tutti fuori dal validatore — alta.**  
    Runtime: `eseguibili/manifest.py` controlla definizione e binding (`:88,95,125,128`), campi protetti (`:59`), connessione senza binding (`:146`), destinazione unica (`:194`), Connection Set solo Estrai (`:205`), un solo input connessione (`:211`), target esistente (`:216`), campo configurabile (`binding_capabilities.py:203,217`) e campo conteso fra input (`manifest.py:228`).  
    `eseguibili/models.py:44,95,147` verifica range, regex, opzioni select, `multiple`, expected columns e chiavi univoche. Il percorso corrente di validazione canvas non compila questa stessa definizione.

15. **Valori runtime: nessuna fase di validazione equivalente prima della conferma — alta.**  
    `eseguibili/runtime.py:19` verifica obbligatorietà, conversione scalare, lunghezza, pattern, min/max e opzioni; `:81` verifica percorsi/indici scrivibili; `:285` rifiuta input sconosciuti.  
    `orchestrator/runtime_inputs.py:24,38` rifiuta input non dichiarati e sostituzioni file non ammesse per un progetto.  
    Connection Set: `eseguibili/service.py:75`, `connections/connection_sets.py:327,359` verificano selezione, accesso e membro attivo.  
    Questi controlli richiedono i valori e quindi non possono essere coperti dall’attuale POST `/validate`, che non li riceve.

16. **Input file degli eseguibili: controlli solo al materializzarsi dell’esecuzione — alta.**  
    `eseguibili/runtime.py:147,155,162,187,198,207,212`: file mancante/vuoto/illeggibile, mapping malformato, colonne richieste assenti, miscela geografico-tabellare, geometrie e CRS incompatibili.  
    Non coperti dalla validazione canvas; una fase preflight con gli upload già selezionati potrebbe eseguirli.

17. **Sorgenti e catene di progetti: validazione incompleta — alta.**  
    Runtime: contesto/tenant (`source_loading.py:94`, `datasource_node_loading.py:120`), contratto sorgente (`project_source_runtime.py:182`), colonne fisiche mancanti (`output_contract/service.py:315`), sorgente stale/non disponibile (`arricchisci/api_connection_source_loading.py:196,220,258,264`).  
    `validate_project_chain` restituisce un report vuoto se un progetto non viene caricato (`canvas_validation.py:563`) e usa `_seen` come stop, senza produrre un errore di ciclo cross-project (`:558`). Il runtime Manipola ha invece `ProjectLineageCycleError` (`project_source_runtime.py:85`).  
    Le autorizzazioni controllate al salvataggio non garantiscono la disponibilità al momento della run.

18. **Estrai: SQL non compilato durante POST `/validate` — alta.**  
    Controlli runtime non replicati: identificatori (`query_generator.py:588`), proiezione vuota e colonne calcolate (`query_projection.py:110,115`), aggregazioni e uso di `*` (`:155,159`), ORDER BY aggregato (`:226`), LIMIT (`:316`), operatori/BETWEEN/funzioni filtro (`query_filters.py:97,131,137,295`), configurazione JOIN e filtri ON (`query_joins.py:103,244,278,287,304`), predicato/distanza spaziale (`:200,205`).  
    Alcuni enum sono protetti dai modelli Pydantic; manca comunque l’esecuzione dello stesso compilatore SQL. Errori del database reale — colonne rimosse, permessi, funzione non disponibile nel dialetto — richiedono inoltre un controllo sulla connessione risolta.

19. **Arricchisci: validatore fondato sullo snapshot del nodo — alta.**  
    `arricchisci/node_validator.py:91` legge `parameters` dal canvas; senza lista non verifica mapping richiesti. Con upstream vuoto non verifica l’esistenza delle colonne (`:109`). Runtime usa la definizione API corrente e la validazione dell’executor (`enrichment_validation_preview.py:49,67`).  
    Il router verifica soltanto l’esistenza dell’ID API; se il caricamento dell’elenco fallisce salta anche quel controllo (`projects/router.py:951,960`).

20. **Arricchisci: configurazione richiesta/risposta non validata dal canvas — alta.**  
    Runtime: URL mancante/non consentito (`api_connection_generator_enricher.py:488`), metodo/body type (`http_executor.py:287`), limite risposta positivo (`:198`), configurazione CSV (`response_parser.py:55`), modalità REST-file e multipart (`api_connection_rest_file.py:275,358,401`).  
    `ArricchisciNodeValidator` controlla essenzialmente API selezionata e mapping richiesti (`node_validator.py:64,73`), non questi contratti.

21. **Arricchisci: alcuni errori dell’executor non diventano neppure fallimenti del canvas — alta, difetto collaterale.**  
    `enrichment_service.py:219` restituisce `success=False` quando l’input non è valido; `:236` determina l’esito dagli errori. Ma `canvas_orchestrator.py:205` scarta `_enrichment_result` e restituisce il frame.  
    Quindi non tutti gli errori elencati dell’enricher fanno effettivamente fallire il canvas: alcuni possono risultare in un’esecuzione apparentemente riuscita. Occorre distinguerli dai fallimenti del generator e REST-file.

Sono già coperti: cicli interni, edge pendenti, nodi processing orfani, Output mancante/duplicato/non collegato, alcune sorgenti non ammesse, operazione transform sconosciuta, parte dei riferimenti espliciti, sicurezza AST, forma della selezione colonne e parte dei mapping API.

Timeout, errori HTTP, risposta XML/CSV/JSON malformata, revoca di accesso, guasti storage/DB ed esaurimento delle risorse rimangono controlli runtime: non sarebbe corretto presentarli tutti come difetti eliminabili con una validazione statica.

**Causa comune e regola proposta — classe 2.**  
Ogni nodo deve esporre la stessa preparazione usata dal runtime, eseguibile senza effetti: normalizzazione, risoluzione degli ID, compilazione delle espressioni, piano colonne e vincoli. Separare tre esiti: **configurazione verificata**, **input/dati verificati**, **dipendenze esterne non garantibili**. Il manifest degli input deve essere compilato già dalla validazione del canvas; con i valori disponibili va eseguita anche la preparazione del contesto runtime. Un semplice DataFrame vuoto non basta per i controlli dipendenti dai dati e può mascherare proprio i ritorni anticipati trovati.

**CLASSE 3 — Arricchisci: stima prima dell’avvio e input runtime**

1. **Le richieste non rappresentano l’esecuzione da confermare — alta.**  
   `arricchisci/router.py:201,214`: `ValidateRequest`/`EstimateRequest` non ricevono `inputs`, canvas risolto o identificativo del nodo da stimare. L’avvio applica invece `apply_project_runtime_inputs` a `router.py:820`.

2. **Dataset della stima diverso dall’ingresso effettivo del nodo — alta.**  
   `arricchisci/source_request_loader.py:20` carica il file indicato oppure **l’ultimo artefatto del progetto**. Non prepara il DAG e non raggiunge l’ingresso del singolo `api_enrich`.  
   Scenario: input scalare modifica filtro, sorgente, parametri statici o configurazione API; la run usa dati diversi, la stima resta basata sul precedente output.

3. **Validate enrichment usa regole diverse dalla validazione runtime — alta.**  
   `enrichment_preflight.py:98` tratta i mapping come nomi letterali di colonne; il runtime supporta template e distingue parametri obbligatori/opzionali (`enrichment_validation_preview.py:49,57`).  
   Scenario: mapping `"{x},{y}"` valido a runtime viene cercato come singola colonna nella prevalidazione. Un mapping opzionale assente può essere rifiutato prima ma accettato dal runtime.

4. **Parametri statici gestiti in modo incoerente — media/alta.**  
   `required_mapping_error` unisce `static_columns` e chiavi di `static_parameters` (`enrichment_preflight.py:21`); `build_validate_response` usa soltanto `static_columns` (`:62,97`). Estimate non riceve nessuno dei due campi.  
   Il conteggio dei null a `:117` usa tutti i valori del mapping, senza la stessa selezione dei parametri richiesta dal runtime.

5. **La stima forza un modello operativo diverso — alta.**  
   `enrichment_preflight.py:157` costruisce direttamente un executor con deduplicazione attiva; il runtime costruisce opzioni da nodo e API in `enrichment_service.py:379`.  
   Scenario: deduplicazione disattivata nel nodo, `execute_if`, skip dei null, batch, parallelismo o soglie d’errore: il numero di chiamate effettivo non coincide necessariamente con le combinazioni uniche stimate.

6. **Generator: zero righe non significa zero chiamate — alta.**  
   Validate ha un ramo generator (`router.py:270`); estimate restituisce zero chiamate senza frame (`enrichment_preflight.py:146`). Il generator può partire senza input e fare una o più richieste paginate (`canvas_orchestrator.py:199`, `api_connection_generator_enricher.py:64`).  
   Anche gli input che modificano paginazione o parametri possono cambiare il lavoro senza cambiare il numero di righe dell’artefatto precedente.

7. **Stima non vincolata alla configurazione confermata — media/alta.**  
   Validate, estimate e execute caricano e preparano separatamente dati/configurazioni. Non c’è un’identità comune della preparazione confermata.  
   Scenario: modifica di un input, del mapping o della connessione fra stima e avvio; la conferma può riferirsi a un altro lavoro.

**Causa comune e regola proposta — classe 3.**  
Un unico passaggio di preparazione deve ricevere gli input scelti, compilare il manifest, risolvere il canvas e le connessioni, validare la configurazione risultante e stimare gli ingressi effettivi dei nodi API. La risposta deve distinguere quantità esatte, stimate e non conoscibili senza chiamate esterne.

Per unire richiesta input e conferma-stima, la schermata può mostrare gli input e aggiornare il preflight sugli stessi valori; la conferma deve inviare **la preparazione identificata da revisione/impronta**, verificata nuovamente all’avvio. Va condivisa la preparazione con il runtime, senza usare per la stima percorsi che materializzano file, persistono risultati o invocano le API. Nessuna correzione eseguita.

