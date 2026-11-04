# Blocco A — raccolta difetti frontend (04/11/2026)

Sola lettura. Percorsi relativi a `frontend/src` salvo dove indicato. [sonda] = verificato con un test vitest temporaneo.

## Classe 1 — Colonne a valle calcolate nel frontend

### Chi scrive le colonne

**Manipola**
- **W1** `hooks/useColumnPropagation.js` (propagateColumns). Scrive `data.inputColumns`, `data.columns` e `config` (syncConfigColumnReferences). Le regole sono copiate dal backend nello switch, in `utils/extendedTransformSchema.js` e in `utils/columnPlans.js`.
- **W2** `pages/ManipolaEditor.jsx` (~218-244). Fonde in `data.columns` di ogni nodo lo schema del backend (POST `/schemas`).
- **W3** `ManipolaEditor.jsx` (~254-289). Calcola `availableColumns` dell'Output e dei Carica da `schemas[nodo a monte]`, poi chiama `withAvailableColumns`.
- Le chiamate imperative a W1 sono sparse, ognuna con il suo setTimeout.

**Arricchisci**
- **W4** `components/panels/ApiEnrichNode.jsx` (~195-295). Un effetto dentro il componente scrive `data.columns` dell'api_enrich.
- **W5** `pages/ArricchisciEditor.jsx` (~204-280). Calcola `availableColumns` come unione delle sorgenti a monte più gli output_mapping.

**Estrai**
- **W6** `pages/EstraiEditor.jsx` (~143-168) con `utils/columnUtils.js` calculateOutputColumnsRecursive.
- **W7** `OutputColumnsSection.jsx` EstraiOutputColumns. Scrive visible/alias sulle sorgenti.

**Al salvataggio**
- **W8** `utils/canvasSerializer.js` _computeResultColumns. Prende il primo nodo a monte.
- **W9** il backend ricalcola `result.columns` (`canvas_columns.py`).

**Lettori**
- **R1** ResultNode upstreamColumns. Alimenta la griglia «Colonne in uscita», GROUP BY/ORDER BY e la geometria.
- **R2** pannelli dei passi (TransformNode): leggono `data.inputColumns`. Riguarda Join, Lookup, Formula, Filtra, DynamicConfig e SelectColumns.
- **R3** MultiJoinNode.
- **R4** Carica e Carica ArcGIS: leggono `availableColumns`.
- **R5** Collega: propone la chiave del Join.
- **R6** ApiEnrichNode: colonne in ingresso.

Nel registro dei tipi `data.columns` derivato viene **salvato** (transform, multi_join, api_enrich, result).

### Difetti
1. [ALTA] Due scrittori di `data.columns` in Manipola (W1 e W2), vince l'ultimo. La griglia dell'Output cambia tra lo schema del backend e il calcolo del frontend; intanto i Carica usano sempre il backend.
2. [ALTA] Modificare un passo (handleUpdateTransform) non ricalcola gli schemi del backend. deleteNode non propaga né ricalcola, e i passi a valle tengono `inputColumns` vecchi.
3. [ALTA] Le colonne nascoste delle sorgenti passano a valle nel frontend, non nel backend (extractSourceColumns; anche R1 e R3). [sonda]
4. [ALTA] W2 riscrive anche le sorgenti con lo schema del backend: le colonne nascoste spariscono e non si possono più rendere visibili. La fusione per nome perde id, alias e visible.
5. [ALTA] project_input di tipo file: niente colonne a valle nel frontend (isSourceNode non lo comprende). [sonda]
6. [ALTA] multi_join, datasource_node e staticValues non hanno un gestore nell'inferenza del backend: schema [] o parziale ma «certain». multi_join esiste solo nel calcolo del frontend.
7. [ALTA] Arricchisci: W4 vive in ApiEnrichNode, montato solo nel pannello laterale. Le colonne dell'arricchimento si aggiornano solo con il pannello aperto.
8. [MEDIA] Arricchisci: Output e Carica usano due calcoli diversi (W4 e W5). Il backend ha `_infer_api_enrich_schema`, ma il frontend non lo chiede mai.
9. [MEDIA] Il GROUP BY dell'Output di Arricchisci viene applicato nel calcolo (withAvailableColumns), mentre l'interfaccia lo nasconde e l'esecuzione non lo applica.
10. [MEDIA] Estrai, calculateOutputColumnsRecursive:
    - mette in minuscolo i nomi delle tabelle;
    - `name` contiene il nome sorgente, non quello in uscita;
    - colonne con lo stesso nome restano doppie;
    - ordine all'indietro;
    - ignora GROUP BY e aggregazioni;
    - nessun id.
11. [MEDIA] Il backend non sa fare lo schema di Estrai: `_infer_result_node_schema` usa solo il primo ingresso. Il valore salvato non coincide con quello in memoria.
12. [MEDIA] Gli schemi applicati dopo che il canvas è cambiato non vengono scartati. Lo `status` certain/indeterminate non arriva al frontend.
13. [MEDIA] MultiJoinNode legge le sorgenti nell'ordine degli archi, non in quello delle maniglie; include le colonne nascoste.
14. [MEDIA] syncConfigColumnReferences riscrive come dato derivato la configurazione dell'utente (espressioni, `*_column_refs`).
15. [BASSA] Concatena: con lo stesso nome resta solo l'id della prima sorgente.
16. [BASSA] R1 si aggiorna solo sui nomi; un cambio di tipo (per esempio la geometria) non lo aggiorna.
17. [BASSA] `fetchNodeSchema`, `validateConnection` e `validateAllConnections` sono morti; fetchNodeSchema legge il canvas salvato.

### Dove si perdono gli id delle colonne
ResultNode upstreamColumns; manipolaColumnsRaw; PipelineOutputColumns (`gridConfig withIds:false`, che toglie anche `name_column_id`); availableColumns di Manipola e Arricchisci; W2 (non copia `id`, `source_name` e `display_name`); ApiEnrichNode; Estrai (nessun id); colonne create (formula, aggregate, …) senza id anche nel backend; Duplica ramo (gli id della sorgente originale); Concatena.

### Se la fonte unica diventa lo schema del backend
- Estrai serve un gestore del backend (join sugli archi).
- Arricchisci deve chiamare POST /schemas.
- Manipola serve gestire multi_join, datasource_node e staticValues.
- I pannelli hanno bisogno delle colonne in ingresso per lato e per sorgente.
- Latenza di rete a ogni modifica.
- Con errore o offline restano gli schemi vecchi: serve un esito per nodo.
- W2 non deve toccare le sorgenti.

Test che fissano il comportamento attuale: useColumnPropagation.* (3 file), transformSchemaRegistryParity, extendedTransformSchema, columnPlansParity, columnUtils (minuscole e doppioni), canvasSerializer:106, ManipolaEditor/ArricchisciEditor/EstraiEditor contract, ResultNode.gating, ResultNodeStandardView, CaricanNode.rest, operationPanels.

**Regola proposta:**
- Un solo selettore `columnsOf(nodeId, side?)` legge una sola mappa, lo schema del backend calcolato sul canvas dell'editor, con `{id, name in uscita, type, source_name}` ed esito.
- La mappa si ricalcola da un'unica sottoscrizione allo store.
- Le colonne derivate spariscono dai nodi e dal salvataggio.
- Tutti i lettori passano dal selettore.
- Test di enumerazione: ogni tipo di nodo ha un gestore nel backend; nessun file scrive colonne derivate; i lettori ricevono solo colonne con id.

## Classe 2 — Altri rimandati

### a) Arricchisci: due finestre, stima senza input
1. Due finestre e due pulsanti di avvio di fila.
2. Per il superadmin la doppia finestra c'è sempre.
3. Validate ed Estimate non ricevono `inputs`; i dati vengono dal file o dall'ultimo output.
4. Una sorgente project_input file o datasource fa stimare l'ultimo risultato dello stesso progetto.
5. Si stima solo il primo api_enrich.
6. [alta] Un mapping a template risulta «colonna mancante» ed Esegui viene disabilitato.
7. Gli `errors` non vengono mostrati: «Problemi rilevati» compare con il corpo vuoto.
8. `isValid ?? true` rende Esegui attivo prima della risposta.
9. Validazione vecchia non azzerata.
10. La stima ignora cache e «Ricalcola tutto», conta i parametri nulli ed è sincrona su 300k righe.
11. Basta un solo rest_file per saltare la conferma di tutti i nodi.
12. Stati duplicati (`pendingExecuteOptions`/`pendingOptsRef`); runState torna 'idle' con le finestre aperte.
13. Annulla nella seconda finestra non annulla la prima.
14. Negli Eseguibili la stima non si fa mai.

Regola: una sola finestra «Prima di avviare» con la stima come sezione, lo stesso payload dell'esecuzione (inputs) e la stessa preparazione.

### b) Estrai: join nella finestra JoinModal
1. Annullando dopo l'inserimento dalla palette, la tabella resta scollegata (due passi di undo).
2. Le colonne vengono preselezionate automaticamente.
3. Nessun «(non trovata)» per una colonna sparita.
4. Residui di dati spaziali o standard.
5. Salva senza modifiche segna comunque «non salvato».
6. In sola lettura la finestra è modificabile.
7. La vista Passi non mostra i join.
8. Il pannello laterale non mostra i join di una tabella; il tasto destro sull'arco modifica invece di eliminare.
9. editingEdge è un'istantanea: dopo un undo si riapplicano dati vecchi.
10. Commento falso.

Regola: ogni configurazione si modifica nel pannello a partire dalla selezione, nodo o arco (JoinEdgePanel); un arco nasce «da completare»; nodo e arco in un solo passo di undo.

### c) Lookup «Riempi con i valori presenti»
Non esiste un endpoint di valori distinti.

Limiti:
- campione di 10k righe per sorgente;
- usa il canvas salvato;
- la colonna va risolta sul nodo a monte;
- con input file non ci sono dati;
- i valori vanno resi in testo come fa lookup_mapping.py;
- serve un tetto sulla cardinalità;
- ValueMappingInput non riceve `disabled`.

Regola: un unico contratto del server per i dati letti, con canvas preparato come in esecuzione, `sampled`/`truncated` e testo dei valori prodotto dalla funzione di esecuzione.

### d) Titoli congelati
1. [alta] NodeInspector precompila il campo con il titolo derivato e lo salva al blur.
2. [alta] Campo non controllato: dopo una conversione mostra il valore vecchio.
3. CompactNode fa lo stesso con il doppio clic.
4. Cambiare operazione lascia il titolo congelato.
5. Collega e Duplica ramo creano titoli congelati.
6. Unione di Filtra.
7. Ripristino e undo.
8. `label` delle tabelle.
9. Arricchisci.

Regola: `data.title` contiene solo testo dell'utente; campo controllato con il derivato come placeholder; svuotarlo torna al derivato.

### e) Intestazione vecchia nel pannello laterale
Hanno ancora intestazione e × propri: tableNode, featureLayerNode, datasourceNode, staticValues, project_input, project_source, file_source, multi_join, api_enrich, result, carica e carica_arcgis.

1. [alta] Due × vicine: una chiude il pannello, l'altra elimina il nodo (su 8 tipi senza conferma).
2. Doppio titolo diverso.
3. Il titolo modificato non arriva alla scheda.
4. Carica e DataSource si comprimono con un clic sull'intestazione.
5. L'Output ripete Valida, Preview ed Esegui con nomi diversi.
6. «Eseguito automaticamente con Esegui Query» compare anche in Manipola e Arricchisci.
7. La vecchia intestazione compare durante il caricamento.
8. Il cambio di modalità del DataSource svuota le colonne senza chiedere.
9. `selected` sempre vero dentro il pannello.
10. In sola lettura il × è visibile.
11. Solo TransformNode è coperto da test.

Regola: intestazione, chiusura ed eliminazione appartengono solo alla shell; titolo e icona vengono da nodeDescriptors; test di enumerazione su tutti i nodeTypes.

### f) Codice morto
- Le azioni onCaricanExecute, caricaExecution e useCaricarica.handleExecute sono registrate ma senza chiamanti. Si portano dietro useCaricarica (353 righe), CaricaricaModal (.jsx e .css), createTask/`api/tasks.js` e un secondo ExecutionModal in Manipola con `projectType` sbagliato, più i relativi test e la documentazione.
- Il ramo backend `TaskType.CARICA` va verificato contro i client esterni.
- Altro:
  - onUpdateProjectSource* senza lettori;
  - funzioni dentro `node.data` nel drop di Manipola;
  - `graphUtils.getNodeName`;
  - ramo datasourceData di onDrop in Estrai;
  - ResultNode sottoscritto a un oggetto mutabile;
  - deps instabili che registrano le azioni a ogni render.

Regola: una tabella dichiarativa EDITOR_ACTIONS (nome, chi registra, chi consuma) e un test per ogni nome senza lettore.

### g) Nomi
Termini inglesi nel testo visibile: «Data Source» ~26 righe, «Output» ~45, «Join» ~14, «Preview» 9 contro «Anteprima» 11, «Pipeline» 7, «Multi-Join» 6, «Run» ~6. Altri arrivano dal catalogo `transforms_registry.json`.

La stessa entità ha nomi diversi: Multi-Join, Join, Concatena, Mappa valori, Output, Esegui, Preview/Anteprima, Carica, Data Source, Arricchimento. Anche il riepilogo mostra chiavi tecniche.

Regola: un glossario dichiarativo unico; da decidere quali anglicismi restano.

### h) Copia di sicurezza con due schede
1. [alta] Due schede sovrascrivono la stessa chiave.
2. [alta] Si ripristina un contenuto diverso da quello annunciato.
3. [alta] Il salvataggio di una scheda cancella la copia dell'altra; lo stesso fa un ripristino di versione.
4. [alta] PUT `/canvas` senza controllo di versione: l'ultimo salvataggio vince in silenzio.
5. Nessun ascolto dell'evento `storage`.
6. Con l'offerta aperta il lavoro nuovo non ha copia.
7. Il ripristino azzera la cronologia, comprese le modifiche correnti.
8. Annulla dopo il ripristino perde la copia.
9. La chiave usa lo username perché manca `u.id` (il login salva `user_id`).
10. Le copie orfane non scadono mai.
11. Lo spazio pieno passa in silenzio.
12. Un progetto eliminato da un'altra scheda fa rinascere la copia.
13. Uscita entro 1,5 s dall'ultima modifica.
14. Il 401 perde le modifiche.
15. `serverNewer` usa orologi diversi.
16. Logout su un computer condiviso.

Regola: chiave utente(id):progetto:scheda con `baseUpdatedAt`; ogni scheda cancella solo la propria copia; salvataggio condizionato alla versione; ascolto di `storage`; pulizia globale; tabella delle transizioni con test in due schede.
