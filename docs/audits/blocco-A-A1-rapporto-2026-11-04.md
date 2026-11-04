# Blocco A - Fase A1 - seconda correzione, 4 novembre 2026

## Ambito

Scritture finali in `backend/`, nessun commit, nessuna modifica al frontend in questo giro. Per rispettare il vincolo di scrittura il rapporto è in `backend/docs/audits/`. Sostituisce `backend/rapporto-A1-2026-11-04.txt`.

## E1 - certezza per aspetto

Arricchisci e Carica controllano `names_status`, come Manipola e la validazione degli edge. L'incertezza dei tipi delle sorgenti reali non declassa un riferimento assente. Rimosso anche il requisito che lo schema noto debba contenere almeno un nome per validare Api Enrich.

Il test AST enumera i moduli backend che consumano `InferredSchema`, l'inferenza tipizzata o `names_status`; vieta ogni accesso `.status` in questi moduli, eccetto il costruttore di compatibilità di `schema_types.py`. Il vincolo è deliberatamente più forte del solo controllo dei validatori. Estrai/edge usano già `names_status`.

## E2 - opzionalità della colonna

`ColumnSchema.optional`, serializzato come `optional: true` soltanto quando necessario, rappresenta le colonne che possono non essere prodotte. I nomi certi restano tali. Le copie e le rinomine preservano questa proprietà, incluso il passthrough Api Enrich.

Uuid generator, string length, window function, geo nearest, string pad e split con nomi espliciti marcano opzionali solo le nuove colonne assenti dal ramo vuoto. Un nome estraneo a tutti questi rami resta un errore; un nome opzionale presente nello schema non genera errori di riferimento. Split senza nomi espliciti resta dinamico: il numero e i nomi delle parti dipendono dai dati, non soltanto dal vuoto.

I contratti di parità verificano la presenza delle colonne obbligatorie e ammettono l'assenza delle sole colonne opzionali. Sono coperti i quattro casi val3, sorgente reale -> Api Enrich e Carica, Geo Nearest e propagazione attraverso Filter.

## E3 - aggregazioni generate dall'esecuzione

`tests2/contracts/generate_aggregation_types.py` esegue `op_aggregate` per 14 funzioni x 7 tipi = 98 coppie. Classifica l'output con `build_columns_with_types`; gli errori runtime diventano `error`. Il risultato versionato è `app/shared/execution/aggregation_types.json`, letto da `aggregation_output_type`, dall'inferenza (anche concat) e dalla fixture. Nessuna seconda tabella manuale.

Valida emette `aggregation.unsupported` per le coppie non eseguibili; se la colonna è opzionale la segnalazione è warning. I test rigenerano l'intera tabella, controllano l'inferenza per ogni coppia riuscita e la validazione per ogni coppia fallita. Le funzioni sono normalizzate in minuscolo, come nel runtime; funzioni e tipi non presenti nella tabella sono rifiutati.

La tabella descrive i bucket effettivamente restituiti dal classificatore backend: i campioni temporali `datetime` sono esposti come `date`; `std` temporale è `str`. Geometry è rappresentata da WKT, coerentemente con il classificatore. I campioni includono tre valori non nulli per gruppo; la tabella è un contratto dei tipi campione, non una garanzia sui valori nulli o su ogni rappresentazione fisica possibile.

### Tabella finale `aggregation_output_type`

| Funzione | int | real | str | bool | date | datetime | geometry |
|---|---|---|---|---|---|---|---|
| avg | real | real | error | real | date | date | error |
| concat | str | str | str | str | str | str | geometry |
| count | int | int | int | int | int | int | int |
| first | int | real | str | bool | date | date | geometry |
| last | int | real | str | bool | date | date | geometry |
| max | int | real | str | bool | date | date | geometry |
| mean | real | real | error | real | date | date | error |
| median | real | real | error | real | date | date | error |
| min | int | real | str | bool | date | date | geometry |
| nunique | int | int | int | int | int | int | int |
| size | int | int | int | int | int | int | int |
| std | real | real | error | real | str | str | error |
| sum | int | real | str | int | error | error | geometry |
| var | real | real | error | real | error | error | error |

## Forma della fixture Join

`join[].expected.columns` e `join_legacy[].expected.columns` sono liste ordinate di triple JSON `[side, source_index, output_name]`:

- `side`: intero 0 per sinistra, 1 per destra.
- `source_index`: indice zero-based della colonna nell'ingresso originale di quel lato, prima di selezioni, deduplicazioni e rinomine.
- `output_name`: nome fisico finale della colonna.

Esempio: `[[0, 0, "id"], [0, 1, "a_L"], [1, 1, "a_R"]`. L'indice identifica anche le occorrenze di nomi duplicati. L'ordine delle triple è l'ordine runtime. `join[].expected.plan` conserva le mappe di selezione/rinomina; le coppie non valide sono `{"error": true}`. Per aggregazioni la forma è `{"type": "real"}` oppure `{"error": true}`.

Fixture rigenerata: `aggregation_types` 180, `grouping` 1093, `join` 1539, `join_legacy` 1328, `select` 152; totale 4292 casi.

## E4 - riproducibilità e pulizia

Rimosso `tests2/contracts/run_a1_identity_audit.py`, dipendente da un runner esterno. I contratti locali su identità univoche, stabilità, self-join, varianti runtime e output per id rimangono eseguibili con pytest senza scratchpad. Il confronto HEAD richiesto usa separatamente gli script scratchpad, senza introdurre quella dipendenza nei test del repository.

## Verifiche

- Prima verifica mirata: 5.206 passati e 6 fallimenti (4 aspettative da aggiornare al nuovo contratto opzionale, 2 controlli AST per BOM); corretti.
- Mirata successiva: 4.745 passati, 2 warning.
- Ultima mirata, inclusa la lettura della tabella anche per concat e la matrice completa di inferenza: 311 passati, 1 warning.
- Confronto fresco HEAD tramite estrazione isolata di `git archive HEAD backend`: 610 casi; due casi UUID non deterministici esclusi; 608 confrontati; esattamente 2 differenze volute Output per id: `output_cols:7:1` e `preview:p_output_id_rename`. Nessuna nuova differenza runtime, 16 esiti di errore uguali nei due tree.

La suite completa è stata avviata una sola volta con il comando richiesto e il venv indicato. La successiva piccola uniformazione di concat alla tabella è stata verificata dai 311 test mirati sopra; la raccolta della suite completa precede l'aggiunta degli ultimi casi di matrice.

Rimossi i 30 file `.log` nella radice di `backend/`, insieme all'estrazione temporanea HEAD e ai pickle di confronto.

### Esito finale

**Suite completa: 13.988 passati, 43 saltati, 6 warning, zero fallimenti; 392,48 secondi.**

Comando eseguito da `backend/`, una sola volta:

```powershell
& 'C:/Users/Marco/AppData/Local/Temp/claude/C--Users-Marco-Desktop-plenora/855a9a5f-29c2-43d0-94ce-65c98f738527/scratchpad/venv-backend/Scripts/python.exe' -m pytest tests2/manipola tests2/projects tests2/contracts tests2/contract tests2/shared tests2/orchestrator tests2/estrai tests2/arricchisci tests2/eseguibili tests2/services -q -p no:cacheprovider -o addopts=
```

Nuovo file `tests2/contracts/test_a1_second_correction.py`: 106 casi parametrizzati complessivi (enumerazione AST, quattro scenari val3, sorgente reale con entrambi i validatori, rigenerazione tabella, 14 coppie in errore, 84 coppie riuscite, Geo Nearest e propagazione). Gli ultimi 85 casi sono inclusi nella verifica mirata finale, aggiunti dopo la raccolta della suite completa.

`git diff --check` senza errori. Rimossi anche i file temporanei `.txt` di queste verifiche: numeri e risultati sono conservati qui.
