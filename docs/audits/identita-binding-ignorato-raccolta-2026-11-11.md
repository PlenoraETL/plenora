# Filtro «Utente che esegue» ignorato in silenzio — raccolta (11/11/2026)

Base: `17423958` (= produzione). Difetto trovato in produzione l'11/11/2026. Regola di dominio: ogni istituto vede SOLO i propri dati; un input «Utente che esegue» (`shared/execution/runtime_identity`) collegato a un filtro o a un parametro DEVE filtrare.

**Caso reale.** Estrai con radice `prt_cd_valuation` e `prt_cd` collegata (INNER JOIN su `cd_id`, `join_conditions` con `LTRIM_RTRIM`), tutte le colonne di `prt_cd` nascoste, filtri su `prt_cd`: `date_end IS NULL` e `ac_id = <Utente che esegue>` (binding `filters.1.value`). `POST /projects/{id}/query-preview` restituiva `FROM afm.prt_cd_valuation WHERE ...` con `tables_used: ["prt_cd_valuation"]`: tabella, JOIN e due filtri spariti; l'Eseguibile e la prova restituivano tutte le righe di tutti gli istituti, senza errore; Valida diceva «valido».

Metodo: raccolta completa, senza correggere, di (A) ogni punto che costruisce il SQL di Estrai o sceglie quali nodi entrano nell'esecuzione e (B) ogni modo in cui un binding di un input con valore iniettato dal server può restare senza effetto. Ricerca: ogni chiamata di `generate_sql`, `query_tables`, `_trova_tabelle_connesse`, `query_plan.sql_query`, `QueryGenerator(` in `backend/app` e `backend/workers`; ogni uso di `run_canvas`, `scoped_canvas`, `preview_canvas`, `executed_canvas`, `unreached_node_ids`; ogni chiamante di `resolve_runtime_inputs`, `identity_values`, `resolve_identity_inputs`, `_apply_scalar_binding`, `_as_identity_source`, `compile_manifest`; il frontend (`utils/canvasGraph`, `FilterModal`, `QueryPreview`).

## 0. Diagnosi: la causa non è la visibilità delle colonne

Riproduzione con il generatore di produzione (`QueryGenerator.generate_sql`, MSSQL, schema `afm`):

| arco della JOIN | colonne di `prt_cd` | `tables_used` | filtri di `prt_cd` |
|---|---|---|---|
| radice → collegata (`val → cd`) | visibili | `["prt_cd_valuation"]` | **spariti** |
| radice → collegata | nascoste | `["prt_cd_valuation"]` | **spariti** |
| collegata → radice (`cd → val`) | visibili | entrambe | presenti |
| collegata → radice | nascoste | entrambe | presenti |

La visibilità non conta: conta il **verso dell'arco**. L'editor crea l'arco dal punto di uscita della tabella di partenza: chi parte dalla radice (radice → collegata, radice → Output) ottiene una tabella collegata che non ha archi verso l'Output. Il costruttore e l'ambito dell'esecuzione risalivano il grafo solo **all'indietro** dall'Output: la collegata «in avanti» restava fuori, con i suoi filtri. Il canvas era «valido» perché la collegata, «non arriva all'Output», aveva solo l'avviso `structure.unreached`, e il suo errore `SOURCE_NO_VISIBLE_COLUMNS` era declassato ad avviso dalla regola dei rami scollegati.

## A. Punti che scelgono le tabelle o i nodi dell'esecuzione

| # | Punto | Chi lo usa | Oggi con la collegata «in avanti» |
|---|---|---|---|
| A1 | `estrai/query_generator._trova_tabelle_connesse` → `query_tables` | ogni SQL di Estrai (A5–A9) | visita solo gli archi entranti dall'Output: **tabella esclusa** |
| A2 | `projects/canvas_preview_scope.unreached_node_ids` → `run_canvas` | `orchestrator/service._execute_loaded_pipeline` (Esegui, Eseguibile, prova, ricalcolo isolato a monte, Pianificazioni, catene), `scoped_canvas` degli esecutori (anteprima dell'Output), Valida | la collegata è un «ramo che non arriva all'Output»: **tolta dal canvas** prima dell'esecutore |
| A3 | `preview_canvas` / `executed_canvas(node_id)` (anteprima di un passo, compreso l'Output come passo; chiusura a monte) | `GET /pipeline/{id}/preview?node_id=`, `upstream_identity` | soli antenati: con `node_id` = Output **tabella esclusa**; con `node_id` = una tabella: il passo e i suoi antenati (scelta di sempre, test `test_estrai_node_scoped_preview`) |
| A4 | `projects/canvas_validation` (`structure.unreached`, `SOURCE_NO_VISIBLE_COLUMNS`, `sql.invalid` sul canvas intero) | Valida | avviso «non arriva all'Output»; errore «colonne deselezionate» declassato ad avviso → **canvas valido** |
| A5 | `estrai/executor._execute_database` → `query_plan.sql_query` | esecuzione (Esegui, Eseguibile, prova, ricalcolo isolato a monte); lo stesso SQL va a `QueryExecutor.execute_query`, quindi anche allo **streaming** (`query_streaming.execute_streaming_to_writer`) e all'**export** dei formati | SQL senza la tabella |
| A6 | `estrai/executor._preview_database` | anteprima dei dati (Output e passo) | SQL senza la tabella |
| A7 | `projects/query_preview.describe_estrai_query` | `POST /projects/{id}/query-preview` | SQL senza la tabella (il caso visto in produzione) |
| A8 | `projects/schema_execution_sources.sql_projection` | `/schemas` (colonne dell'Output, usate dai Manipola a valle) | colonne senza la tabella (nascoste comunque: nessuna differenza visibile) |
| A9 | `projects/canvas_validation._canvas_issues` (`generate_sql` sul canvas intero) | Valida | costruisce senza la tabella, quindi nessun `sql.invalid` |
| A10 | stima e conteggio | stima dell'Eseguibile Estrai (`eseguibili/preflight`): **non** costruisce SQL; nessuna query `COUNT` separata in Estrai (ricerca `COUNT(`: solo l'aggregazione dell'utente in `query_projection`) | — |
| A11 | valori distinti di Estrai | `projects/router.distinct_node_values` → `CanvasDataPreparation`: non legge le tabelle SQL di Estrai (solo sorgenti file/statiche/progetto) | — |
| A12 | frontend `utils/canvasGraph.analyzeCanvas` (pannello «non arriva all'Output», vista Passi) | editor | la collegata è segnata «scollegata» |
| A13 | lettura ArcGIS (`executor._execute_feature_service`, `_preview_feature_service`, `query_plan.feature_service_query`) | connessione ArcGIS | legge solo il primo layer: i filtri delle tabelle SQL non entrano mai (nessun binding vi si offre oggi, ma un filtro `value_literal` su una tabella SQL resterebbe senza effetto) |

Trattamento dei filtri in LEFT JOIN (verificato, da non cambiare): `query_filters.generate_where` mette nella WHERE i filtri di **ogni** tabella della query, qualunque sia il tipo di JOIN (una tabella in LEFT JOIN con un filtro si comporta come in INNER JOIN). Vale già per le tabelle con colonne visibili; la tabella senza colonne visibili deve avere lo stesso trattamento.

## B. Modi in cui un binding «Utente che esegue» resta senza effetto

| # | Modo | Oggi | Atteso |
|---|---|---|---|
| B0 | Filtro su una tabella collegata «in avanti» (caso reale) | **ignorato in silenzio** | la tabella entra (regola 1); il filtro ha effetto |
| B1 | Filtro su una tabella non collegata a niente / a un ramo che non arriva all'Output | **ignorato in silenzio** (la tabella non entra) | blocco |
| B2 | Filtro collegato senza colonna o senza operatore (la WHERE lo salta: `_filter_condition` → `""`) | **ignorato in silenzio** | blocco |
| B3 | Filtro collegato in un gruppo «oppure» | rifiutato solo alla costruzione dell'esecuzione (`LITERAL_IN_OR_GROUP_MESSAGE`); Valida (canvas senza il segno `value_literal`) lo dice valido | blocco anche in Valida, anteprima, avvio |
| B4 | Operatore che allarga (LIKE, `!=`, `<`, BETWEEN...) | come B3 (`LITERAL_OPERATOR_MESSAGE`) | come B3 |
| B5 | Parametro di un passo di Manipola che non arriva all'Output | **ignorato in silenzio** (`run_canvas` toglie il passo) | blocco |
| B6 | Sorgente «Utente che esegue» (Manipola, `as_source`) non collegata | **ignorata in silenzio** (`staticValues` resta tra i nodi fuori dal flusso) | blocco |
| B7 | Parametro statico di un nodo API non collegato all'Output | **ignorato in silenzio** | blocco |
| B8 | Indice di filtro inesistente (filtro cancellato dopo il binding) | rifiutato da `binding_capabilities` (`compile_manifest`): Valida errore, pubblicazione rifiutata, avvio 400 | invariato (già blocca) |
| B9 | Indice spostato su un filtro senza valore (`IS NULL`) | rifiutato come B8 | invariato |
| B10 | Indice spostato su un ALTRO filtro con valore | non rilevabile: il binding non porta un'impronta del bersaglio (la colonna). `FilterModal` rinumera i binding quando si cancella un filtro dalla finestra; altri percorsi no | punto aperto |
| B11 | Nodo bersaglio cancellato | rifiutato da `check_binding` («nodo inesistente») | invariato |
| B12 | Nodo bersaglio sostituito da un file (input file `data_source` sullo stesso nodo) | oggi impossibile per costruzione (il bersaglio `data_source` è sempre il nodo dell'input file), ma la regola non lo vietava | blocco se succede |
| B13 | HAVING collegato con GROUP BY o HAVING spenti / condizione che la query salterebbe | bersaglio offerto solo con GROUP BY e HAVING attivi (altrimenti rifiutato come B8); `generate_having` salta in silenzio una funzione o un operatore fuori elenco, ma il modello del canvas (`ResultNode.having_conditions`) non li ammette | invariato (già blocca); coperto anche dalla verifica sulla query |
| B14 | Connessione ArcGIS con filtri di tabelle SQL collegati | ignorati (A13) | blocco nell'esecutore |
| B15 | Anteprima di un passo che non contiene il bersaglio | il passo non è filtrato (scelta di sempre: l'anteprima di un passo mostra il passo) | invariato, dichiarato |

## C. Percorsi che devono passare dalla verifica

| # | Percorso | Funzione | Identità |
|---|---|---|---|
| C1 | avvio dell'Eseguibile | `eseguibili/service.execute_versioned` → `upstream_plan.plan_for_version` + `runtime.resolve_runtime_inputs` | `authenticated` |
| C2 | stima e impronta dell'Eseguibile (tutti i tipi; Estrai e Manipola non risolvono gli input) | `eseguibili/preflight.preflight_versioned`, `fingerprint_for` → `plan_for_version` | `authenticated` |
| C3 | «Esegui di prova» | `orchestrator/runtime_inputs.apply_test_run_inputs` → `_apply_own_runtime_inputs` | `test_run` |
| C4 | Valida con input, stima di Arricchisci, «Esegui» del progetto | `apply_project_runtime_inputs` | `preview` / `project_run` (blocca prima) |
| C5 | anteprima dell'Output e di un passo | `apply_preview_inputs` → `apply_identity_inputs(node_id=...)` | `preview` |
| C6 | valori distinti | `projects/router.distinct_node_values` → `apply_identity_inputs(node_id=...)` | `preview` |
| C7 | ricalcolo isolato a monte | `upstream_plan._resolve_entry` | come l'avvio |
| C8 | Valida (senza input) | `canvas_validation._canvas_issues` | — |
| C9 | anteprima della query | `query_preview.describe_estrai_query` | — |
| C10 | pubblicazione e ripubblicazione dell'Eseguibile, stato della sorgente | `eseguibili/service.create_from_project`, `publish_new_version`, `republish_preview` (stato della sorgente) | — |
| C11 | Pianificazioni, catene, Carica, worker | il nodo «Utente che esegue» resta nel canvas: `assert_identity_resolved` blocca (invariato) | nessuna |

Il valore dell'utente si scrive nel canvas solo in `runtime._resolve_into` (binding `_apply_scalar_binding`, sorgente `_as_identity_source`), chiamata solo da `resolve_runtime_inputs`; si calcola anche per l'impronta (`identity_values` in `preflight`), dopo `plan_for_version`.

## D. Esito della correzione

Correzione unica in due regole, nella sezione «Filtro "Utente che esegue" ignorato: correzione (11/11/2026)» di `docs/development/piano-fase5-2026-11.md`. Test: `backend/tests2/eseguibili/unit/test_identity_binding_effect.py` (enumerazione dei costruttori di SQL, un test per ogni modo B, enumerazione AST di chi scrive o calcola il valore, caso reale, non regressione degli Eseguibili in produzione) e `frontend/tests/shared/canvasGraph.contract.test.js`.
