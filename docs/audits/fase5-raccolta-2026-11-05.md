# Fase 5 — Raccolta dello stato dell'audit frontend (05/11/2026)

Base: main e4688ae6. Tre lettori indipendenti, sola lettura, voce per voce di `docs/audits/frontend-audit-2026-11-03.md` sul codice attuale, piu' difetti nuovi della stessa classe. Nota: i punti backend P1/P2 del rapporto A3 citati in fondo alla terza parte sono stati chiusi dopo (esecuzione a vuoto, terza sessione A3).

# Fase 5 — Raccolta R1–R4 sul codice attuale

**Base:** `main` = e4688ae6 (05/11/2026). **Fonte delle voci:** `docs/audits/frontend-audit-2026-11-03.md`, sezioni R1–R4.
**Metodo:** sola lettura, nessuna correzione. Percorsi relativi a `frontend/src/` salvo indicazione; righe del codice attuale.

**Legenda stato:** CHIUSA (meccanismo o test che la chiude per costruzione) · APERTA (scenario che la riproduce oggi) · PARZIALE (cosa manca) · NPA = non più applicabile (codice rimosso).
**Gravità** (dell'eventuale residuo): A alta · M media · B bassa.

## Conteggi

| Sezione | Voci | CHIUSA | PARZIALE | APERTA | NPA |
|---|---|---|---|---|---|
| R1 | 12 | 6 | 4 | 2 | 0 |
| R2 | 10 | 7 | 1 | 2 | 0 |
| R3 | 21 | 11 | 3 | 7 | 0 |
| R4 | 20 | 1 | 3 | 15 | 1 |
| **Totale** | **63** | **25** | **11** | **26** | **1** |

Più **14 difetti nuovi** della stessa classe (sezione in fondo) e 1 nota fuori classe.

---

## R1 — Mutazioni del canvas solo tramite azioni esplicite dello store

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| R1.1 Modifica di un join esistente non salvata | CHIUSA | Canali espliciti: `store/useCanvasStore.js:555-574` (`editNodes`/`editEdges`), `:743-760` (`updateEdge`), tutti con snapshot e dirty. Il pannello del join scrive con `updateEdge` (`components/canvas/JoinEdgePanel.jsx:68-74`). Test: `tests/shared/canvasMutationChannels.contract.test.js:62-71` (R1.1) e `:160-176`, che vieta `setNodes`/`setEdges`. | — |
| R1.2 `exec_params` tramite `useReactFlow().setNodes` | CHIUSA | `components/panels/ApiEnrichNode.jsx:45,139-143`: ora `updateNodeData` dello store. Il test di enumerazione vieta il setter di React Flow (`canvasMutationChannels.contract.test.js:171`). | — |
| R1.3 FilterModal muta `node.data` | CHIUSA | Copia delle righe all'apertura (`components/modals/FilterModal.jsx:56`) e aggiornamento immutabile (`:167-176`). | — |
| R1.4 Upload paralleli di `extra_files` si sovrascrivono | **APERTA** | `ApiEnrichNode.jsx:318-327`: `onUploaded` e `onRemoved` partono da `data.extra_files` del render in cui l'upload è iniziato; `ExtraFileSlot.handleFileSelected` (`:361-391`) lo chiama dopo l'`await`. **Scenario:** due campi `extra_input`; si sceglie il file per A e, mentre A carica, quello per B. Finisce A, che scrive `{A}`; poi finisce B, che scrive `{B}` partendo dalla copia vecchia: A sparisce. | A |
| R1.5 Patch nello stesso tick che si perdono | CHIUSA (per TransformNode/DynamicConfig) | `patchNodeConfig` fonde la patch con la config corrente dello store (`useCanvasStore.js:794-813`); TransformNode la usa (`components/transforms/TransformNode.jsx:112-123`). Test: `canvasMutationChannels.contract.test.js:136-142`. Resta un caso fuori dall'elenco originale: MultiJoin, vedi N5. | — |
| R1.6 Aggiunte senza snapshot; coalescenza globale | PARZIALE | Chiusa la parte delle aggiunte: toolbar, drop, palette, Carica e tabella correlata passano da `addNodes`, `insertNode`, `addSubgraph` e `insertBetween`, che fanno lo snapshot (`useCanvasStore.js:583-712`). **Manca:** la coalescenza resta globale e basata sul tempo (`SNAPSHOT_COALESCE_MS = 350`, `:33,373-375`). Due azioni diverse a meno di 350 ms l'una dall'altra (es. "Riordina" subito dopo un'aggiunta, o due clic su una tabella del catalogo) diventano un solo Ctrl+Z. | B |
| R1.7 Tasto destro su un arco lo elimina senza conferma | PARZIALE | Chiuso: ora si può annullare, e in sola lettura non fa nulla (`removeEdges`/`editEdges` fanno lo snapshot e sono no-op con `isReadOnly`). **Manca la conferma:** `pages/ManipolaEditor.jsx:208-211`, `pages/ArricchisciEditor.jsx:351`, `hooks/useEdgeHandlers.js:98-101`. In Estrai un tasto destro cancella un join con condizioni e filtri ON; il pannello invece chiede conferma (`components/canvas/NodeInspector.jsx:116-124`). | M |
| R1.8 Effetti che marcano "modificato" all'apertura | CHIUSA | Canale derivato `{derived:true}` / `applyDerived*` (`useCanvasStore.js:728-740,765-813`). Test di enumerazione: `tests/shared/mountWrites.contract.test.js` (nessuna scrittura utente negli effetti o nelle funzioni load/fetch/init) e `canvasMutationChannels.contract.test.js:90-101`. `_advOpen` è transitorio (`utils/nodeTypeRegistry.js:195`). | — |
| R1.9 Stato locale duplicato e stantio dopo un undo | **APERTA** | Quattro copie, nessuna risincronizzata con lo store: `components/nodes/TableNode.jsx:33` (`tempAlias`; "+ Aggiungi alias" e il clic sull'alias, `:148,161`, non lo reimpostano); `components/nodes/FeatureLayerNode.jsx:60-61,107-117,163` (`whereClause`/`tempWhere`); `components/transforms/PivotMappingBuilder.jsx:39` (`rows`, solo dallo stato iniziale); `components/nodes/StaticValuesNode.jsx:36,48-51` (`fields` locale, scritto nello store ma mai riletto). **Scenario:** pannello di un passo Valori statici aperto, si aggiunge un campo e si preme Ctrl+Z con il fuoco fuori dai campi. Lo store torna indietro, il pannello mostra ancora il campo, e la modifica successiva lo riscrive annullando l'undo. Stesso schema per il WHERE del layer: il badge ✓ resta e "Applica" riscrive il valore vecchio. | M |
| R1.10 In sola lettura i controlli restano attivi | PARZIALE | Lo store ignora le scritture (no-op), ma il corpo del pannello non è disabilitato (`NodeInspector.jsx:233-235`, niente `fieldset disabled` né `useNodeEditable`). Gestiscono `isReadOnly` solo TransformNode, ProjectInputNode (in parte), DataSourceNode (in parte), le griglie Output e ValueMapping. Restano attivi e senza effetto: TableNode, FeatureLayerNode, CaricanNode, CaricanArcGISNode, ApiEnrichNode, StaticValuesNode, MultiJoinNode, FileSource, ProjectSource, FilterModal, ColumnBuilderModal, e i campi Etichetta/Chiave di ProjectInputNode (`ProjectInputNode.jsx:136-147`). | M |
| R1.11 Eliminazione incoerente; lo switch DB↔REST azzera la config | PARZIALE | Chiuso: un solo "Elimina passo" con conferma per ogni tipo (`NodeInspector.jsx:197-205`; regola 6). **Manca:** (a) Canc/Backspace cancellano nodi e archi senza conferma (`hooks/useCanvasKeyboard.js:86-105`); (b) "REST" su un Carica DB configurato cancella connessione, schema, tabella e mapping senza conferma (`components/nodes/CaricanNode.jsx:66-83`; si recupera solo con Ctrl+Z, mentre il DataSourceNode chiede conferma, `DataSourceNode.jsx:88-101`). | M |
| R1.12 Cambio di tipo dell'input: edge rimossi prima dello snapshot | CHIUSA | `ProjectInputNode.jsx:150-161`: `removeEdges` (con snapshot) e poi `update`, nella stessa finestra di undo; anche `removeBinding` passa da `removeEdges` (`:120-123`). | — |

## R2 — Tabella dichiarativa dei tipi di nodo

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| R2.1 Carica: `create_unique_index` e `unique_key_column` persi | CHIUSA | Registro `utils/nodeTypeRegistry.js:67-68`; factory e serializer derivano da lì (`persistedNodeData`, `:207-218`). Test di round-trip per ogni tipo e campo: `tests/shared/nodeTypeRoundTrip.contract.test.js:24-45`. | — |
| R2.2 Flag `access_denied*` scartati | CHIUSA | Helper unico `backendAccessFlags` (`nodeTypeRegistry.js:185-188`), usato in entrambe le direzioni. Test `nodeTypeRoundTrip.contract.test.js:53-60`. | — |
| R2.3 DataSource: `file_offset` | CHIUSA | `nodeTypeRegistry.js:114` e round-trip. | — |
| R2.4 Arricchisci → Data Source: `datasource_config` non serializzato | CHIUSA | `nodeTypeRegistry.js:120` e lista di esclusione (`:195,211-213`); creazione in `ArricchisciEditor.jsx:241`. | — |
| R2.5 `carica_arcgis` contro `caricaArcgis` | CHIUSA | Alias nel registro (`nodeTypeRegistry.js:73`, canonico al salvataggio, `canvasSerializer.js:357`); handler (`useEdgeHandlers.js:32`); JoinModal rimosso; `availableColumns` è transitorio (`nodeTypeRegistry.js:195`). | — |
| R2.6 `datasource_node` contro `datasourceNode` | PARZIALE | Il salvataggio è canonico (`nodeTypeRegistry.js:99`), ma Manipola crea ancora i nodi nuovi come `datasource_node` (`ManipolaEditor.jsx:253`), mentre la deserializzazione restituisce `datasourceNode`: nella stessa sessione convivono due forme. Sono registrate entrambe (`ManipolaEditor.jsx:91`, `useEdgeHandlers.js:34`, `canvasGraph.js:37`); i confronti solo camelCase restano in `components/panels/QueryPreview.jsx:85-180`, che però è solo di Estrai. | B |
| R2.7 Carica: `update_columns`, `if_table_exists`, `allow_unsafe_append` azzerati; `input_kind` legacy | CHIUSA | `nodeTypeRegistry.js:69-71`. `input_kind` default `null`, e il default "senza tipo = file" è lo stesso del backend (piano M7). | — |
| R2.8 `spatial_distance` 0 → null; `connection_id` della tabella | CHIUSA | `utils/nodeFactories.js:44` e `canvasSerializer.js:61` usano `??`; `connection_id` è in `nodeTypeRegistry.js:19`. | — |
| R2.9 ApiEnrich: `parameters: []` blocca il ripiego | **APERTA** | `ApiEnrichNode.jsx:63`: `data.parameters \|\| fetchedApi?.parameters`. `[]` è truthy, quindi il ripiego non scatta mai, anche se `loadApiDetails` parte (`:79`). **Scenario:** nodo salvato con `parameters: []` (connessione senza parametri al momento della scelta), poi alla connessione vengono aggiunti parametri: il nodo continua a non mostrarli e non chiede di mapparli. | M |
| R2.10 Copia della connessione API mai aggiornata nel nodo | **APERTA** | Alla creazione il nodo copia `endpoint`, `auth` (mascherato), `parameters`, `output_mapping`, `static_parameters` e `openapi_spec` (`ArricchisciEditor.jsx:236-242`). Il nodo rilegge la connessione solo se mancano i parametri o il nome, e mai se c'è `endpoint` (`ApiEnrichNode.jsx:79`). **Scenario:** si aggiunge un parametro obbligatorio alla connessione in API Esterne; il nodo già nel canvas non lo vede e la validazione del nodo dice "Pronto". | M |

## R3 — Un solo modulo per colonne e grafo

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| R3.1 Manipola `refreshSchemas` riscrive le colonne | CHIUSA | Codice rimosso; la fonte unica è `utils/columnSource.js` + `store/useColumnStore.js` (una sola sottoscrizione, `EditorShell.jsx:136`). Le colonne derivate non vivono nei nodi (`nodeTypeRegistry.js:202-203`). | — |
| R3.2 Arricchisci: `availableColumns` aggrega tutte le sorgenti | CHIUSA | L'effetto è rimosso; i consumatori leggono `columnsOf(..., {side:'input'})` (`ApiEnrichNode.jsx:161`, `CaricanNode.jsx:55`). | — |
| R3.3 Validazione e stima sul primo `api_enrich` | CHIUSA | Prima di avviare la stima passa dal backend (regola 5; nessun `find(... 'api_enrich')` nel frontend). | — |
| R3.4 Mappa delle etichette dell'anteprima per `node.id` | CHIUSA | `ArricchisciEditor.jsx:72-74,444-451`: origine per id con `columnOrigin` (`columnSource.js:316-332`) e titolo da `nodeTitle`. | — |
| R3.5 Manipola: Output e Carica leggono lo schema salvato | CHIUSA | `ResultNode.jsx:85-86`, `CaricanNode.jsx:55`: fonte unica sul canvas corrente. | — |
| R3.6 Carica (Estrai): `availableColumns` vuoto dopo il reload | CHIUSA | Stesso meccanismo; l'early return è rimosso. | — |
| R3.7 Tre calcoli divergenti | CHIUSA | `calculateOutputColumnsRecursive` e `useColumnPropagation` rimossi (`utils/columnUtils.js` ha solo helper di tipo). Test di enumerazione: `tests/shared/columnSource.contract.test.js` e `ColumnReaders.contract.test.jsx`. | — |
| R3.8 Firma del ResultNode solo sui nomi | CHIUSA | Firma unica `columnSignature` (`columnSource.js:346-351`) sul contenuto serializzato. | — |
| R3.9 La propagazione ignora i cambi di tipo | CHIUSA | Propagazione rimossa; la firma include il canvas serializzato (con i tipi delle sorgenti), quindi un cambio di tipo rifà la richiesta. | — |
| R3.10 Multi-join: ordine, chiavi, `join_pairs` fantasma | PARZIALE | Chiuso l'ordine: i lati seguono le maniglie come il backend (`columnSource.js:146-166`; `MultiJoinNode.jsx:33-44`). **Manca:** con 3 o più sorgenti la UI nasconde le coppie (`MultiJoinNode.jsx:143,228-267`) ma il serializer continua a inviarle in `join_pairs`, `left_keys` e `right_keys`, e le coppie uguali entrano in `join_keys` (`canvasSerializer.js:377-388`). **Scenario:** 2 sorgenti con la coppia `id=id`, poi si collega una terza sorgente e si deseleziona `id` dalle chiavi comuni: `join_keys` torna a contenere `id`. | M |
| R3.11 L'anteprima del multi-join elimina colonne | CHIUSA | Colonne in uscita dal server (`MultiJoinNode.jsx:78`). | — |
| R3.12 La rinomina riscrive i letterali nelle espressioni | **APERTA** | La regola dell'id (`utils/expressionRefs.js:81-103`) limita quando si riscrive, ma non dove: `rewriteExpressionToken` usa `\bnome\b` anche dentro le stringhe (`:38-40`), e `expressionHasToken` conta come riferimento anche un letterale (`:28`). **Scenario:** Formula `stato == 'stato'` con la colonna `stato` rinominata a monte in `status` → `status == 'status'`: il letterale è cambiato. | A |
| R3.13 La preview SQL diverge | **APERTA** | `components/panels/QueryPreview.jsx` è invariato: mancano ORDER BY e HAVING (`:290-299`); ignora i `featureLayerNode` (`:84-86`); include tabelle scollegate (`:84`, tutte le tableNode); il filtro con valore numerico 0 sparisce (`:274`, `else if (filter.value)`); nessun `AS` per le colonne calcolate (`:103-107`); l'arco verso `carica_arcgis` non è escluso e diventa un JOIN (`:157-165`). In più incolla i valori dei filtri tra apici senza escape (`:231,276`). | M |
| R3.14 ApiEnrich: lo schema in uscita non si svuota | CHIUSA | Le colonne in uscita vengono dal server; `data.columns` è tolto dai nodi `api_enrich` (`nodeFactories.js:336-337`). | — |
| R3.15 FeatureLayer: `relationships` al drop, tre mappature dei tipi, alias come rinomina | **APERTA** | Il drop crea il nodo senza `relationships` né colonne (`pages/EstraiEditor.jsx:328-331`); `loadSchema` legge solo i campi (`FeatureLayerNode.jsx:84-91`), quindi "+ Join" compare solo per i layer aggiunti come correlati (`EstraiEditor.jsx:162`). Due ingressi, due regole: tipo `f.type` (`FeatureLayerNode.jsx:86`) contro `f.field_type \|\| f.type` (`EstraiEditor.jsx:154`); alias `f.alias` (cioè l'alias di visualizzazione ArcGIS, `backend/app/core/features/arcgis_read.py:160`) contro `alias: null`. Lo stesso layer prende nomi di colonna diversi secondo come è stato aggiunto (`columnSource.js:210`: `alias \|\| name`). | M |
| R3.16 GROUP BY e ORDER BY deduplicati per nome | **APERTA** | `components/nodes/result/ResultNodeStandardView.jsx:131` (Estrai, `Map` per `col.name`) e `:134` (Manipola). **Scenario:** `comuni.id` e `province.id` → una sola voce "id", e non si può raggruppare per `province.id`. | M |
| R3.17 HAVING con GROUP BY spento, alias `count_N`, colonne stantie | PARZIALE | L'HAVING con GROUP BY spento non arriva all'SQL (`backend/app/core/estrai/query_projection.py:320-321`), ma resta nei dati e riappare quando si riattiva il GROUP BY (`GroupOrderHavingPanel.jsx:86-92`). **Aperti:** alias `count_${aggregations.length + 1}` (`:112`), che collide dopo una rimozione; cambiare colonna o funzione sovrascrive l'alias scritto dall'utente (`:123-127`); le colonne del GROUP BY sparite a monte non vengono segnalate. | M |
| R3.18 La select della geometria mostra `[0]` | **APERTA** | `components/nodes/result/standard/OutputSettingsPanel.jsx:145`: `value={selectedGeomColumn \|\| availableGeomColumns[0]}`. Con un `geomColumn` stantio (non tra le opzioni) il DOM mostra la prima opzione mentre il dato salvato resta quello vecchio; con `null` mostra `[0]` ma invia `null`. | M |
| R3.19 Il progetto corrente tra le sorgenti trascinabili | **APERTA** | `hooks/useEditorData.js:185` filtra solo per tipo; `components/panels/ProjectsSidebar.jsx:29-36` idem. **Scenario:** in Manipola si trascina il progetto stesso come `project_source` (ciclo). | M |
| R3.20 Self-join senza alias; `mapApiTypeToStandard('int')` | PARZIALE | La parte self-join non si applica più (calcolo rimosso). **Aperto:** `utils/columnUtils.js:56-76` non mappa `int`, `real` né `str` (restituisce `'str'` per `'int'`). Lo usa solo l'icona delle colonne aggiunte dall'API (`ApiEnrichNode.jsx:525`): un campo `int` appare come testo. | B |
| R3.21 ColumnSelector: colonna sparita a monte | **APERTA** | `components/transforms/parameter-inputs/ColumnSelector.jsx:92-104`: con un valore assente dalle opzioni la select mostra il segnaposto e la config tiene il nome vecchio. Valida ora lo segnala (A3), ma il pannello mostra "Seleziona colonna" come se fosse vuoto. | M |

## R4 — Risorse e task asincroni: un solo hook

`useResource`, `useTask` e `<ListState>` non esistono: nessuna primitiva condivisa. Il test di architettura previsto ("nessuna pagina con `useEffect` + fetch fuori da `useResource`") manca.

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| R4.1 Ticket: dati e bozza tra ticket diversi | **APERTA** | `pages/TicketDetail.jsx:60,70-81,113-124`: nessun id di richiesta; `commentBody` non è legato all'`id`; `setCommentBody('')` dopo l'invio cancella anche il testo scritto durante la richiesta. | A |
| R4.2 Eseguibili: l'analisi del file A finisce sul file B | **APERTA** | `components/eseguibili/ExecutableDynamicRunForm.jsx:52-75`: `analyzeFile` scrive per indice dopo l'`await`. **Scenario:** si sceglie A e si clicca Analizza, poi si sceglie B prima della risposta: B riceve `analysis.file_id` di A e "Esegui" si abilita con il file sbagliato. | A |
| R4.3 FileManager: preview di A usata per B; layer e foglio non azzerati | **APERTA** | `pages/FileManager.jsx:137-141` (`handleFileSelect` non azzera `uploadLayer`/`uploadSheet` né lo stato delle colonne); `:209-260` (`handleNextStep` senza guardia di richiesta); `:142-156` (`_loadColumnsForLayer`: tab diverse si contendono `loadingLayerCols`). | A |
| R4.4 ColumnBuilder: la SQL tradotta è dei parametri precedenti | **APERTA** | `components/modals/ColumnBuilderModal.jsx:136-157,193-233`: debounce senza id di richiesta; una traduzione vecchia arriva dopo e riscrive `sqlPreview`. In più, se la traduzione fallisce resta la SQL precedente (`:204-207`) e "Salva" la salva (`:241`). | A |
| R4.5 Race varie (Log, Ticket, browser dei Data Source, Object Storage, System Status, Task Runtime, RunHistory, servizi e layer ArcGIS, test del connettore, seed del wizard) | **APERTA** | Nessuna guardia di richiesta in `components/panels/FeatureServicePanel.jsx`, `pages/DataSourceManager.jsx`, `components/modals/RunHistoryModal.jsx`, `pages/AdminObjectStoragePage.jsx`, `components/api-conn/ApiConnectionWizard.jsx`, `wizard-steps/Step5TestSave.jsx`, `components/logviewer/RunDetail.jsx`, `pages/LogViewer.jsx:78-93`, `pages/TicketsList.jsx:84-99`, `DataSourceNode.jsx:103-117` (browser), `CaricanArcGISNode.jsx:180-205` (servizi e layer). La sotto-voce "validazione dell'enrichment" non si applica più (stima nel backend). | M |
| R4.6 Cambio progetto nella stessa route | PARZIALE | Chiuso: salvataggio e ripristino usano progetto e versione dello snapshot (`useCanvasStore.js:60-89,105-167,185-200`); lo store si azzera al cambio di `projectId` (`hooks/useCanvasCore.js:201-204`). **Manca:** `key={projectId}` sulle route (`App.jsx:120,125,127`); `useExecution` non è legato al progetto (polling e `setLastTaskResult` di A dopo il passaggio a B, `hooks/useExecution.js:186-216`); `handleAddRelatedLayer` (`EstraiEditor.jsx:144-170`) aggiunge al canvas corrente dopo un `await`. Oggi nessun link interno porta da un progetto a un altro nella stessa route, quindi è poco raggiungibile. | B |
| R4.7 Ogni reload sostituisce la pagina con lo spinner | PARZIALE | Chiuso in ScheduleList (`loadedOnceRef`, `pages/ScheduleList.jsx:187`). Restano: `pages/ProjectList.jsx:280` (skeleton a ogni `reload`, anche dopo un'azione), `pages/ConnectionManager.jsx:208`, `pages/FileManager.jsx:367`, `pages/TicketDetail.jsx:195-201` (dopo ogni commento o cambio di stato), `pages/UserManager.jsx:458`. | M |
| R4.8 Errore mostrato come stato vuoto; TicketDetail pagina bianca | **APERTA** | `useEditorData.js:165-169` (catalogo → `{}`, "Nessuna tabella trovata"), `:187-198` (sorgenti e file → `[]`, "Nessun progetto disponibile"), `:233-236` (connessioni); `TicketDetail.jsx:203` (`if (!ticket) return null`: pagina bianca per un errore diverso da 404); `DataSourceNode.jsx:73-82` (errore silenzioso); `ProjectInputNode.jsx:97` (set di connessioni → `[]`). | M |
| R4.9 ProjectList: errore negli stati di esecuzione → "Mai eseguito" | **APERTA** | `components/projects/useProjectListData.js:114` (ripiego `{statuses:{}}`), escluso di proposito dall'avviso (`:117-124`). | M |
| R4.10 `useInlinePreview` senza import di `humanizeError` | CHIUSA | `hooks/useInlinePreview.js:11`; gate di lint (Fase 1, `no-undef`). | — |
| R4.11 Modale di esecuzione di Estrai senza `onCancel` né progresso; polling senza `onError` né timeout | **APERTA** | `EstraiEditor.jsx:584-590` e `ManipolaEditor.jsx:513` non passano `progress`, `progressMessage`, `taskStatus` né `onCancel` (solo Arricchisci li passa, `ArricchisciEditor.jsx:437`); per i progetti non Arricchisci "Chiudi" è disabilitato durante l'esecuzione (`components/modals/ExecutionModal.jsx:117-121`); `startMainPolling` non ha `onError` né timeout (`useExecution.js:186-216`). **Scenario:** il task resta in 404 o 5xx, `isExecuting` resta vero, `runState` resta `'running'` e "Esegui" è spento fino al ricaricamento della pagina. | A |
| R4.12 Polling infinito su 404 o 5xx; `revoked` nelle anteprime; `cancelledRef` | **APERTA** | `hooks/useTaskPolling.js:427-440`: 404 sempre ritentato, e senza `onError` anche il 5xx (nessun chiamante passa `onError`: useExecution, usePreview, useInlinePreview, `ExecutableDynamicRunForm.jsx:83-107`). `usePreview.js:69-101` e `useInlinePreview.js:195-209` non gestiscono `revoked` (spinner per sempre). Il `cancelledRef` di usePreview non viene mai messo a true: `closeModal` (`:119-121`) non ferma il polling. | M |
| R4.13 LogViewer: Live torna a pagina 1, riga espansa per posizione, log tecnici chiesti ai non-admin, dettaglio della run non aggiornato | **APERTA** | `LogViewer.jsx:113-114` (`loadLogs(0)` ogni 5 s); `:282` (`expandedRow === idx`); `:105` (`loadLogs` e `loadStats` partono anche per chi non ha `canSeeTechnicalLog`); `components/logviewer/RunsTab.jsx:67` (a ogni `refreshTick` torna all'offset 0); `RunDetail.jsx:28` (caricato una sola volta). | M |
| R4.14 Admin: polling con la tab nascosta; "Aggiorna" in race; TaskRuntime cancella l'errore | **APERTA** | `pages/AdminSystemStatusPage.jsx:345-357` e `pages/AdminTaskRuntimePage.jsx:61-72`: nessun controllo `visibilityState`; `load` senza id di richiesta; `AdminTaskRuntimePage.jsx:31` (`setError('')` a ogni poll cancella l'errore di `resolveRun`, `:47,55`). | M |
| R4.15 AdminBackups: "Caricamento..." infinito; `setTimeout` non annullato; backup non seguito | **APERTA** | `pages/AdminBackupsPage.jsx:101,134`: `StatusCard` e `ScheduleCard` mostrano "Caricamento..." per sempre se quella chiamata fallisce; `:276-278` (`setTimeout` non annullato, legge `awsCredentialsError` di quel render); un solo reload dopo 5 s, nessun polling fino alla fine. | M |
| R4.16 Schedule: tempo trascorso, `queued` come running, run automatiche non rilevate, tick ogni secondo | **APERTA** | `ScheduleList.jsx:124-131,203` (`Date.now()` di quando la pagina vede la run); `:125` (`queued` trattato come `running`); `:136-139` (il polling parte solo se c'è già una run in corso); `:152` (`setInterval` da 1 s che ridisegna tutta la lista). | M |
| R4.17 Carica ArcGIS, schema e tabelle del Carica DB, auto-mapping: errori solo in console | PARZIALE | Chiusi: spinner e annullamento per schema e tabelle DB (`components/nodes/carica/useCaricaSchemaSelect.js:16-54`); errori visibili per servizi e layer ArcGIS (`CaricanArcGISNode.jsx:180-205`). **Restano** solo in console: schema e tabelle DB (`useCaricaSchemaSelect.js:28,48`), connessioni, cartelle e schema del layer ArcGIS (`CaricanArcGISNode.jsx:173-174,213-214,226-227`), auto-mapping (`carica/ColumnMappingPanel.jsx:38-39`). | M |
| R4.18 useEditorData: caricamenti in serie | **APERTA** | `useEditorData.js:209-248`: progetto → connessioni → catalogo → progetti → file, tutti in sequenza. | M |
| R4.19 Connection set: flag "caricato" impostato prima della risposta | NPA | Il codice in EstraiEditor è rimosso; i set si caricano in `ProjectInputNode.jsx:43-52,93-100` (con una nuova cache problematica, vedi N4). | — |
| R4.20 Registry delle trasformazioni in errore → "Caricamento..." permanente | **APERTA** | `utils/transformRegistry.js:39-43` restituisce `null` in caso di errore; `TransformNode.jsx:49-55` non ritenta, mostra "Caricamento operazioni…" e la select resta disabilitata (`:149`); la palette di Manipola resta vuota senza nuovo tentativo (`ManipolaEditor.jsx:279-285`). | M |

---

## Nuovi difetti (stessa classe, non presenti nell'audit)

| Id | Classe | File:riga | Scenario | Grav. |
|---|---|---|---|---|
| N1 | R3/R4 (caricamenti legati al pannello) | `pages/EstraiEditor.jsx:328-331`; `components/nodes/FeatureLayerNode.jsx:73-77`; `components/canvas/compactNodeTypes.js:19-21` | Dalla Fase 4 i form completi vivono solo nel pannello laterale, quindi gli effetti "al montaggio" girano solo quando si apre il pannello. Un layer ArcGIS trascinato nasce con `columns: []` e lo schema si carica solo selezionandolo. Collegato all'Output senza aprirlo, i server-schemas e la SELECT non hanno colonne del layer. Stessa dipendenza per `loadApiDetails` e l'auto-mapping di ApiEnrich (`ApiEnrichNode.jsx:78-83,165-189`) e per `fetchSchema` del DataSource (`DataSourceNode.jsx:178-184`). | M |
| N2 | R4 (race) | `hooks/useInlinePreview.js:170-195`; `hooks/usePreview.js:39-69` | Nessun id per richiesta. Anteprima del passo A, poi B prima che `previewProject(A)` risponda: se A risponde dopo B, `startPolling(A)` sostituisce B e sotto "Dati dopo «B»" compaiono i dati di A (`components/canvas/StepPreview.jsx:89-100`). Stesso schema per "Aggiorna" con limiti diversi in PreviewModal. | M |
| N3 | R4 (race) | `components/nodes/DataSourceNode.jsx:126-176` | `fetchSchema` senza guardia: si sceglie il file X e poi Y; lo schema di X arriva dopo e viene scritto (derived, poi salvato) come colonne di Y. | M |
| N4 | R4 (risorse e cache) | `components/nodes/ProjectInputNode.jsx:26-52` | Cache di modulo `registryCache` e `connectionSetsCache` mai invalidate: diversamente da `utils/transformRegistry.js:48-53` non si svuotano al logout, quindi l'utente successivo nella stessa scheda vede i connection set del precedente. Un set creato dopo il primo caricamento non compare fino al ricaricamento della pagina. È anche una seconda copia del registry fuori dalla cache condivisa. | M |
| N5 | R1.5 (patch su config del render) | `components/transforms/MultiJoinNode.jsx:58-63` | `updateConfig` fa `updateNodeData(id, {config: {...config, ...patch}})` con la `config` del render e non usa `patchNodeConfig`: due modifiche nello stesso tick (o con il render in ritardo) si sovrascrivono. Non lo copre il test R1.5, che verifica solo lo store. | B |
| N6 | R2 (campo perso al round-trip) | `utils/canvasSerializer.js:392-408`; `utils/estraiJoins.js:293-299,314-318` | `serializeEdge` non conserva `data.suggestion`: un join nato da una relazione ArcGIS, "da completare", perde al reload le chiavi suggerite. Per gli archi non c'è un registro dichiarativo né un round-trip per ogni campo come per i nodi. | B |
| N7 | R3 (ripiego dichiarato) | `utils/columnSource.js:200-201` contro `components/nodes/StaticValuesNode.jsx:69-74` | Il ripiego legge `f.type`, ma il nodo scrive `field_type`: prima della risposta del server tutti i Valori statici risultano `str`. | B |
| N8 | R1.9 (stato locale) | `components/modals/FilterModal.jsx:52-68` | L'effetto di apertura dipende da `edges` (e da `filters`): un cambio degli archi con la finestra aperta riporta le righe locali al valore salvato e si perde quello che si stava scrivendo. Probabilità bassa (la finestra copre il canvas). | B |
| N9 | R1/R3 (letture non reattive) | `components/panels/ApiEnrichNode.jsx:105-138` | `connectedFileSource` e `upstreamAccessDenied` leggono `getNodes()`/`getEdges()` in un `useMemo` che dipende solo dal numero di archi in ingresso. Se cambia la sorgente a parità di numero di archi (es. "Collega"/insertBetween) o cambiano i flag `access_denied` della sorgente, lo stato del nodo resta quello vecchio. | B |
| N10 | R4.17 (errori solo in console) | `components/nodes/FeatureLayerNode.jsx:93-94`; `pages/EstraiEditor.jsx:332` | L'errore di `getLayerSchema` del layer finisce in console, senza messaggio né "Riprova"; il layer resta con "Nessun campo". Anche il drop con JSON non valido va solo in console. | M |
| N11 | R2 (lista di esclusione) | `pages/ArricchisciEditor.jsx:236-242`; `utils/nodeTypeRegistry.js:207-218` | Con la lista di esclusione, nel canvas si salvano copie della connessione: `auth` (mascherato), `openapi_spec` intero, `endpoint`. JSON del canvas gonfiato e copie che invecchiano (radice di R2.10); nessun test segnala un campo "copia della risorsa". | B |
| N12 | R4.12 (polling) | `components/eseguibili/ExecutableDynamicRunForm.jsx:83-107` | Il polling degli Eseguibili usa `useTaskPolling` senza `onError`: un task sparito (404) o un 5xx persistente lasciano la finestra in "esecuzione" per sempre (`isRunning`, `:39`). | M |
| N13 | R4.20 (risorse senza errore) | `pages/ManipolaEditor.jsx:279-285` | Se il registry non arriva, l'effetto non ritenta (dipende da `transformRegistry`, che resta `null`): la palette "Cosa vuoi fare?" resta senza operazioni, senza messaggio né "Riprova". | M |
| N14 | R3.13 (preview SQL) | `components/panels/QueryPreview.jsx:224-232,271-279` | Oltre all'elenco dell'audit: i filtri collegati a un input runtime e gli operatori IN e BETWEEN sono mostrati con il valore statico e senza parentesi (`IN valore1, valore2`), diversi dalla SQL eseguita. | B |

### Nota fuori classe (R7, da non perdere)

- `components/common/CommandPalette.jsx:67-72`: Ctrl+K → "File", "Log", ... naviga fuori dall'editor senza `confirmLeaveWithUnsaved` (`hooks/useUnsavedChangesGuard.js:28-30`). Le modifiche non salvate si perdono, salvo la copia di sicurezza locale.


---

# Raccolta Fase 5 — R5, R6, R7, R8 (stato sul codice attuale)

Base: repo `C:\Users\Marco\Desktop\plenora`, HEAD e4688ae6. Sola lettura, nessuna correzione.
Percorsi relativi a `frontend/src/` salvo indicazione. `npx eslint src` (gate no-undef + rules-of-hooks) esce pulito.

Conteggi: **CHIUSA 6 · PARZIALE 9 · APERTA 33 · NON PIU' APPLICABILE 0** (48 voci).

## R5 — Toast, sessione ed errori

| voce | stato | prova / scenario | grav. |
|---|---|---|---|
| R5.1 | CHIUSA | `context/ToastContext.jsx:81-84`: il value contiene solo azioni stabili (`useCallback([])`), la lista `toasts` resta interna al provider e arriva solo a `ToastContainer`. Test: `tests/context/ToastContext.contract.test.jsx`. | A |
| R5.2 | APERTA | `api/http.js:127-131`: ogni 401 (tranne `/auth/logout`) cancella il token **attuale** di sessionStorage e chiama `authBus.onUnauthorized` senza confrontarlo con il token che la richiesta aveva inviato (`http.js:29-31`) e senza deduplica. Scenario: polling o fetch partiti con il token A; l'utente esce e rientra (token B); la risposta 401 tardiva della richiesta A cancella B e fa logout della sessione nuova. Con N richieste parallele scadute, `logout()` + `navigate('/login')` + `emitBeforeLogout` girano N volte (`context/AuthContext.jsx:71-74`). | M |
| R5.3 | APERTA | `api/users.js:7` `getMe` è definito ma non lo usa nessuno; ruolo e gruppo vengono solo da sessionStorage (`AuthContext.jsx:21-29`). Un admin declassato vede ancora `/admin/*` (`App.jsx:83-107`) finché il token non scade. | M |
| R5.4 | APERTA | `components/nodes/ProjectInputNode.jsx:28,43-52`: `connectionSetsCache` di modulo, mai azzerato (nessun `authBus.addLogoutListener`). Dopo un cambio di account nella stessa scheda il select «Set di connessioni» mostra i set dell'utente precedente. Restano stantii anche i set creati dopo il primo caricamento (non si ricarica mai). | M |
| R5.5 | PARZIALE | Chiuso: `reset()` ora azzera `lastTaskResult` (`store/useCanvasStore.js:359`). Manca: il logout non chiama `useCanvasStore.getState().reset()` (nessun listener in `utils/authBus.js`, `AuthContext.jsx:37-66`), e nemmeno `useColumnStore`. Il grafo dell'utente precedente resta in memoria finché non si apre un altro editor. | B |
| R5.6 | CHIUSA | Wrapper senza hook + corpo con gli hook: `components/shared/AllowedGroupsSelector.jsx:34-38`, `components/transforms/DynamicConfig.jsx:29-58`. Gate `react-hooks/rules-of-hooks` in `frontend/eslint.config.mjs:32` (CI `npm run lint`); test `AllowedGroupsSelector.contract.test.jsx`. | A |
| R5.7 | PARZIALE | Chiuso: `vite:preloadError` gestito (`utils/chunkReload.js:16-31`, installato in `index.jsx:16`); testo "URBY-ETL" sostituito da "Plenora" (`ErrorBoundary.jsx:99`); `CanvasErrorBoundary` dentro `EditorShell.jsx:398`, `ModalErrorBoundary` in Estrai e Arricchisci. Manca: (a) per le pagine non editor (admin, Data Source, Connessioni, Scheduler, Ticket, /run) c'è solo `AppErrorBoundary`, fuori dal Router (`App.jsx:154`): un crash in una pagina sostituisce tutta l'app e non si resetta navigando; (b) nessun `Sentry.captureException` in `componentDidCatch` (`ErrorBoundary.jsx:38,89,135`): in produzione React non rilancia gli errori catturati da un boundary, quindi Sentry (`index.jsx:26-37`) non li vede. | M |
| R5.8 | PARZIALE | Chiuso: il 404 delle condivisioni si riconosce da `response.status`; `toastApiError` e `humanizeError` esistono. Manca: le normalizzazioni sono ancora più d'una (`api/http.js:72-92`, `api/errors.js:46`, `utils/errorMessages.js`, `AdminBackupsPage.jsx:36-46`, `useCanvasStore.js:139`, `utils/backendValidationResult.js:74`) e restano toast con `err.message` grezzo, che può valere "Network Error" o "timeout of …ms exceeded" (http.js:91): `ScheduleList.jsx:210-211` e `:266-267`, `ApiConnectionManager.jsx:147-148`, `shared/AllowedGroupsSelector.jsx:175-177`, `logviewer/RunsTab.jsx:57-59`, `ConnectionManager.jsx:98-106`; nei banner e campi errore: `DataSourceManager.jsx:129,164`, `FileManager.jsx:206,252,258,286`, `UserManager.jsx:274`, `ChangePasswordModal.jsx:43`, `AdminSystemStatusPage.jsx:339`, `ApiConnectionWizard.jsx:452`, `DataSourceNode.jsx:112`, `FeatureServicePanel.jsx:184`, `ResponseConfigBuilder.jsx:194`. Il test "nessun err.message passato ai toast" proposto dall'audit non esiste. | M |
| R5.9 | APERTA | `context/AuthContext.jsx:32` richiama `loadFromStorage()` (con `JSON.parse`) a ogni render del provider; `:99` `value={{ user, token, login, logout }}` non è memoizzato, quindi ogni render di AuthProvider ri-renderizza tutti i consumer di `useAuth`. | B |
| R5.10 | APERTA | `hooks/useTheme.js:32` `localStorage.getItem` e `:45` `localStorage.setItem` senza try/catch. Con lo storage bloccato (cookie di terze parti disattivati o criteri aziendali) `ensureInit` lancia durante il primo `useState`, quindi crash della LoginPage e dell'intera app. | B |

## R6 — Stack di modali e wizard

| voce | stato | prova / scenario | grav. |
|---|---|---|---|
| R6.1 | APERTA | `hooks/useDialogFocus.js:105`: ogni dialog aperto registra un `keydown` su `document`, senza uno stack. Esc su un ConfirmDialog aperto sopra un'altra modale (per esempio la conferma della copia di sicurezza o "Salva e continua" in `EditorShell.jsx:240-249`) chiude entrambe; con due dialog aperti anche il Tab-trap si contende il focus. `ModalWrapper.jsx:59-67`: chiudendo la modale interna `body.overflow` torna `''` mentre l'esterna è ancora aperta. `ModalWrapper.jsx:93,100`: `id="modal-title"` è fisso, quindi duplicato con due modali (aria-labelledby ambiguo); lo stesso vale per `id="wizard-title"` (`Wizard.jsx:86,93`). | A |
| R6.2 | APERTA | `components/common/Wizard.jsx:106`: un clic sulla barra degli step chiama `goTo(i)` senza `validate`/`canAdvance` (`allowJump` di default è true, `:43`). In `ApiConnectionWizard` (`:597-607`) e in `DataSourceConfigModal` (`:192-203`) si salta da Tipo a Test e si salva: un connettore senza parametri o senza campo file (`validateParamsStep` aggirato) oppure un Data Source senza credenziali (`completeDisabled` non le controlla). Solo ScheduleWizard ha un `goToStep` che valida (`ScheduleWizard.jsx:158-161`). | A |
| R6.3 | APERTA | `components/api-conn/ApiConnectionWizard.jsx:351-365`: `handleSave` non ha flag di busy e il `<Wizard>` (`:597-607`) non riceve `completing`, quindi un doppio clic su "Salva API" crea due connettori. Lo stesso per `DataSourceConfigModal.jsx:57,192` (`onSave` senza stato). | A |
| R6.4 | APERTA | `Wizard.jsx:49-50`: `closeOnEsc` e `closeOnOverlay` sono true di default. ApiConnectionWizard (6 step) e DataSourceConfigModal non li cambiano: Esc o un clic fuori perdono tutto il form senza conferma. | M |
| R6.5 | APERTA | `ModalWrapper.jsx:70-73` controlla solo `e.target === e.currentTarget` sul `click`. Se il mousedown parte dentro la modale (per esempio selezionando del testo) e il mouseup avviene sull'overlay, il click viene emesso sull'overlay e la modale si chiude. In `Wizard.jsx:79` è peggio: `onClick={onClose}` senza alcun controllo. Stessa classe negli overlay fatti a mano: `CommandPalette.jsx:84`, `nodes/carica/ConflictModal.jsx:20`, `ApiConnectionManager.jsx:378,409,456`, `DataSourceManager.jsx:272,412,516`, `EseguibiliList.jsx:269,326`. | M |
| R6.6 | APERTA | `ModalWrapper.jsx:103-111`: la × resta attiva qualunque sia lo stato (`loading` non la disabilita). Esempi: `ConnectionManager.jsx:310-317` (Esc e overlay sono bloccati durante `saving`, la × no), `UserManager.jsx:84-97` (UserModal: Annulla e × attivi durante `loading`), `TicketCreateModal.jsx:26,74` (× ed Esc attivi durante `submitting`). `FileManager/UploadModal.jsx:146-147,173-184`: Annulla, ×, Esc e overlay restano attivi durante l'upload; la richiesta continua e poi `resetUploadModal` agisce sulla modale riaperta. | M |
| R6.7 | PARZIALE | Chiuso: Canc/Backspace agiscono solo con il focus sul canvas (`hooks/useCanvasKeyboard.js:87-89`). Manca: Ctrl+Z, Ctrl+Y e Ctrl+Shift+Z (`:70-82`) agiscono con qualsiasi target che non sia INPUT, TEXTAREA o SELECT. Con una modale aperta e il focus su un pulsante (anche la × che riceve il focus iniziale, R6.8) Ctrl+Z annulla le modifiche del canvas dietro la modale. EditorShell invece controlla `[role="dialog"]` (`EditorShell.jsx:221,264`): la regola non è unica. | M |
| R6.8 | APERTA | `useDialogFocus.js:63-65`: il focus va sul primo elemento focusabile, cioè la × dell'header di ModalWrapper o Wizard; questo annulla gli `autoFocus`, per esempio "Nome connessione" (`ConnectionManager.jsx:346`) e "Nuova password" (`UserManager.jsx:206`). | B |
| R6.9 | APERTA | `hooks/useConfirmModal.js:26-29`: una seconda `confirm()` sovrascrive `resolveRef` e la prima promise resta pending per sempre. Scenario: `ConfirmProvider` condiviso dai nodi (`ConfirmContext.jsx:42-49`) e due richieste ravvicinate (copia di sicurezza + "Salva e continua", oppure doppio Elimina da tastiera). | B |
| R6.10 | PARZIALE | Chiuso: TemplateLibraryModal ora usa ModalWrapper (`arricchisci/TemplateLibraryModal.jsx:137`). Restano: `window.prompt` in `AdminTaskRuntimePage.jsx:40-49`; `window.confirm` per ogni uscita con modifiche (`hooks/useUnsavedChangesGuard.js:29`) e come fallback di `ConfirmContext.jsx:78-81`; dialog `role="dialog"` fatti a mano senza `useDialogFocus`: `CommandPalette.jsx:85-91` e `canvas/NodePalette.jsx:89`; dialog fatti a mano con useDialogFocus ma senza la × o le regole di ModalWrapper: `DataSourceManager.jsx` (4), `ApiConnectionManager.jsx` (3), `EseguibiliList.jsx` (2), `ConflictModal.jsx`, `TicketCreateModal.jsx`, `EseguibileExecuteWizard.jsx`. | M |

## R7 — Guard di navigazione

| voce | stato | prova / scenario | grav. |
|---|---|---|---|
| R7.1 | PARZIALE | Chiuso: Indietro, "Connessioni" e logout degli editor chiedono conferma (`EstraiEditor.jsx:347,391,561`, `ManipolaEditor.jsx:457`, `ArricchisciEditor.jsx:376`, `panels/EditorSidebar.jsx:67`); in più la copia di sicurezza locale (`hooks/useLocalDraft.js`) rende recuperabili le modifiche. Manca: (a) CommandPalette (Ctrl+K) naviga senza guard (`common/CommandPalette.jsx:67-72`); (b) il tasto Indietro del browser e i link interni non sono intercettati: `BrowserRouter` (`App.jsx:155`), nessun `useBlocker`/data router; `beforeunload` (`useUnsavedChangesGuard.js:33-45`) copre solo reload e chiusura; (c) la conferma è `window.confirm` (R6.10). | A |
| R7.2 | CHIUSA | `modals/CanvasVersionsModal.jsx:59,118`: "Salva versione attuale" è disabilitato con modifiche non salvate e un avviso lo spiega (`:103-107`). Il ripristino è una scrittura condizionata alla versione della scheda (`useCanvasStore.js:186-201`); `markClean()` avviene prima del reload (`utils/serverActions.js:61-66`), quindi non c'è un prompt da annullare né una sovrascrittura successiva. | A |
| R7.3 | CHIUSA | Nessun link a `/api-connections` nel codice delle pagine; test `tests/shared/internalLinks.contract.test.js` confronta ogni link interno con le Route di `App.jsx`. | B |
| R7.4 | APERTA | `pages/LogViewer.jsx:322`: `navigate(\`/project/${entry.project_id}\`)` vale per qualsiasi tipo di progetto, e l'editor Estrai non controlla `project_type`. Un log di un progetto Manipola o Arricchisci apre il canvas nell'editor Estrai. La mappatura tipo→rotta esiste solo in `ProjectList.jsx:198-202` (nessuna costante `ROUTES` condivisa). | M |
| R7.5 | APERTA | `App.jsx:58,73,86,101`: `<Navigate to="/login">` senza `state.from`, e `LoginPage.jsx:124,135` porta sempre a `landingPathFor(user)`: dopo il login o un 401 non si torna alla rotta richiesta. `App.jsx:146`: il catch-all reindirizza a `/` (nessuna pagina 404). `navigate(-1)` aperto da un link diretto esce dall'app: `ApiConnectionManager.jsx:239`, `ConnectionManager.jsx:219`, `DataSourceManager.jsx:177`, `FileManager.jsx:380`, `LogViewer.jsx:153`, `ScheduleList.jsx:355`. | B |

## R8 — Form dichiarativi e input con buffer locale

| voce | stato | prova / scenario | grav. |
|---|---|---|---|
| R8.1 | APERTA | `nodes/ProjectInputNode.jsx:19-24,144`: `normalizeKey` toglie i `_` finali a ogni tasto, quindi digitando "file_" diventa "file" e "file_clienti" non si può scrivere (solo incollando). `:226-231` (Colonne attese), `:242` (Estensioni), `:279` (Opzioni): split/trim/filter a ogni tasto e il valore mostrato viene ricostruito con `join(', ')`, così la virgola appena digitata sparisce. | A |
| R8.2 | APERTA | `connections/ConnectionSetsPanel.jsx:227`: la key `${index}:${member.selector_key}` rimonta la riga a ogni carattere della chiave e il focus si perde. `:19-24,243`: `normalizeKey` toglie i `_`, `-` e `.` finali. `:235`: la chiave derivata dall'etichetta si fissa al primo carattere (`member.selector_key \|\| normalizeKey(label)`). | A |
| R8.3 | APERTA | `transforms/PivotMappingBuilder.jsx:63-65`: al primo carattere della chiave `val` viene precompilato con quel carattere e poi non si aggiorna più ("canone" dà "c"). | A |
| R8.4 | APERTA | `parseInt(v)\|\|default` ancora in: `api-conn/wizard-steps/Step1BasicInfo.jsx:86,97`, `Step2Params.jsx:118,160,162,193`, `Step3Auth.jsx:111`, `Step4Batch.jsx:29`, `modals/ScheduleWizard.jsx:414`, `nodes/carica/DbAdvancedOptions.jsx:51`, `nodes/carica/RestDestinationSection.jsx:171,181`, `pages/ConnectionManager.jsx:368` (porta). Svuotando il campo torna subito il default, quindi digitando "5" si ottiene "1005". | B |
| R8.5 | APERTA | `pages/DataSourceManager.jsx:120-121`: il payload scarta i valori `''`. In modifica (PUT parziale) un campo non si può svuotare (per esempio `root_path`, `site_id`). Vengono inviati anche i campi degli altri tipi rimasti nel form (`tls`, `passive`, `host_key_policy` sempre; `host` e `username` dopo un cambio di tipo, `handleTypeChange` a `:110`). Gli obbligatori non sono validati oltre al nome e al sito SharePoint (`:116-117`): un FTP senza host si salva. | M |
| R8.6 | CHIUSA | `arricchisci/DataSourceConfigModal.jsx:84`: il cambio di tipo azzera `credentials: {}`. (Resta R6.2: `canAdvance` a `:95` basta con una sola credenziale qualsiasi.) | M |
| R8.7 | APERTA | `pages/ConnectionManager.jsx:334,144,152,155`: in modifica di una connessione DB, un clic sul tipo "ArcGIS" porta `isArcgis` a true mentre `editingCategory` resta 'database', quindi viene chiamato `createFeatureConnection` e nasce una connessione nuova (la vecchia resta). Vale anche al contrario. | M |
| R8.8 | APERTA | `api-conn/ApiConnectionWizard.jsx:459-460` con `wizard-steps/Step2Params.jsx:138`: `static_parameters` è indicizzato per `p.name`. Rinominando o rimuovendo un parametro la vecchia chiave resta e viene inviata (`:326`) e usata nei test (`:403,417,443`). | M |
| R8.9 | APERTA | `ApiConnectionWizard.jsx:423`: il test batch usa `method_override \|\| 'POST'`, mentre la UI dice "Usa metodo principale (GET)" (`Step4Batch.jsx:59`) e l'esecuzione usa il metodo principale (`backend/app/core/arricchisci/enrichment_batch_mode.py:231`): test ed esecuzione divergono. `ApiConnectionWizard.jsx:105-109`: il `default` di un parametro del template finisce in `static_parameters` anche se il parametro è `mapped`, quindi diventa una costante. | M |
| R8.10 | APERTA | `eseguibili/ExecutableDynamicRunForm.jsx:186-195`: `min`, `max` e `pattern` sono solo attributi HTML (nessun `<form>`, nessun `checkValidity`); `canRun` (`:107-121`) guarda solo gli obbligatori. `:145`: i valori vanno al backend come stringhe ("true", "42"). `:58-79`: l'analisi del file è un passo manuale per ogni file. | M |
| R8.11 | APERTA | Quattro cataloghi diversi: `ExecutableDynamicRunForm.jsx:11` (xlsx/csv/json/geojson/gpkg/shp), `EseguibileExecuteWizard.jsx:25-32` (xlsx/csv/json/geojson/zip/pdf), `PublishEseguibileModal.jsx:106-113` (xlsx/csv/json/geojson/zip/pdf), `nodes/result/standard/OutputSettingsPanel.jsx:24-31` (csv/json/xlsx/geojson/gpkg/shp). Si può pubblicare un default (`pdf`, `zip`) che il form dinamico non offre, o viceversa. | M |
| R8.12 | CHIUSA | `modals/ScheduleWizard.jsx:113` legge `enabled` dallo schedule esistente; `:166` lo invia. Test `tests/components/ScheduleWizard.contract.test.jsx`. | A |
| R8.13 | PARZIALE | Chiuso: se il caricamento in modifica fallisce la finestra si chiude (`ScheduleWizard.jsx:115-120`). Manca: il cron è solo "non vuoto" (`:147`) e i formati non validi arrivano al backend; ci sono solo 3 fusi orari (`:300-304`), e uno schedule con un altro fuso viene mostrato come "Europe/Rome"; 6 step obbligatori (`:154`, `goToStep` solo ±1); `retry_backoff_seconds` scarta in silenzio i valori non numerici e lo 0 (`:178`). | M |
| R8.14 | APERTA | `transforms/ParameterInput.jsx:95,105,123,134`: `value ?? defaultValue` mostra il default senza scriverlo nella config (nessun writer in `DynamicConfig.jsx`). `:139`: una select `required` non ha l'opzione vuota, quindi con valore assente mostra la prima opzione che non è salvata. `:104-114`: `min`/`max` passati come attributi ma non applicati (`parseFloat` diretto). | M |
| R8.15 | PARZIALE | Ora il backend segnala la rinomina duplicata come avviso (`rename.duplicate`, dfd796dd). Restano nel frontend `transforms/parameter-inputs/RenameListInput.jsx:39-74`: il conteggio delle collisioni considera solo le colonne con suffisso, quindi con `id` e `id_L` la proposta `id_L→id` duplica la colonna esistente `id`; `addRename` (`:24-26`) e `loadAllColumns` (`:34`) creano righe con `new_name: ''`. | M |
| R8.16 | APERTA | `transforms/parameter-inputs/ColumnOrderInput.jsx:17-21`: una volta salvato l'ordine, le colonne nuove a monte non compaiono e quelle rimosse restano in elenco (tipo di ripiego 'str', `:74-77`); `key={colName}` (`:88`) va in collisione con nomi duplicati. | M |
| R8.17 | PARZIALE | JoinModal non esiste più; il join di Estrai sta in `canvas/JoinEdgePanel.jsx`. Chiuso: una condizione incompleta blocca la scrittura con un messaggio (`utils/estraiJoins.js:231-238`, `JoinEdgePanel.jsx:227-231`); nessuna preselezione automatica (`JoinEdgePanel.jsx:15`). Manca: i filtri ON senza colonna vengono scartati in silenzio (`estraiJoins.js:157`) e quelli con operatore che richiede un valore vuoto passano senza avviso; passando a "Spaziale" i filtri ON vengono azzerati in scrittura (`estraiJoins.js:144`) senza avviso; un arco duplicato viene ancora scartato in silenzio (`hooks/useEdgeHandlers.js:85`). | M |
| R8.18 | APERTA | `modals/FilterModal.jsx:86-99`: i tipi non sono normalizzati. Con `integer`, `float`, `varchar`, `timestamp` o `datetime` ci sono solo gli operatori "all" (niente `>`, `<`, `LIKE`, `BETWEEN`). `:133-140`: per `date` l'input è `type="date"`, quindi BETWEEN ("v1 AND v2") e IN ("a, b") non si possono scrivere. | M |
| R8.19 | APERTA | `input_${ordinal}` con ordinal = numero di nodi presenti + 1: `EstraiEditor.jsx:238-244`, `ManipolaEditor.jsx:225-231`, `ArricchisciEditor.jsx:261-264` (anche `order` duplicato). Aggiungendo due input e cancellando il primo, il successivo nasce ancora come `input_2`. Lo stesso per `count_${aggregations.length+1}` in `nodes/result/standard/GroupOrderHavingPanel.jsx:112`. Solo `RuntimeInputBindingControl.jsx:19,95` usa un generatore unico. | M |
| R8.20 | APERTA | `pages/UserManager.jsx:149-154`: il select "Gruppo" è modificabile anche in modifica, ma `:75` toglie `group_id` dal payload, quindi la modifica viene persa in silenzio. `:123-128`: un admin che modifica un admin vede "Esecutore" (manca l'option `admin`) e invia `admin`. `:24-29`: `RoleBadge` non ha `esecutore`. | M |
| R8.21 | APERTA | `UserManager.jsx:206`: il placeholder dice "Minimo 6 caratteri…" mentre il controllo a `:180` chiede 12 (backend `app/core/auth/users_router.py:243`). | B |
| R8.22 | APERTA | `pages/LoginPage.jsx:96-97,136-139`: `needsMfa` e `mfaCode` non si azzerano quando cambia lo username (`:203`). Dopo la richiesta MFA per l'utente A, l'accesso con l'utente B invia il codice di A e chiede ancora l'MFA. | M |
| R8.23 | APERTA | `pages/FileManager.jsx:201,275`: `parseInt(userSrid)` su testo libero (`FileManager/UploadModal.jsx:73`), quindi "EPSG:4326" diventa NaN e viene inviato. `nodes/result/standard/OutputSettingsPanel.jsx:69`: un limite negativo viene accettato. `:61`: il nome file non è sanificato ("a/b", "x.csv" dà "x.csv.csv"). `transforms/parameter-inputs/StringListInput.jsx:11,41`: il testo non confermato con Invio si perde (nessun commit su blur). `parameter-inputs/ConditionListInput.jsx:24,64`: `between` a testo libero "min,max" non validato. | B |

## Nuovi difetti (stessa classe, non presenti nell'audit)

| id | classe | file:riga | scenario | grav. |
|---|---|---|---|---|
| N1 | R8 (regola password) | `components/ChangePasswordModal.jsx:33` | Il cambio password lato utente accetta ≥6 caratteri, il backend ne chiede 12 (`backend/app/core/auth/auth.py:289`). L'utente vede accettata una password da 8 caratteri e poi riceve un 400. | B |
| N2 | R8 (regola password) | `pages/UserManager.jsx:59-60,104` | La creazione utente non controlla la lunghezza della password (solo "non vuota"); il placeholder dice "Password sicura...". L'errore arriva solo dal backend (`users_router.py:138`). | B |
| N3 | R5.4 (cache per account) | `components/nodes/ProjectInputNode.jsx:26-41` | Seconda cache del registro delle trasformazioni, separata da `utils/transformRegistry.js`, che non viene azzerata al logout (quella condivisa sì, `transformRegistry.js:53`). Dopo un cambio di account o un deploy che cambia il registro, i parametri collegabili restano quelli vecchi. | B |
| N4 | R8.1 (input che riscrive) | `components/nodes/ProjectInputNode.jsx:224-231` | Ogni tasto in "Colonne attese" ricrea l'elenco con `type: 'str', required: true`: i tipi e i "facoltativo" già presenti (canvas salvati, colonne impostate altrove) si perdono appena si tocca il campo. | M |
| N5 | R5.8 (errore muto) | `components/api-conn/ApiConnectionWizard.jsx:363-364` | Il salvataggio del connettore fallito mostra solo "Errore salvataggio" e scarta il messaggio del backend (nome duplicato, auth non valida, 422). | M |
| N6 | R6.6 (chiusura durante busy) | `components/TicketCreateModal.jsx:26,74`; `pages/UserManager.jsx:84-97`; `pages/ConnectionManager.jsx:319` (Annulla disabilitato, × no) | La × (ed Esc per Ticket e UserModal) chiude durante l'invio. La risposta arriva a una modale smontata o a quella riaperta: per Ticket `onCreated` naviga lo stesso al ticket. | B |
| N7 | R6.5 (overlay) | `components/common/Wizard.jsx:79` | Variante più grave di R6.5: l'overlay del Wizard chiude a ogni click, senza nemmeno il controllo `target === currentTarget`. Un drag di selezione che parte da un campo e finisce fuori dal dialog chiude il wizard a 6 step senza conferma (vale per ApiConnectionWizard, DataSourceConfigModal e UploadModal; ScheduleWizard solo quando non sta salvando). | M |
| N8 | R7.2 (ripristino) | `utils/serverActions.js:61-66`; `components/modals/CanvasVersionsModal.jsx:73-89` | (a) Il ripristino con modifiche non salvate le scarta senza dirlo esplicitamente: `markClean` e poi la copia di questa scheda viene ritirata (`serverCanvasReplaced`); il bottone dice solo "Confermi? (sovrascrive)". (b) Tra `markClean` e il reload passano 600 ms senza blocco: una modifica fatta in quella finestra si perde senza prompt. (c) `handleRestore` non ha un flag di busy: un doppio clic su "Confermi?" manda due ripristini (il secondo finisce in conflitto). | B |
| N9 | R5.2 (401 in login) | `api/http.js:127-131` | Un 401 su `/auth/login` (credenziali errate) passa dall'interceptor: `logout()`, `emitBeforeLogout` e `navigate('/login', {replace})` partono a ogni tentativo fallito. Oggi è innocuo ma accoppia login e logout; con R5.2 corretto andrebbe escluso come `/auth/logout`. | B |
| N10 | R6.7 / R6.1 (regola unica per i tasti) | `hooks/useCanvasKeyboard.js:57-110` vs `components/editor/EditorShell.jsx:221,264` vs `components/common/CommandPalette.jsx:39-47` | Tre regole diverse per "c'è una finestra aperta". Ctrl+K apre la palette comandi anche sopra una modale (e naviga senza guard, R7.1); Ctrl+Z agisce sotto le modali; Ctrl+S invece è bloccato. | M |
| N11 | R8 (sola lettura) | `components/nodes/ProjectInputNode.jsx:137,141-146,168-171,213-216,220-253,259-305` | In sola lettura Etichetta, Chiave, Obbligatorio, Colonne attese, Estensioni, Dimensione, Valore predefinito, Opzioni e validazioni restano abilitati: l'utente digita e non succede nulla, perché `updateNodeData` ignora in silenzio (`store/useCanvasStore.js:765-770`). Solo i select hanno `disabled={isReadOnly}`. | B |
| N12 | R8.4 (numeri) | `components/nodes/DataSourceNode.jsx:348`, `components/panels/ApiEnrichNode.jsx:611`, `components/transforms/parameter-inputs/MaskingListInput.jsx:79,90` | Altre occorrenze di `parseInt(v)\|\|0` non elencate nell'audit: il campo svuotato mostra subito "0". | B |
| N13 | R7.5 / R8.13 (fusi) | `components/modals/ScheduleWizard.jsx:300-304` | Uno schedule con un fuso non in elenco (impostato via API) viene mostrato come primo valore ma salvato con il valore originale: quello che si vede non coincide con quello che si salva. | B |
| N14 | R6.10 (dialog) | `components/canvas/NodePalette.jsx:89` e `components/common/CommandPalette.jsx:85` | Dialog `aria-modal` senza `useDialogFocus`: niente Tab trap né ripristino del focus; Esc funziona solo con il focus nel campo di ricerca (`CommandPalette.jsx:75,100`; `NodePalette.jsx:83`). | B |


---

# Fase 5 — Raccolta R9, R10, R11, P, D, U, Rimandati, residui Blocco A

Codice letto: `main` = e4688ae6 (05/11/2026). Sola lettura, nessuna correzione.
Base dell'audit: 86cca5e0. Percorsi relativi a `frontend/src/` salvo indicazione.
Le pagine di sistema (UserManager, Ticket*, LogViewer, Admin*, ConnectionManager, DataSourceManager,
FileManager, EseguibiliList, EsecutoreHome, ApiConnectionManager, Eseguibile*) **non sono state toccate**
da Fase 1-4 e Blocco A (`git log 86cca5e0.. -- <file>` vuoto, salvo le voci della Fase 1): le loro voci
sono state comunque verificate riga per riga.

Stati: **C** = CHIUSA, **A** = APERTA, **P** = PARZIALE, **NPA** = NON PIU' APPLICABILE.

## Conteggi

| Sezione | C | A | P | NPA | Tot |
|---|---|---|---|---|---|
| R9 | 4 | 9 | 2 | 1 | 16 |
| R10 | 1 | 8 | 0 | 0 | 9 |
| R11 | 0 | 4 | 0 | 0 | 4 |
| P1-P31 | 3 | 26 | 2 | 0 | 31 |
| P - Accessibilita' | 0 | 3 | 1 | 0 | 4 |
| P - Performance | 1 | 5 | 1 | 0 | 7 |
| D | 6 | 12 | 0 | 0 | 18 |
| U - conferme dal vivo | 1 | 7 | 0 | 0 | 8 |
| U1-U8 Manipola | 7 | 0 | 0 | 1 | 8 |
| U - Estrai | 1 | 2 | 0 | 1 | 4 |
| U - Dashboard | 0 | 6 | 0 | 0 | 6 |
| U - Pagine di sistema | 0 | 8 | 0 | 0 | 8 |
| U - Linee guida | 1 | 3 | 1 | 0 | 5 |
| Rimandati piano frontend (1-12) | 12 | 0 | 0 | 0 | 12 |
| Residui Blocco A | 0 | 3 | 0 | 0 | 3 |
| **Totale** | **37** | **96** | **7** | **3** | **143** |

Nuovi difetti: 8 (2 M + 1 M-esteso + 5 B, vedi sezione). Codice morto nuovo: 9 elementi (+ 14 preesistenti non in audit).

---

## R9 — Esecuzioni

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| R9.1 inputDefinitions Manipola/Arricchisci, `inputs` scartati | C | `components/editor/PipelineRunner.jsx:158-164` mette `inputs` nelle opzioni; `api/enrichment.js:43-48` li invia; Manipola/Estrai `onExecute: (id, opts) => execution.handleExecute(opts)` (ManipolaEditor.jsx:175, EstraiEditor.jsx:179); test `tests/components/BeforeRun.contract.test.jsx` | A |
| R9.2 `permission_revoked` → "Completata" + Scarica | A | `components/eseguibili/EseguibileExecuteWizard.jsx:159-160` mette `taskResult={...permissionRevoked:true}`; il ramo `:473 taskResult ? (...)` mostra comunque "✅ Esecuzione completata!" e "⬇ Scarica Risultato" (`:476-489`) sotto il banner. Scenario: utente declassato durante il task → banner rosso + successo verde + download di un file inesistente | A |
| R9.3 /run: cleanup solo dopo download | A | `EseguibileExecuteWizard.jsx:187-191` chiama `onAfterDownload` solo da `handleDownload`; "Chiudi" (`:487`) e l'esito fallito non puliscono. `pages/EsecutoreHome.jsx:171` promette "I file caricati ed elaborati non vengono conservati". Scenario: l'esecutore chiude senza scaricare → input e output restano sul server | A |
| R9.4 Manipola senza progresso/annulla; annulla legato al tipo | A | `components/modals/ExecutionModal.jsx:60` `isAsync = projectType === 'arricchisci'`; Annulla solo se `isAsync` (`:109`); ManipolaEditor.jsx:513 ed EstraiEditor.jsx:584 non passano `progress`, `taskStatus`, `onCancel`. In piu': per Manipola/Estrai durante l'esecuzione la finestra non si chiude in nessun modo (`:116` Chiudi disabilitato, `:133` onClose undefined, `:139-140` niente Esc ne' ×) e la barra e' finta (`useSmoothProgress`). Scenario: run Manipola di 10 minuti → editor bloccato senza progresso reale ne' annullamento | M |
| R9.5 Arricchisci 3 passaggi / 3 salvataggi | C | Percorso unico `PipelineRunner.run` (`:181-216`): un salvataggio (`saveForRun`), finestra «Prima di avviare» solo se serve; `ArricchisciEditor.jsx:182-185` aggiunge solo il formato | M |
| R9.6 fallimento salvataggio pre-esecuzione senza toast | C | `PipelineRunner.jsx:61-66` → `saveFailedResult(e)` mostrato in ValidationModal; `ResultNode.jsx:107,115-116` gestisce il catch | M |
| R9.7 Valida/Preview/Esegui senza busy | P | Esegui protetto (`EditorToolbar.jsx:113,160`, `ExecuteActionsPanel.jsx:44,122`, `PipelineRunner.jsx:183`). **Valida e Anteprima no**: `ExecuteActionsPanel.jsx:100-117` senza `disabled`, `PipelineRunner.validate/preview` (`:146-149,236-242`) senza guardia. Scenario: doppio clic su Valida con modifiche non salvate → due "Salva e continua" / due POST /validate concorrenti | M |
| R9.8 modale chiuso non riapribile; nuovo Esegui durante polling; `onClose(evento)` | P | Nuovo Esegui bloccato (`hooks/useExecution.js:219`, runState). Restano: nessun punto riapre la finestra di esecuzione (nessun `setShowModal(true)` fuori da useExecution) dopo "Continua in secondo piano" (Arricchisci); `EstraiEditor.jsx:585` passa ancora `onClose={execution.closeModal}` e `ModalWrapper.jsx:106` chiama `onClose(evento)` → `closeModal(resetResult=evento)` azzera l'esito | M |
| R9.9 EnrichmentConfirmModal senza errors | NPA | Componente rimosso (nessuna occorrenza in src); sostituito da `components/editor/BeforeRunModal.jsx:76,108` che mostra `node.errors` e `utils/beforeRun.js:47,59` (blocking) | M |
| R9.10 busy a id singolo | A | `pages/ConnectionManager.jsx:52,191,197`, `pages/DataSourceManager.jsx:63,127,130`, `pages/AdminTaskRuntimePage.jsx:25,50,57`, `components/eseguibili/ExecutableDynamicRunForm.jsx:31,64,79`. Scenario: test su conn. A e B in parallelo → la fine di A rimette `testingId=null` e riabilita B mentre e' ancora in corso | M |
| R9.11 clonazione e rinomina eseguibile senza busy per id | A | `pages/ProjectList.jsx:163-171` nessun busy (doppio clic = 2 cloni); `pages/EseguibiliList.jsx:109-142,237`: `startRename` non controlla `savingRename`; durante il salvataggio di A si apre la rinomina di B, poi la fine di A esegue `setRenamingId(null)` e chiude B | M |
| R9.12 toggle schedule `s.enabled` | C | `pages/ScheduleList.jsx:224-233` (`withEnabled` su `schedule.enabled`); test `tests/pages/ScheduleList.batch3.contract.test.jsx` | A |
| R9.13 rollback ottimistico | A | `ProjectList.jsx:150-158` (rinomina) e `:139-147` (tag): rollback a `project.name`/`prev` catturati al clic. Scenario: rinomina A→B lenta che fallisce dopo B→C riuscita → torna "A" mentre il server ha "C" | M |
| R9.14 download senza catch (Schedule, ExecutionModal 410) | A | `ScheduleList.jsx:439-440` `downloadScheduleOutput(...)` senza catch; `useExecution.js:301-328` `downloadTaskResult` senza try; `ExecutionModal.jsx:296-299` `onClick={onDownload}`. Scenario: risultato scaduto (410) → promise rifiutata, nessun messaggio | M |
| R9.15 Ripristina/Ripubblica versione eseguibile senza conferma | A | `components/eseguibili/EseguibileVersionsModal.jsx:32-36,74,99` azione diretta | M |
| R9.16 Quick test API cliccabile piu' volte | A | `pages/ApiConnectionManager.jsx:222-228,403`: nessun busy ne' feedback durante la chiamata | M |

## R10 — Permessi

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| R10.4 Bonifica orfani: conteggio filtrato, richiesta globale; orfano per nome | A | `pages/AdminObjectStoragePage.jsx:50-51` orfano = `project_name === 'Progetto eliminato'`, catalogo filtrato da `group_id` (`:68`); `cleanupOrphanStorage()` (`api/adminObjectStorage.js:18-22`) senza gruppo. Scenario: filtro gruppo G (2 orfani) → la conferma dice 2, il server bonifica tutti i gruppi | A |
| R10.6 condivisioni salvabili se getShares fallisce | C | `components/shared/AllowedGroupsSelector.jsx:63,113,122,132` (`locked` finche' `!sharesLoaded`, 404 da `response.status`); test `tests/components/AllowedGroupsSelector.contract.test.jsx` | A |
| R10.7 gruppo proprietario dall'utente | A | `AllowedGroupsSelector.jsx:65` `ownerGroupId = user?.group_id`. Scenario: superadmin (gruppo S) condivide una risorsa del gruppo G → G compare tra i gruppi selezionabili e S viene escluso | M |
| R10.1 azioni schedule ai viewer, delete progetto, `can.*` non usati, `role ===` grezzi | A | `ScheduleList.jsx:529-539` Esegui/Modifica/Elimina senza `can.*`; `components/projects/ProjectCard.jsx:156-159` cestino sempre visibile; `utils/permissions.js:72,74` `deleteOwnProject`/`deleteFile` senza chiamanti; `pages/TicketDetail.jsx:64`, `pages/TicketsList.jsx:83` `user?.role === 'admin'` | M |
| R10.2 EseguibileCard delete senza `can_edit` | A | `components/projects/EseguibileCard.jsx:33-37` gate solo `isAdmin` | M |
| R10.3 eliminazione gruppo: nome vs id, report scartato, confermabile senza impatto | A | `pages/UserManager.jsx:370` "Scrivi il nome" ma `:378` confronta `groupId`; `api/users.js:25-27` scarta il body (`res.report` sempre undefined in `UserManager.jsx:446`); `:372` input attivo anche con riepilogo in caricamento o fallito (`:313-316`) | M |
| R10.5 "Autorizza retry" con `window.prompt` | A | `pages/AdminTaskRuntimePage.jsx:40-44` | M |
| R10.8 header REST "Secret" in chiaro; devtools in produzione | A | `components/nodes/carica/RestDestinationSection.jsx:41,202,211` il segreto resta in `node.data.rest_headers` (salvato e versionato); `store/useCanvasStore.js:41,1018` `devtools(...)` senza `enabled: import.meta.env.DEV` | M |
| R10.9 rimozione allegati ticket visibile a tutti | A | `pages/TicketDetail.jsx:355-360` nessun gate | B |

## R11 — Formatter

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| R11.1 ora UTC mostrata come locale; storico schedule | A | `utils/logViewerHelpers.jsx:36-41` taglia l'ISO senza fuso; `components/logviewer/RunDetail.jsx:101` `(l.ts).slice(11,19)`; `ScheduleList.jsx:44-48` `new Date('YYYY-MM-DDTHH:mm:00')` senza Z = ora locale | A |
| R11.2 formatter duplicati, GB mancanti | A | 9 formattatori di dimensione: `ScheduleList.jsx:38`, `pages/FileManager/helpers.js:28`, `TicketDetail.jsx:43`, `DataSourceManager.jsx:41`, `AdminObjectStoragePage.jsx:27`, `AdminSystemStatusPage.jsx:31`, `AdminBackupsPage.jsx:78`, piu' inline in `EseguibileExecuteWizard.jsx:315`, `ExecutableDynamicRunForm.jsx:334`, `ApiEnrichNode.jsx:743`; FileManager/Schedule/Ticket senza GB | B |
| R11.3 enum grezzi | A | `pages/TicketsList.jsx:150-158,163-171` chip `{s}`; `TicketDetail.jsx:255` `{ticket.status}` | B |
| R11.4 AdminBackups date non formattate | A | `pages/AdminBackupsPage.jsx:121,142,143` | B |

## P — Difetti puntuali

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| P1 XSS tooltip Leaflet | C | `components/modals/PreviewModal.jsx:321-334` tabella DOM con `textContent`; guardia `tests/shared/htmlSinks.contract.test.js` (vedi nuovo difetto N5: la guardia ha un buco) | A |
| P2 "Testa connessione" Data Source finto | C | pulsante rimosso: `components/arricchisci/DataSourceConfigModal.jsx:176-178`; nessun altro timer simulato in src | A |
| P3 TransformNode: tab di categoria cambia operazione | C | tab sostituite da `<select>` con optgroup (`components/transforms/TransformNode.jsx:147-155`). Vedi N2 per il cambio operazione | A |
| P4 FileManager multilayer | A | `pages/FileManager.jsx:279-282`: `setAvailableLayers(info.layers)` seguito da `resetUploadModal()` che fa `setAvailableLayers([])` (`:352`) nello stesso batch → selettore dei layer vuoto; `resetLayerModal` (`:354`) non ricarica la lista → il file caricato non compare se si annulla; errori colonne solo `console.error` (`:152`) | A |
| P5 PreviewModal `data` ricreato a ogni render | P | Manipola passa `preview.data` stabile (ManipolaEditor.jsx:515). Restano Estrai (`pages/EstraiEditor.jsx:594-615`) e Arricchisci (`pages/ArricchisciEditor.jsx:439+`) con IIFE → nuovo oggetto a ogni render dell'editor → `PreviewModal.jsx:548-555` azzera pagina, ordinamento, ricerca e tab. In Estrai l'IIFE chiama anche `columnsOf(columnSnapshot(), ...)` a ogni render | A |
| P6 Leaflet senza `map.remove()`, timer, `resetStyle`, `getColor` O(n²) | A | `PreviewModal.jsx:239-272` nessun cleanup (ne' `map.remove` ne' `clearTimeout`); `:338` `resetStyle` torna allo stile di costruzione (colore del primo `colorBy`) dopo un cambio di colorazione; `:211-229` `rows.map` + `Math.min(...)`/`new Set` per ogni feature | M |
| P7 parser WKT/WKB | A | WKT `MULTIPOINT (1 2, 3 4)` (senza parentesi interne) → `:126-130` un solo punto; `POINT M (...)`, `POINT ZM` non riconosciuti (regex `Z?`); WKB ISO Z/M/ZM (1001.., 2001.., 3001..) → `:48-50` tipo normalizzato ma `hasZ` solo dal flag EWKB (`:45`), M non gestito → coordinate lette sfasate | M |
| P8 gruppo di colonne collassato senza intestazione | A | `components/modals/PreviewTable.jsx:121-132`: gli span si calcolano sulle colonne visibili, il gruppo collassato sparisce; il ramo "▶ n col." (`:208-209`) e' irraggiungibile | M |
| P9 clipboard senza guardia | A | `components/panels/QueryPreview.jsx:313-316` `navigator.clipboard.writeText` senza controllo ne' await; "Copiato" sempre | M |
| P10 import OpenAPI | A | `components/arricchisci/OpenAPIImportModal.jsx:110,130` `JSON.parse` con accept `.yaml/.yml` (`:206`); `$ref` nel body non risolto (`:66-68`); niente Swagger 2.0 (`spec.servers`, `components.securitySchemes`); autodetect auth assente da file (`:128-134`); `in` dell'apiKey ignorato (`:96`); checkbox endpoint `onChange={()=>{}}` (`:304`); metodi solo get/post/put/delete (`:63`) | M |
| P11 preview response-config | A | `components/panels/response-config/responseConfigEngine.js:150-153` senza `iterate_on` chiama `extractRow(item, mappings, {})` ma `extractRow` ignora il primo argomento (`:177-189`) → tutte le colonne null; `ResponseConfigBuilder.jsx:94-96` `onChange` al mount riscrive la config | A |
| P12 ConnectionSelectorModal crash senza `auth` | A | `components/arricchisci/ConnectionSelectorModal.jsx:233-235` `undefined !== 'none'` vero → `connection.auth.type` TypeError | M |
| P13 wizard API: `testRawResponse` non azzerato | A | `components/api-conn/ApiConnectionWizard.jsx:252-300` l'apertura (new/edit/template) azzera `testResult` ma non `testRawResponse` (azzerato solo in `:485`, import cURL) | M |
| P14 creazione DataSource da /external-connectors | A | `pages/ApiConnectionManager.jsx:364-366` `catch(e){console.error(e);}`; nessun toast ne' in errore ne' in successo | M |
| P15 quota esaurita al 90% | A | `components/api-conn/ApiConnectionCard.jsx:48` | B |
| P16 FileManager upload multiplo / stato vuoto | A | `FileManager.jsx:441` "Supporta upload multiplo" ma `:133,511` solo `files[0]`; `:458` stato vuoto ignora il tab | M |
| P17 eseguibili spariscono senza progetti | A | `ProjectList.jsx:390-395` `projects.length === 0` → solo QuickStart; `EseguibiliSection` (`:506`) dentro il ramo else | M |
| P18 stato vuoto con filtro; pulsante 📢; toast doppio | A | `ProjectList.jsx:409-411` "Crea il tuo primo progetto" anche con filtro; `components/projects/EseguibiliSection.jsx:41` cita il pulsante 📢 che le card non hanno; toast in `PublishEseguibileModal.jsx:44` e in `ProjectList.jsx:533` | B |
| P19 System Status OK con componenti mancanti | A | `pages/AdminSystemStatusPage.jsx:359-365` `.filter(Boolean)` poi `every` → mancanti ignorati, lista vuota = 'ok' | M |
| P20 tema scuro rotto, colori hardcoded | A | `styles/AdminObjectStoragePage.css:1,8,10..`, `styles/AdminTaskRuntimePage.css:1,7,13` (`--bg-primary`, `--surface`, `--text-primary` non definite; nessuna definizione in styles/); `PermissionRevokedBanner.jsx:25,28`; fallback Suspense `App.jsx:112`; anche `pages/EseguibiliList.css:6` (stessa radice della voce U Eseguibili) | M |
| P21 Export CSV pagina corrente; tile "questa pagina" | A | `pages/LogViewer.jsx:131-139` esporta `logs` (pagina); `components/logviewer/RunsTab.jsx:77-81` "Run Totali {total} — questa pagina" accanto a conteggi della pagina | M |
| P22 Ticket: limite senza avviso, stati vuoti = tutti, popup bloccabile | A | `TicketsList.jsx:89-93` nessuna paginazione/avviso; `status: statusFilter.length ? ... : undefined` → deselezionando tutto compaiono anche i chiusi; `api/tickets.js:139` `window.open` dopo un `await` (bloccato) | M |
| P23 Object Storage senza debounce; download | A | `AdminObjectStoragePage.jsx:60-68,82,227` un fetch per tasto su `groupId`; `api/adminObjectStorage.js:25-41` blob intero + `revokeObjectURL` subito dopo `click()` | M |
| P24 DataSourceNode componenti nel render | A | `components/nodes/DataSourceNode.jsx:186` `BreadCrumb`, `:205` `DsSelector` definiti dentro il componente | M |
| P25 errori di eliminazione senza dipendenze nascoste | A | `api/errors.js:62` accetta `hiddenCount`, ma nessun chiamante lo passa: `ProjectList.jsx:115`, `ApiConnectionManager.jsx:209`, `ConnectionManager.jsx:178`, `DataSourceManager.jsx:149` (estensione della classe a 4 punti) | B |
| P26 ResizeObserver dell'hex canvas mai collegato | A | `utils/hexCanvas.js:80-90` effetto una sola volta; con il `<canvas>` non montato al primo effetto (ProjectList `:280` skeleton durante loading; FileManager `if (loading) return`) l'observer non si collega mai | B |
| P27 anteprima inline senza "obsoleta"; AnnotationNode autoFocus | A | `components/nodes/result/ResultInlinePreview.jsx` senza indicatore (solo `StepPreview.jsx:101,119` lo ha); `AnnotationNode.jsx:40` `editingTitle = !title` → `:83 autoFocus` al caricamento di ogni nota senza titolo | B |
| P28 ricerca incoerente, badge sidebar, CommandPalette su /login e /run | A | `App.jsx:159` CommandPalette montata fuori dalle route, `CommandPalette.jsx:38-47` Ctrl+K sempre attivo; AppSidebar invariata | B |
| P29 Estrai: placeholder, self-join, Undo/Redo | A | `EstraiEditor.jsx:415` "Cerca tabelle e viste…" filtra solo tabelle (`:348`; viste a parte `:498`); `:351` tabelle gia' nel canvas escluse → niente self-join; Undo/Redo solo da tastiera (`hooks/useCanvasKeyboard.js:69`), nessun pulsante in `EditorToolbar.jsx` | B |
| P30 toolbar diverse; posizioni fisse in Arricchisci | P | Barra unica condivisa (M7, `components/editor/EditorToolbar.jsx`). Restano: menu diversi (Manipola senza "Carica ArcGIS" `ManipolaEditor.jsx:479-491`, Arricchisci senza "Nota" `ArricchisciEditor.jsx:398-414`); posizioni fisse `ArricchisciEditor.jsx:256,263,270,329` (Valori statici, Input runtime, Output) fuori vista | B |
| P31 index.html: Google Fonts, CSP, Sentry | A | `frontend/index.html:26-32` font esterni, nessuna CSP; `index.jsx:27` import dinamico di Sentry | B |

### P — Accessibilita'

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| a11y-1 `<div onClick>` al posto di link/button | P | Cambio tema negli editor ora voce di menu (`EditorToolbar.jsx:120-127`): chiuso. Restano `components/AppSidebar.jsx:164-167` (e voci 186-266), `EsecutoreHome.jsx:44`, `TicketsList.jsx:204-208` (`<tr onClick>`), cartelle `DataSourceManager.jsx:460-462`, `ProjectCard.jsx:70` | M |
| a11y-2 label non associate, form senza `<form>` in UserManager | A | `UserManager.jsx:104,109,115,122,150,158,205,247,252,256` `<label>` senza `htmlFor`; nessun `<form>` nel file → Invio non invia | B |
| a11y-3 campo MFA senza focus | A | `pages/LoginPage.jsx:246-257` nessun `autoFocus`/focus al passaggio MFA | B |
| a11y-4 toggle password `tabIndex=-1` | A | `LoginPage.jsx:235` | B |

### P — Performance

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| perf-1 selettori su tutto `nodes`/`edges` | P | CaricanNode/CaricanArcGIS chiusi (`CaricanNode.jsx:49-51`, `CaricanArcGISNode.jsx:68` selettore ristretto). Restano `RuntimeInputBindingControl.jsx:38`, `ProjectInputNode.jsx:56-57`, `FilterModal.jsx:32`; nuovi: vedi N6 | B |
| perf-2 QueryPreview regex per riga | A | `QueryPreview.jsx:401-425` `new RegExp` per keyword a ogni render | B |
| perf-3 FilterModal/ColumnBuilderModal montati da chiusi | A | `components/nodes/TableNode.jsx:370-389` sempre montati (FilterModal sottoscrive tutti i nodi) | B |
| perf-4 modali lazy sempre montati | A | `ManipolaEditor.jsx:513-514`, `ArricchisciEditor.jsx:437-438`, `EstraiEditor.jsx:584-591`: `<PreviewModal isOpen=...>` sempre nel Suspense → chunk e Leaflet scaricati all'apertura dell'editor | B |
| perf-5 `useSmoothProgress` 60 fps | A | `hooks/useSmoothProgress.js:31-41` setState a ogni rAF | B |
| perf-6 `applyFilters` doppio / `files.find` | A | `ProjectList.jsx` 6 chiamate a `applyFilters(...)` (empty + map per sezione) | B |
| perf-7 `useValidationHighlights` ricrea tutti i nodi | C | `hooks/useValidationHighlights.js:32,43,51` `mapNodesIfChanged`: stesso array se nulla cambia | B |

## D — Codice morto dell'audit

| Voce | Stato | Prova | Grav. |
|---|---|---|---|
| D1 percorso "CARICA manuale" (`onCaricanExecute`, `useCaricarica`, `CaricaricaModal`, `caricaExecution`, `executeCaricanTask`) | C | 0 occorrenze in src; commit 7a67064c; `components/editor/editorActions.js` + `tests/components/EditorActions.contract.test.js` | — |
| D2a EstraiEditor `handleFeatureSchemaLoaded`, `handleUpdateFeatureWhere` | C | 0 occorrenze | — |
| D2b FeatureServicePanel `loadLayerSchema`, `onLayerReady` | A | `components/panels/FeatureServicePanel.jsx:48` prop mai passata (EstraiEditor.jsx:545-548); `:141-153` `loadLayerSchema` + stato `layerSchemas` senza chiamanti | B |
| D2c CaricanArcGISNode `data.executeResult` | A | `components/nodes/CaricanArcGISNode.jsx:60,681-684` nessuno scrive `executeResult` | B |
| D3a ManipolaEditor `onUpdateProjectSource*`, `data.onChange` | C | 0 occorrenze | — |
| D3b ArricchisciEditor `exportFormat` | C | 0 occorrenze | — |
| D3c ramo `api_enrich` di `onDrop` | A | `pages/ArricchisciEditor.jsx:369-370`: le sidebar trascinano solo `project_source`/`file_source` (`ProjectsSidebar.jsx:42`, `FilesSidebar.jsx:55`) | B |
| D3d fingerprint su tipi non registrati | C | nessuna occorrenza in ArricchisciEditor | — |
| D4a `useSchemaInference.fetchNodeSchema` | C | hook rimosso | — |
| D4b `Step2Params.wizardStepError` | A | `components/api-conn/wizard-steps/Step2Params.jsx:16,23-25`; `ApiConnectionWizard.jsx:549` non lo passa; CSS `styles/ApiConnectionManager.css:394` | B |
| D4c step "Conferma" di UploadModal | A | `pages/FileManager/UploadModal.jsx:144` `content: null`, `:181` `onStepChange={() => {}}`; `uploadStep` arriva al massimo a 2 | B |
| D5a UserManager `ChangePasswordModal`/`showPwdModal` | A | `pages/UserManager.jsx:16,407,597` mai `setShowPwdModal(true)` (il cambio password vive in AppSidebar) | B |
| D5b `api/config.js DEFAULT_HEADERS` | A | `api/config.js:27,35`, `api/index.js:22`, nessun consumatore | B |
| D5c `can.assignAdminRole`, `manageGroups`, `createEsecutore` | A | `utils/permissions.js:82-84` nessun chiamante | B |
| D6a `maplibre-gl` | A | `frontend/package.json:12`, mai importato | B |
| D6b `manualChunks` deck.gl/maplibre, commento `ImdfPreviewModal` | A | `frontend/vite.config.js:47-75` | B |
| D6c mock MSW login con shape diversa | A | `mocks/handlers.js:24-25` `{access_token, user}` vs `context/AuthContext.jsx:84-90` (`data.user_id`, `data.username` in radice) | B |
| D6d nome pacchetto | A | `frontend/package.json:2` `canvas-query-builder-frontend` | B |

## U — Review dal vivo

### Conferme dal vivo

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| R1.8 Manipola "modificato" muovendo la vista | C | Fase 3 (34b94046); `tests/shared/canvasMutationChannels.contract.test.js` | A |
| P29/B1 sidebar Estrai | A | vedi P29 | B |
| R3.13 "2 tabelle, 1 join" | A | `QueryPreview.jsx:332` `{nodes.length} tabelle, {edges.length} Join` con `nodes = dataNodes` (`hooks/useCanvasCore.js:123-126`, esclude solo i commenti): Output contato come tabella, arco tabella→Output come Join | B |
| P21/M29 RUN TOTALI "questa pagina" | A | vedi P21 | M |
| R11.3 enum grezzi ticket | A | vedi R11.3 | B |
| R8.20/M13 `esecutore` senza badge | A | `UserManager.jsx:22-30` RoleBadge senza `esecutore`; nessuna `.um__badge--esecutore` in `styles/UserManager.css:153-156` | B |
| P20 Object Storage / Code e Worker diversi | A | vedi P20 | M |
| L12 badge sidebar solo sulla pagina corrente | A | `components/AppSidebar.jsx` invariato dall'audit | B |

### Manipola

| Voce | Stato | Prova | Grav. |
|---|---|---|---|
| U1 tutti i nodi "Trasformazione" | C | titolo e riassunto derivati: `components/canvas/nodeDescriptors.js:164,244-326` | — |
| U2 nodo ripetitivo | C | nodo compatto + inspector (M1): `components/canvas/CompactNode.jsx`, `NodeInspector.jsx` | — |
| U3 catene Rinomina / suffissi `_L_L` | C | Join con piano colonne (`components/transforms/panels/JoinPanel.jsx`, B2), "Seleziona e rinomina" (`SelectColumnsPanel.jsx`, B5/M5) | — |
| U4 Filtra/Formula in fila, Case When come tabella | C | `FilterPanel.jsx`, `FormulaPanel.jsx`, `LookupSuggestion.jsx` | — |
| U5 catene ricostruite a mano | C | "Duplica ramo" `components/canvas/BranchSection.jsx:125` (M6) | — |
| U6 layout, minimappa | C | `components/editor/EditorShell.jsx:62,72` MiniMap + autoLayout | — |
| U7 join "SX: Formula" | C | `TransformNode.jsx:93-104` usa `nodeTitle` del passo a monte | — |
| U8 Output ben fatto (modello) | NPA | osservazione positiva, non un difetto | — |

### Estrai

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| E1 editor piu' pulito (riferimento) | NPA | osservazione positiva | — |
| E2 schermata "Caricamento progetto…" 5-7 s, catalogo in serie | A | `hooks/useEditorData.js:209-243`: progetto → connessioni → `loadCatalog` (`getConnectionTables`) in serie, `loading` resta true fino alla fine; `EditorShell.jsx:324` mostra l'overlay fino ad allora | M |
| E3 "Output" sbiadito senza spiegazione | A | `EstraiEditor.jsx:396` `disabled: hasResultNode` senza motivo nel titolo della voce | B |
| E4 "Connessioni" esce senza guard | C | `EstraiEditor.jsx:391` `if (confirmNavigation()) navigate('/connections')` | — |

### Dashboard

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| DB1 cestino minuscolo accanto ad "Apri" | A | `ProjectCard.jsx:156-162`, `EseguibileCard.jsx:33-43` | M |
| DB2 9 tile KPI, molte a 0 | A | `ProjectList.jsx:368-389` sempre 9 tile, solo 3 cliccabili | B |
| DB3 card rumorose, nessuna vista a elenco | A | `ProjectCard.jsx:92` "Nessuna descrizione"; nessuna vista tabella in ProjectList | B |
| DB4 titoli lunghi troncati sotto il badge | A | `styles/ProjectList.css` invariato dall'audit | B |
| DB5 voci sidebar = ancore della dashboard, titolo "Dashboard" | A | `AppSidebar.jsx:135-140` ancore `#key` su `/` | B |
| DB6 sezione Arricchisci vuota enorme | A | `ProjectList.jsx` invariato su quella sezione (ProjectSection con empty state) | B |

### Pagine di sistema

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| S1 Eseguibili fuori dal design system | A | `pages/EseguibiliList.css:6` `var(--bg-primary, #0f1419)` (non definita → sempre scuro); `EseguibiliList.jsx:152` "← Dashboard" | M |
| S2 Ticket: intestazione diversa, vuoto sopra la tabella, enum | A | `TicketsList.jsx:118-130,187-201` (messaggio vuoto prima di `<table>`) | B |
| S3 Connessioni "Non testata", colonna AZIONI, Connection Set vuoto | A | `ConnectionManager.jsx` e `ConnectionSetsPanel.jsx` invariati; `testResults` solo in memoria | B |
| S4 API Esterne e Scheduler: KPI e filtri a 0 | A | `ScheduleList.jsx:315-389` tile sempre mostrati; ApiConnectionManager invariato | B |
| S5 Log Viewer: data senza anno/fuso, Progetto = ID troncato | A | `logViewerHelpers.jsx:36-41,48-51` (`truncateProjectId`) | M |
| S6 UserManager senza titolo, esecutore senza badge | A | `UserManager.jsx` nessun `<h1>`; RoleBadge `:22-30` | B |
| S7 Object Storage: "Elimina tutte le preview" primo pulsante rosso | A | `AdminObjectStoragePage.jsx:215-218` | M |
| S8 "← Indietro" su pagine di primo livello (`navigate(-1)`) | A | `navigate(-1)` in ApiConnectionManager, ConnectionManager, DataSourceManager, FileManager, LogViewer, ScheduleList | B |

### Linee guida

| Voce | Stato | Prova | Grav. |
|---|---|---|---|
| G1 un solo layout di pagina | P | editor: shell unica (EditorShell/EditorToolbar); pagine di sistema no (S1-S8) | M |
| G2 un solo componente di stato vuoto | A | nessun componente comune; stati vuoti diversi in TicketsList, ConnectionSetsPanel, ProjectList, FileManager | B |
| G3 azioni distruttive mai accanto alle primarie | A | DB1, S7 | M |
| G4 nodi sul modello Estrai/Output | C | M1 (CompactNode + inspector) nei tre editor | — |
| G5 formati uniformi | A | R11.1-R11.4, S5 | M |

## Rimandati del piano frontend (`docs/development/piano-frontend-manipola-2026-11.md`)

| # | Voce | Stato | Prova |
|---|---|---|---|
| 1 | id delle colonne nell'Output fino all'esecuzione | C | Blocco A A1/F2: `name_column_id` in `components/nodes/result/standard/OutputColumnsSection.jsx:4-5`; backend `app/core/dataframes/output_columns.py`; rapporto A1 "Output per id" (differenze volute `output_cols:7:1`, `preview:p_output_id_rename`) |
| 2 | Join tra tabelle di Estrai nel pannello | C | commit 03f87b79; `components/canvas/JoinEdgePanel.jsx`, `NodeInspector.jsx` EdgeInspector |
| 3 | Lookup "Riempi con i valori presenti" | C | `utils/lookupFill.js`, `ValueMappingInput.jsx:49-81`, `POST /nodes/{node}/distinct`; `tests/components/LookupFill.contract.test.jsx` (residuo legacy: BA3) |
| 4 | GROUP BY su Output di Arricchisci | C | `backend/app/core/projects/schema_inference.py:571` raggruppa solo `kind == "manipola"` |
| 5 | schema previsto ≠ esecuzione sui Join vecchi | C | parita' A1 (`tests2/contracts/column_plans_cases.json`, test di parita' per ogni operazione) |
| 6 | titolo "Condizionale (Case When)" dopo Mappa valori | C | `TransformNode.jsx:125-137` niente titolo scritto; `nodeDescriptors.js:266-302` titoli legacy generati ignorati (vedi N7) |
| 7 | intestazione a scheda dell'Output | C | commit af5331f9 (il pannello possiede intestazione ed eliminazione) |
| 8 | Arricchisci con input runtime: due finestre | C | finestra unica `BeforeRunModal` + `PipelineRunner.openWindow` (`PipelineRunner.jsx:139-143`); test BeforeRun |
| 9 | nomi in inglese nel menu | C | decisione glossario (piano Blocco A regola 10), `utils/glossary.js` |
| 10 | codice morto `onCaricanExecute` | C | vedi D1 |
| 11 | copia di sicurezza in due schede | C | commit 3c1aa2d8 (per utente, progetto e scheda); `tests/components/DraftTabs.contract.test.jsx` |
| 12 | Valida non segnala Formula non valida ne' input scalare non collegato | C | A3: Valida = preparazione dell'esecuzione + `compile_manifest` (rapporto A3 riga 27, test "Input 1 non e' collegato a nessun campo") |

## Residui dichiarati del Blocco A

| Voce | Stato | Prova / scenario | Grav. |
|---|---|---|---|
| BA1 tipi previsti del Lookup approssimati | A | `backend/app/core/projects/schema_inference.py:770-781` `lookup` tra le operazioni con tipi `indeterminate`; rapporto A3: 380 casi su una matrice 5×6×4×2×2 con tipo previsto ≠ eseguito. La griglia dell'Output e i controlli di tipo a valle del Lookup lavorano su un tipo che puo' non essere quello vero | M |
| BA2 Eseguibili senza la finestra unica «Prima di avviare» | A | `components/eseguibili/EseguibileExecuteWizard.jsx` + `ExecutableDynamicRunForm.jsx`: nessuna stima/preflight ne' impronta (nessuna occorrenza di `preflight`/`BeforeRun` in components/eseguibili, EsecutoreHome, EseguibiliList). Scenario: eseguibile pubblicato da un Arricchisci → l'esecutore avvia senza stima delle chiamate ne' controllo `preparation_changed` | M |
| BA3 proposte di «Riempi» su configurazioni legacy | A | `utils/lookupMapping.js:15-22` legge ogni chiave con `String(...)`, `utils/lookupFill.js:74-76` (`toSavedRows`) riscrive TUTTE le righe come testo alla prima scrittura (compilare una sola proposta di Riempi basta). Scenari: (a) mappa legacy `[{"from": true, "to": "Si"}]` su colonna booleana: il backend confronta `lookup_value_text(True) = "True"` (`backend/app/shared/execution/lookup_mapping.py:106-107,169-178`); dopo la riscrittura la chiave e' `"true"` → nessuna corrispondenza, risultato cambiato in silenzio; (b) chiave numerica `5` su colonna di testo (object): a HEAD corrispondeva al valore 5, dopo diventa `"5"` e non corrisponde piu' (conseguenza dichiarata in A3); (c) chiave `null` → riga con «valore» vuoto e «diventa» pieno → `ValueMappingInput.jsx:39` errore "Ogni riga ha bisogno del valore di partenza" che blocca il salvataggio di tutta la mappa | M |

Nota: il rapporto A3 dichiara anche, lato backend, **P1 APERTO** (matrice negativa esaustiva delle 71 operazioni) e **P2 PARZIALE** (unificazione delle regole) (`docs/audits/blocco-A-A3-rapporto-2026-11-04.md:195-196`); non riverificati qui (fuori dal perimetro frontend richiesto).

---

## Nuovi difetti (stessa classe, non nell'audit)

| Id | File:riga | Scenario | Grav. |
|---|---|---|---|
| N1 | `utils/lookupMapping.js:15-22`, `utils/lookupFill.js:74-76` | Riscrittura delle chiavi legacy non testuali della Mappa valori (dettaglio in BA3): una modifica qualsiasi cambia il risultato di altre righe | M |
| N2 | `components/transforms/TransformNode.jsx:127-129,134` | Cambio di operazione dal `<select>`: `config: {}` senza conferma (resta solo Ctrl+Z) e gli archi di binding degli input runtime verso i parametri della vecchia operazione restano sul nodo; `hasParameterBindings` resta vero e forza il modulo generico anche sulla nuova operazione | M |
| N3 | `components/modals/ExecutionModal.jsx:92-101` | "Scarica report errori": nessun try/catch (410/404 → promise rifiutata, nessun messaggio) e `revokeObjectURL` subito dopo `click()` (stessa classe di R9.14/P23, punto non elencato) | M |
| N4 | `components/modals/ExecutionModal.jsx:116,133,139-140` | Manipola/Estrai: durante l'esecuzione la finestra non si chiude in nessun modo (Chiudi disabilitato, × assente, Esc spento) mentre il task e' asincrono: editor bloccato per tutta la durata (estensione di R9.4) | M |
| N5 | `frontend/tests/shared/htmlSinks.contract.test.js:24` | La guardia P1 riconosce `bindTooltip/bindPopup` solo con un letterale stringa come primo argomento: `layer.bindTooltip(html)` con `html` stringa, o `L.divIcon({ html })`, passano il test | B |
| N6 | `components/canvas/NodeInspector.jsx:83-84,107`, `components/canvas/StepsView.jsx:22-23` | Nuovi selettori su tutto `nodes`/`edges` (M1/M6): pannello laterale e vista Passi ri-renderizzati a ogni frame di trascinamento (classe perf-1) | B |
| N7 | `components/canvas/nodeDescriptors.js:286-288`, `utils/transformRegistry.js:56` | `isGeneratedName` usa il registro in cache: prima che sia caricato un titolo legacy uguale al nome di un'operazione (es. "Condizionale (Case When)") e' trattato come titolo dell'utente, poi cambia a registro caricato | B |
| N8 | `pages/ApiConnectionManager.jsx:209`, `pages/ConnectionManager.jsx:178`, `pages/DataSourceManager.jsx:149` | Stessa omissione di P25 (dipendenze nascoste) in altre 3 pagine | B |

## Codice morto nuovo (lasciato da Fasi 1-4 e Blocco A)

Ricerca: export senza chiamanti fuori dal file (`scratchpad/f5raccolta/unused_exports.txt`), classi CSS senza uso in JS/JSX (`unused_css.txt`, filtrate a mano dai modificatori dinamici `--${...}`), origine con `git log -S`.

| Elemento | File:riga | Origine | Note |
|---|---|---|---|
| `reachingOutput`, `danglingNodeIds`, `stepLanes` | `utils/canvasGraph.js:185,188,191` | 0d331928 (M6) | usati solo dai test |
| `addedColumns` | `utils/columnSource.js:335` | 9f13fe3a (F2) | solo test |
| `DESCRIBED_NODE_TYPES` | `components/canvas/nodeDescriptors.js:329` | cd437c91 (M1) | nessun uso, nemmeno nei test |
| `hasNodeDescriptor` | `components/canvas/nodeDescriptors.js:229` | cd437c91 (M1) | solo test |
| `nodeTerm` | `utils/glossary.js` | 33ad698d (G) | solo test |
| `.canvas-view-toggle` | `components/canvas/StepsView.css:46-52` | 0d331928 (M6) | la barra usa `.etb-seg` |
| `.ppanel__card--draft` | `components/transforms/panels/panels.css` | 0fb30f58 (M4) | mai usata |
| `.arricchisci-theme-toggle`, `.arricchisci-theme-opt(--on)` | `styles/ArricchisciEditor.css` | resti di M7 (cambio tema nel menu) | usate al baseline 86cca5e0 |
| `.manipola-toolbar__badge` | `styles/ManipolaEditor.css` | resti di M7 | usata al baseline |

Codice morto preesistente non elencato nell'audit (per completezza della classe D): `utils/graphUtils.js` (`detectCycle`, solo test); `api/features.js` `queryLayer`, `writeFeatures`, `createFeatureService`; `api/shares.js` `addShare`, `removeShare`; `api/users.js` `getMe`; `api/eseguibili.js` `analyzeUpload`; `api/projects.js:239` e `api/schedules.js:64` `getDownloadUrl` (il commento a `schedules.js:82` dice che e' stato sostituito); `components/nodes/StaticValuesNode.jsx:270` `createStaticValuesNode`; `store/useCanvasStore.js` `getCanvas`; `utils/columnUtils.js:196` `deduplicateColumns` (solo test); `constants/canvasTypes.js` `COLUMN_TYPES_BASE`, `DEST_TYPES`, `DEST_TYPES_CARICAN_UI`, `MULTI_JOIN_TYPES` (solo test); `api/openapiClient.js` (strumento dei test dentro `src/`); CSS `.estrai-sidebar__subtitle`, `.manipola-hex-canvas`, `.arricchisci-hex-canvas`, `ValidationModal.css` `__header/__footer/__overlay/__close/__btn/__title` (la finestra usa ModalWrapper), `ExecutionModal.css` `__spinner/__loading-text`.

Hook e componenti senza importatori: nessuno (verificato su `src/hooks/*` e `src/components/**/*.jsx`). Endpoint API introdotti dal Blocco A (`distinctNodeValues`, `preflightEnrichment`, `validateProjectCanvas`): tutti con chiamanti.
