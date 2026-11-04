# Blocco A ? Fase A2 ? seconda correzione, 4 novembre 2026

## Perimetro

Riferimento: **HEAD c73545df**, confrontato con il working tree A2 non committato. Questa correzione scrive soltanto in `backend/` e in questo rapporto. Nessun commit, nessun sotto-agente. Non sono stati modificati frontend o `docs/architecture/dependency-matrix.md`: le loro modifiche erano gi? presenti all'inizio della sessione. Lo snapshot frontend era stato rigenerato nella lavorazione precedente.

Il presente rapporto sostituisce le conclusioni del primo giro su D1, D2 e D6: il recupero anticipato per `source_name`, il fallback di tipo applicato all'intera lista e la regola unica `column or path` non erano sufficienti. D3/D4/D5/D7/D8 restano nel perimetro delle regressioni esistenti e della suite finale.

## E1 ? Identit? del contratto precedente

`build_output_contract` applica nell'ordine:

1. Origine `(source_node, source_name)`, presente e univoca su entrambi i lati. `source_name` rappresenta la colonna fisica o il path.
2. Per il contratto storico senza `source_node`, nome fisico di output uguale e univoco.
3. Solo come ultimo recupero, `source_name` univoco nei due contratti, senza occupare il nome fisico di un'altra colonna precedente e senza sottrarre l'id a un vecchio nome ancora presente nel nuovo output.

Un id esplicito del piano non scavalca queste verifiche. Un'origine rinominata non pu? occupare con il proprio vecchio id il nome di un'altra colonna precedente. In caso di ambiguit? viene assegnato un id nuovo, diverso da tutti gli id precedenti; il vecchio id risulta rimosso. Non si converte l'ambiguit? in una rinomina verso un'altra colonna.

Regressioni nuove: sei permutazioni di A/B/C per due rappresentazioni storiche di `source_name`; B produce `id_2`, C produce `id_terza`; id esplicito ingannevole; origine ambigua; rinomina verso un nome gi? esistente; controllo delle rimozioni/migrazioni. Il probe esegue anche il publisher reale di HEAD, poi passa quel contratto precedente al publisher del working tree.

Due aspettative A2 precedenti sono state corrette: gli id non prevalgono pi? sui nomi scambiati di un contratto storico; il test di continuit? Manipola conserva l'origine mentre modifica il tipo e l'alias. La variazione non verificabile dell'origine non ? pi? trattata come prova sufficiente di continuit?.

## E2 ? Tipi per colonna

`app/core/dataframes/schema.py::resolve_column_type` centralizza la precedenza:

**dtype runtime specifico (numerico, datetime, booleano/geometrico) > tipo dichiarato > riconoscimento del contenuto > stringa.**

`object`, `str` e `string` runtime non sovrascrivono il dichiarato. `QueryExecutor` trasporta separatamente dtype e riconoscimento del contenuto; il publisher Estrai combina i metadati per ogni colonna con la dichiarazione del canvas o del piano corrispondente, anche quando il join disambigua il nome fisico (es. `id_2`). Se lo streaming non espone un dtype, si usa il dichiarato disponibile. `result_columns_from_plan` usa la stessa funzione per Estrai, Manipola e Arricchisci; i percorsi Manipola e Arricchisci con DataFrame passano le serie effettive. EnrichExecutor privo di serie usa i tipi del piano e il fallback stringa.

I test attraversano `QueryExecutor` e il publisher, con driver/persistenza simulati, per **CSV, XLSX, JSON, GeoJSON, GPKG, SHP, GDB**, includendo il ramo materializzato forzato e quello streaming quando previsto dal formato. Verificano WKT ? `geometry`, oggetti `date` ? `datetime`, numerica interamente nulla dichiarata ? `number`. Sono verificati anche riconoscimento dal contenuto senza piano e prevalenza dei dtype specifici su dichiarazioni discordanti. Non sono test di serializzazione fisica dei sette writer GIS/tabulari.

## E3 ? Mapping API e comportamento storico

Scelta **(a)**: nessuna correzione dei risultati runtime. La funzione unica `app/shared/execution/api_mapping.py::mapping_destination` esplicita le fasi che HEAD gi? distingue:

- `columns`: `column` se presente, anche vuoto/None; altrimenti `path`.
- `response`: `column or path`, usato dalla risoluzione/estrazione delle risposte.
- `source`: accesso al path nella stessa funzione.
- `explicit`: lettura del solo campo column per i consumatori storici che non applicavano fallback.

Sono centralizzati motori seriale/parallelo e batch, elaborazione delle risposte, generator, parser, pianificazione parallela, inferenza e mapping del worker. L'inferenza rappresenta anche le colonne preallocate e segnala `names_status=indeterminate`/ordine incerto per destinazioni vuote o nulle. Per il caso richiesto l'esecuzione resta `['id', '', nan, 'x', 'z']`; il boundary HTTP rappresenta il nome non testuale come `"nan"` e include tutte e cinque le colonne.

La guardia AST enumera tutti i moduli Arricchisci, l'inferenza e il worker e vieta letture dirette di column/path sulle variabili di mapping fuori dall'helper. Il probe confronta anche il caso vuoto/None con HEAD, pieno e vuoto. **Proposta separata, non implementata:** normalizzare/eliminare le colonne vuote/nulle nell'esecuzione; richiede una modifica esplicita dei risultati e dei contratti, esclusa da questo giro.

## E4 ? Vocabolario chiuso

`OutputColumnType.logical` ? vincolato a **string, integer, number, boolean, datetime, geometry**, gli stessi sei tipi enumerati dal controllo dei dtype dei consumatori. La normalizzazione converte un tipo non riconosciuto in `string`; `result_columns_from_plan` non produce pi? `unknown`. L'incertezza resta negli stati dello schema, non introduce un nuovo tipo pubblico.

Test: uguaglianza fra vocabolario del modello e vocabolario del consumatore; enumerazione di tipi/alias pubblicati; fallback per unknown, object, tipo inventato e tipo assente; rifiuto diretto di `OutputColumnType(logical="unknown")`.

## E5 ? Pulizia

**`backend/.a2-compare/` eliminata.** Verificato `Test-Path=False`. Il primo tentativo con cancellazione forzata ? stato bloccato dalla policy automatica; la cancellazione ordinaria PowerShell, sul percorso assoluto verificato, ? riuscita. Nessuna estrazione o file di confronto temporaneo ? stato creato nel repository in questo giro.

Tutti gli artefatti di confronto/log sono nella scratchpad richiesta:

`C:/Users/Marco/AppData/Local/Temp/claude/C--Users-Marco-Desktop-plenora/855a9a5f-29c2-43d0-94ce-65c98f738527/scratchpad/`

File: `a2-second-head.tar`, `a2-second-head/`, `a2-second-cases.json`, `a2-second-before.pkl`, `a2-second-after.pkl`, `a2-second-targeted.log`, `a2-second-full.log`.

## Verifiche e numeri

Nuovo file permanente: `backend/tests2/contracts/test_a2_second_corrections.py`, **38 casi**. Ultima verifica combinata dei tre file A2: **235 passati, 2 warning, 6,87 s**. Verifica precedente delle nuove regressioni pi? la guardia sulle annotazioni: **39 passati, 4,44 s**. Prima verifica combinata A2: **232 passati**, prima dell'aggiunta degli ultimi due casi. Verifica estesa di contracts/shared/estrai/arricchisci: **12.296 passati, 42 saltati, un fallimento, 4 warning, 223,98 s**; il fallimento riguardava le annotazioni della nuova funzione, corrette senza alzare baseline e con successiva guardia verde.

Ruff **F811,F401,F821** su tutti i Python modificati/nuovi: verde. `git diff --check`: verde.

### Confronto HEAD ? working tree

`tests2/contracts/a2_runtime_probe.py` usa processi separati e import radicati esplicitamente nei due tree. HEAD ? estratto con `git archive` esclusivamente nella scratchpad. Il secondo processo riceve i contratti prodotti dal primo come contratti precedenti.

**60 SQL identici; 36 DataFrame identici con `assert_frame_equal(check_exact=True)`; 546 contratti verificati; zero trasferimenti di id fra nomi fisici gi? esistenti; zero rinomine verso nomi gi? esistenti.** Confermato sul codice finale prima della suite completa.

Matrice ricostruita ed estesa: 12 canvas SQL (gli 11 A2 pi? A?B?C), cinque renderer SQL; sette mapping API ? pieno/vuoto ? output prima/dopo selezione, pi? otto generator; 13 casi di contratto (12 SQL pi? i tre tipi E2 nello stesso output) ? sei contesti (cinque provider e provider non disponibile) ? sette formati = **546**. Anche tutti i 42 contratti del caso tipi conservano geometry/datetime/number. La matrice ? dichiarata nella sua composizione: non viene spacciata per una riesecuzione della matrice esterna di 162 contratti citata nel giro di conferma. I provider esterni non sono collegati: si confrontano SQL, runtime API simulato e publisher; non nuove esecuzioni su cinque server database.

### Suite completa finale

Una sola esecuzione portata a completamento sul codice definitivo, dal venv indicato e con cwd `backend/`. Una precedente invocazione ? stata interrotta al 89%, prima del completamento, per aggiungere la regressione e correggere il fallback al tipo dichiarato nel piano per i nomi disambiguati dal join. Non viene conteggiata come suite completata; il suo log ? `a2-second-interrupted.log`. Il codice non viene modificato durante la nuova esecuzione finale.

```powershell
& 'C:/Users/Marco/AppData/Local/Temp/claude/C--Users-Marco-Desktop-plenora/855a9a5f-29c2-43d0-94ce-65c98f738527/scratchpad/venv-backend/Scripts/python.exe' -m pytest tests2/manipola tests2/projects tests2/contracts tests2/contract tests2/shared tests2/orchestrator tests2/estrai tests2/arricchisci tests2/eseguibili tests2/services -q -p no:cacheprovider -o addopts=
```

**Esito finale: 14.386 passati, 43 saltati, zero fallimenti, 6 warning; 457,65 secondi (7 min 37 s), exit code 0.** Nessuna modifica al codice dopo l?avvio di questa esecuzione; soltanto completamento del presente rapporto.

## Continuit? D3/D4/D5/D7/D8

Restano coperti il boundary HTTP con nomi serializzati come stringhe, l'isolamento degli errori per nodo, le proiezioni SQL parzialmente note, COUNT numerico, origine distinta dal nome finale, ingressi selezionati secondo il runtime e gli output parzialmente corrispondenti al piano. Nessuna modifica frontend in questa correzione.

Le relazioni reciproche A2 gi? documentate restano **projects ? estrai** (riuso del generatore/proiettore SQL) e **projects ? connections** (lettura del provider tramite bridge DB). Erano gi? nella componente SCC censita: il passaggio della guardia SCC non significa assenza di nuovi archi reciproci. Nessuna baseline ? stata aumentata in questa seconda correzione.

# Terza correzione mirata — R1–R5 (ripresa dopo il riavvio)

Questa sezione sostituisce le conclusioni precedenti sulla gestione delle destinazioni API vuote e sulla generazione degli id in caso di origine ambigua. Le modifiche parziali trovate alla ripresa sono state rilette e verificate con nuovi test. I file frontend e `docs/architecture/dependency-matrix.md`, già modificati nel working tree iniziale, non sono stati toccati in questo giro. Nessun commit e nessun sotto-agente.

## Regole applicate

- **R1:** il builder conserva `str(name)` senza fallback su alias/path, senza deduplicare o rinominare le etichette. Il binding al piano assegna origine e tipo, non modifica il nome fisico. Il lookup dei tipi Estrai accetta etichette non stringa. Il controllo downstream accetta etichette vuote, None/nan e duplicate anche quando riceve il DataFrame per verificare i tipi. EnrichExecutor pubblica anche il contratto senza colonne, come il worker, evitando di lasciare il vecchio contratto.
- **R2:** gli id sintetici delle origini ambigue derivano da progetto, origine e indice di occorrenza; nessun ciclo sui precedenti id. Si conserva il recupero E1 per origine univoca e, per gli id storici, nome fisico univoco. Una coppia origine/nome fisico invariata e univoca conserva l'id anche quando l'origine compare più volte: questo chiude il cambio di id alla seconda esecuzione dopo il recupero da HEAD. Le rinomine verso il nome fisico di un'altra colonna restano protette. Non vale più la precedente regola «id diverso da tutti gli id precedenti»: richiederla causava l'alternanza.
- **R3:** runtime e inferenza leggono il mapping con `mapping_destination`, distinguendo le fasi storiche response/columns/source/explicit. Il generator conserva la destinazione vuota; per None l'inferenza usa la costruzione pandas delle etichette a record, che può produrre `None` oppure `nan` secondo le altre colonne. Destinazioni vuote/nulle dichiarano `names_status` e ordine incerti. Nessuna modifica alla trasformazione dei dati eseguita dal runtime.
- **R4:** worker ed EnrichExecutor pubblicano mediante lo stesso `result_columns_from_plan`, con nomi e piano, senza passare al solo worker il DataFrame per riconoscere date dal contenuto. Un campo API dinamico non dichiarato resta stringa su entrambi i percorsi; restano applicabili i tipi dichiarati nel piano. Estrai e Manipola mantengono la risoluzione dei tipi basata sui dati che possiedono.
- **R5:** Valida emette `ARRICCHISCI_EMPTY_MAPPING_DESTINATION`, severity `warning`, con nome del nodo e path. Sono coperti generator/enricher, mapping della connessione e override del nodo, destinazione `""` e `None`, path semplice e wildcard. Il warning non modifica il runtime.

## Test aggiunti

`backend/tests2/contracts/test_a2_third_corrections.py`: **354 casi**.

- 288 enumerazioni: 9 mapping × pieno/vuoto × generator/enricher × 4 publisher reali (Estrai, Manipola, EnrichExecutor, persistenza worker) × contratto iniziale assente/storico. Ogni caso pubblica tre volte, confronta tutte le etichette in ordine, chiama `ensure_dataframe_matches_contract`, verifica id univoci e invariati e assenza di rimozioni.
- 36 confronti worker/EnrichExecutor: uguaglianza dell'intera lista delle colonne pubblicate, inclusi nomi, tipi e id, con il caso data ISO in auto-flatten.
- 16 casi di inferenza generator; 12 warning attraverso Valida con il validatore Arricchisci reale; 2 regressioni dirette su id indipendenti dai previous_ids e nomi fisici non sostituiti da alias/path, inclusi duplicati.

I quattro file A2 combinati: **589 passati, 2 warning, 12,02 s**. Questo controllo precede l'ultima estensione del test downstream ai nomi duplicati, poi inclusa nella verifica mirata e nella suite finale. Ruff F401/F811/F821 sui Python modificati/nuovi: verde. `git diff --check`: verde.

## Confronto HEAD c73545df / working tree

Nuova estrazione di `git archive c73545df backend`, import in processi separati. Tutte le estrazioni, sonde, pickle e log si trovano esclusivamente in:

`C:/Users/Marco/AppData/Local/Temp/claude/C--Users-Marco-Desktop-plenora/855a9a5f-29c2-43d0-94ce-65c98f738527/scratchpad`.

- Sonda permanente `a2_runtime_probe.py`, usando la matrice `a2-second-cases.json`: **60 SQL e 36 DataFrame identici** con confronto esatto; **546 contratti** controllati, compresi E1 A→B→C e i tipi E2. Output aggiornati: `a2-third-before.pkl`, `a2-third-after.pkl`.
- Sonda `a2-third-api-probe.py`: usa le stesse fixture permanenti R1–R5, estraendone le definizioni senza importare codice applicativo WT nel processo HEAD; simula chiamate API e storage, esegue il runtime API e i quattro publisher. **36 ulteriori DataFrame identici**, **144 contratti reali di HEAD** forniti al WT come precedenti, **432 pubblicazioni WT**, id stabili su tre esecuzioni, enumerazione fisica esatta, **zero colonne rimosse**. Le etichette del payload dei publisher sono serializzate a stringa come al boundary; il DataFrame confrontato conserva le etichette originali. Output: `a2-third-api-head.pkl`, `a2-third-api-wt.pkl`.

Totale di questa riesecuzione: **60 SQL, 72 DataFrame confrontati, 690 contratti di partenza verificati**. Questi numeri descrivono la matrice effettivamente rieseguita; non sostituiscono con numeri inventati la matrice esterna di conferma (250 SQL, 152 Estrai, 36 worker, 18 EnrichExecutor), il cui runner non è presente nella scratchpad. Nessuna esecuzione contro server SQL/API esterni: rendering SQL, runtime API simulato e publisher reali con storage simulato.

## Verifiche mirate finali e guardie

Il primo controllo esteso (`a2-third-targeted.log`) ha concluso con **6.767 passati, 42 saltati e 5 fallimenti**. I cinque punti sono stati risolti prima della suite completa: una fixture che confondeva l'etichetta None con la coercizione pandas a nan; ritorno del nuovo helper tipizzato precisamente; helper spostato in `shared/execution/api_mapping.py` per evitare l'arco aggiuntivo projects→dataframes; catch della lettura API limitato a errori SQLAlchemy/I/O/valore; aspettativa storica di rinomina dei duplicati aggiornata alla regola R1. Nessuna baseline o budget di eccezioni aumentati.

Verifica mirata successiva di tutte le nuove regressioni e dei cinque file/guardie interessati: **376 passati, 3 saltati, zero fallimenti, 6,90 s**. Confronti HEAD/WT e Ruff ripetuti sul codice definitivo: verdi. Il confronto e le guardie non hanno scritto codice durante la suite completa.

## Correzione delle guardie emerse nella prima suite completa

La prima esecuzione completa di questo giro (`a2-third-full.log`) ha terminato con **14.737 passati, 43 saltati, 3 fallimenti, 6 warning, 447,31 s**. I tre fallimenti erano tutti riferiti allo stesso import pandas in `shared/execution/api_mapping.py`: boundary DataFrame, allowlist degli import diretti e modulo shared engine senza dipendenze pesanti. Nessun test funzionale fallito. Questo esito non viene presentato come suite verde.

La collocazione finale sostituisce quella descritta nella verifica mirata precedente: `record_column_names` sta in `core/dataframes/schema.py`; `generator_mapping_column_names` espone il risultato attraverso il servizio Arricchisci già usato da projects. Il mapping condiviso resta privo di pandas e di dipendenze core. Entrambi gli helper restituiscono una lista tipizzata di nomi in ordine, senza ritorni dict generici. Nessun nuovo arco di dominio, nessuna allowlist/baseline ampliata. Il controllo mirato completo di R1–R5 e delle guardie interessate ha concluso con **415 passati, zero fallimenti, 10,28 s**. Anche HEAD/WT e Ruff sono nuovamente verdi.

È stata necessaria una **seconda** esecuzione completa sul codice corretto, anziché la singola esecuzione pianificata/richiesta. La deviazione viene riportata esplicitamente; il primo log è conservato. Il codice resta fermo durante la seconda esecuzione; soltanto questo rapporto viene completato.

## Esito finale della suite completa

Eseguita da `backend/` con il venv richiesto e il comando:

```powershell
& 'C:/Users/Marco/AppData/Local/Temp/claude/C--Users-Marco-Desktop-plenora/855a9a5f-29c2-43d0-94ce-65c98f738527/scratchpad/venv-backend/Scripts/python.exe' -m pytest tests2/manipola tests2/projects tests2/contracts tests2/contract tests2/shared tests2/orchestrator tests2/estrai tests2/arricchisci tests2/eseguibili tests2/services -q -p no:cacheprovider -o addopts=
```

**14.740 passati, 43 saltati, zero fallimenti, 6 warning; 479,63 secondi (7 min 59 s); exit code 0.** Log: `a2-third-final-full.log` nella scratchpad. Nessuna modifica al codice dopo l'avvio della suite finale. I warning riguardano Starlette/httpx, JWT di test, centroidi su CRS geografico e parsing date; nessuna guardia architetturale fallita.

R1–R5 applicate e verificate; confronto HEAD/WT invariato nei casi enumerati sopra. Scritture del giro limitate a backend, al presente rapporto e alla scratchpad autorizzata. Nessun commit, nessun intervento frontend, nessun sotto-agente avviato.

# Quarta correzione mirata — G1/G2

Questa sezione sostituisce la regola R4 del terzo giro. **G1:** worker ed EnrichExecutor passano entrambi il DataFrame finale reale a `result_columns_from_plan`. Il servizio espone il finalizer gia disponibile nell'orchestratore; EnrichExecutor conserva il frame in memoria durante la normale persistenza, senza inserirlo nella risposta JSON. Precedenza comune: dtype runtime specifico > dichiarazione compatibile > riconoscimento del contenuto > string. `object`/`str` da soli non smentiscono una dichiarazione (in particolare sui null o sui numeri conservati come object); testo non nullo non riconosciuto come data/geometria costituisce invece evidenza contraria e resta string. La dichiarazione non esegue conversioni. La regola e condivisa con E2 di Estrai attraverso lo stesso resolver; i casi E2 data/geometria object e numeri nulli restano verdi. Float e interi runtime conservano number/integer anche contro dichiarazioni contraddittorie.

**G2:** la lettura opzionale del mapping intercetta SQLAlchemyError, psycopg.Error, RuntimeError del pool, OSError e ValueError. Registra un log `[GESTITO]` e salta solo l'avviso mapping del nodo, conservando gli altri issue. Nessun nuovo broad-except, budget o baseline modificato.

**Test:** 25 nuove regressioni G1/G2, con numericita, dati vuoti, dichiarazioni incompatibili, identita del frame nel finalizer e indisponibilita DB. La matrice preesistente confronta ora i publisher fornendo il frame reale anche al finalizer EnrichExecutor. Controllo esteso: 12.478 passati, 42 saltati, un errore della guardia annotazioni; corretto annotando il finalizer. Ricontrollo mirato definitivo: **419 passati**. Ruff F401/F811/F821 e diff-check verdi.

**HEAD c73545df / WT:** import separati dalla copia HEAD gia estratta nella scratchpad; rieseguiti **60 SQL e 72 DataFrame**, tutti identici esattamente. Verificati 546 contratti Estrai e 144 contratti API di partenza con 432 pubblicazioni WT su tre esecuzioni: nomi fisici esatti, id stabili, zero rimozioni. Restano verdi i casi A→B→C. Sono i numeri delle sonde disponibili, non una nuova attestazione delle 1.224 pubblicazioni della conferma esterna. Confronti e log esclusivamente nella scratchpad richiesta (`a2-fourth-*`).

**Suite completa finale: UNA esecuzione**, da `backend/`, venv e comando richiesti (dieci gruppi, `-q -p no:cacheprovider -o addopts=`): **14.765 passati, 43 saltati, zero fallimenti, 6 warning, 374,78 s; exit code 0**. Log `a2-fourth-full.log` nella scratchpad. Codice invariato durante e dopo la suite. Warning: Starlette/httpx, JWT di test, centroidi geografici e parsing date. Nessun commit, nessun sotto-agente, nessuna modifica frontend; scritture del giro limitate al backend, a questo rapporto e agli artefatti di confronto/log nella scratchpad autorizzata.
