# Audit frontend Plenora: raccolta difetti e miglioramenti

**Data:** 03/11/2026. **Fase:** raccolta (regola raccolta → correzione unica → conferma). Nessun file è stato modificato.

**Lettori indipendenti:**
- Claude, 4 lettori: Estrai; Manipola/Arricchisci; pagine di gestione; admin, login e parti trasversali.
- Codex: tutte le 21 route, senza vedere i risultati di Claude.

**Legenda delle voci:**
- `✦` = trovato **sia da Claude sia da Codex**: massima affidabilità.
- `[Cl]` = solo Claude; `[Cx]` = solo Codex.
- Gravità: **A** = alta, **M** = media, **B** = bassa.
- Le voci marcate *verif.* sono state ricontrollate sul codice anche in fase di consolidamento.

**Percorsi:** sono relativi a `frontend/src/`. I numeri di riga sono indicativi (±5).

---

## Sintesi

Quasi tutti i difetti nascono da **11 regole mancanti**: logiche che oggi ogni pagina o editor reimplementa a modo suo, con copie che si sono già differenziate. Secondo la regola di correzione, ogni gruppo qui sotto va chiuso **per costruzione**, cioè con un meccanismo unico e un test di enumerazione, e non voce per voce.

| # | Regola strutturale | Cosa chiude | Rischio attuale |
|---|---|---|---|
| R1 | Le modifiche al canvas passano solo da azioni esplicite dello store | modifiche non salvate, undo rotto, finto "modificato" | **perdita di dati** |
| R2 | Tabella dichiarativa dei tipi di nodo (factory, serializer ed edge da una sola fonte) | campi persi al reload, nomi di tipo incoerenti | **perdita di config** |
| R3 | Un solo modulo per colonne e grafo | colonne sbagliate, mapping errati, preview SQL falsa | risultati errati |
| R4 | Risorse e task asincroni con un unico hook | race, spinner infiniti, errori mostrati come liste vuote, pagina che lampeggia | UX e dati stantii |
| R5 | Toast, sessione ed errori: un solo contratto | loop di fetch, logout spurio, crash globali | **stabilità** |
| R6 | Stack di modali e wizard sicuri di default | Esc che chiude tutto, salti di validazione, doppio submit | perdita di input |
| R7 | Guard di navigazione unico | modifiche perse uscendo, versioni sovrascritte, link rotti | perdita di dati |
| R8 | Form dichiarativi e input con buffer locale | campi in cui non si può digitare, payload diversi dalla UI | UX e dati |
| R9 | Macchina a stati unica per le esecuzioni | input runtime ignorati, "successo" falso, file non puliti | **correttezza** |
| R10 | Permessi solo tramite `can.*`, perimetri espliciti | azioni visibili a chi non può farle, bonifiche più ampie del previsto | **sicurezza** |
| R11 | Formatter unici (data con fuso, dimensioni, enum) | orari UTC mostrati come locali, valori grezzi | UX |

A queste regole si aggiungono **difetti puntuali** (§ P) e **codice morto da rimuovere** (§ D).

### Le 12 voci più urgenti

1. ✦ **XSS nel tooltip della mappa** in PreviewModal: valori DB inseriti come HTML. *verif.* (§ P1)
2. ✦ **Toast context instabile**: loop infinito di fetch e toast nelle pagine admin, Tickets e /run. *verif.* (R5.1)
3. ✦ **Modifica di un join esistente non marcata come modificata**: Esegui usa la configurazione vecchia e Salva non salva. *verif.* (R1.1)
4. ✦ **Test connessione Data Source finto**: risponde sempre "riuscita". *verif.* (§ P2)
5. ✦ **"Bonifica orfani"**: la conferma mostra il perimetro filtrato, l'operazione è globale; gli orfani sono riconosciuti dal nome. (R10.4)
6. [Cx] **Condivisioni sovrascrivibili** dopo un caricamento fallito. *verif.* (R10.6)
7. ✦ **Input runtime ignorati** in Manipola e Arricchisci. (R9.1)
8. ✦ **Modificare uno schedule disattivato lo riattiva.** (R8.12)
9. [Cx] **La rinomina di una colonna riscrive anche i letterali** nelle espressioni. *verif.* (R3.12)
10. ✦ **Il nodo Carica perde l'indice univoco** al reload, e il salvataggio successivo lo azzera. (R2.1)
11. ✦ **`useInlinePreview` senza import**: lo spinner resta per sempre al primo errore. *verif.* (R4.10)
12. ✦ **Revoca dei permessi mostrata come successo** con "Scarica risultato". (R9.2)

---

## R1 — Mutazioni del canvas solo tramite azioni esplicite dello store

**Radice.** `setNodes` e `setEdges` dello store marcano il canvas come modificato solo se cambia la struttura (id aggiunti o rimossi), `store/useCanvasStore.js:357-387`. Le modifiche dell'utente che lasciano invariati gli id non risultano modificate, mentre gli aggiornamenti derivati automatici passano da `updateNodeData`, che marca modificato e crea uno snapshot.

**Correzione unica.**
- Azioni nominate (`addNodes`, `addEdges`, `updateEdge`, `removeEdges`, `updateNodeConfig(id, patch)` funzionale) che impostano **sempre** modificato e snapshot.
- Un canale separato `applyDerived` che non imposta mai modificato.
- Vietare `useReactFlow().setNodes` dai nodi, con test di architettura.
- In sola lettura: no-op e controlli disabilitati tramite un hook `useNodeEditable()`.

| id | voce | file | gravità |
|---|---|---|---|
| R1.1 | ✦ Modifica di un join esistente via `setEdges` → non modificato, salvataggio saltato, nessun undo *verif.* | hooks/useEdgeHandlers.js:231 | A |
| R1.2 | [Cl] `exec_params` del Processore File via `useReactFlow().setNodes` (change `reset`) → mai salvati | components/panels/ApiEnrichNode.jsx:148 | A |
| R1.3 | [Cl] FilterModal modifica gli oggetti di `node.data` direttamente (copia superficiale): "Annulla" non annulla | components/modals/FilterModal.jsx:53,164 | A |
| R1.4 | [Cl] Upload paralleli di `extra_files` si sovrascrivono (snapshot vecchio) | ApiEnrichNode.jsx:459 | A [Cx] |
| R1.5 | [Cl] `updateConfig` fonde la patch con la config vecchia: le patch nello stesso tick si perdono (geom_column cancellata) | transforms/TransformNode.jsx:194, DynamicConfig.jsx:177-291 | M |
| R1.6 | [Cl] Aggiunta di nodi ed edge (toolbar, drop, related layer, Carica) senza snapshot: Ctrl+Z annulla due azioni insieme; coalescenza globale di 350 ms | EstraiEditor.jsx:271-383, useCanvasStore.js:205 | M |
| R1.7 | ✦ Tasto destro su un edge lo elimina senza conferma, senza undo, anche in sola lettura | ManipolaEditor.jsx:416, ArricchisciEditor.jsx:487, useCanvasCore.js:58 | M |
| R1.8 | [Cl] Effetti automatici che marcano modificato all'apertura: DynamicConfig, ColumnOrderInput, caricamento schema di FeatureLayer, reset formato del ResultNode (GeoJSON→CSV in silenzio), `_advOpen` nei dati del nodo, DataSourceNode con autosave dopo 100 ms | vari | M |
| R1.9 | ✦ Stato locale duplicato dello store e stantio dopo undo: `tempAlias`, `whereClause`, righe del Pivot, StaticValues | TableNode.jsx:33, FeatureLayerNode.jsx:59, PivotMappingBuilder.jsx:269, StaticValuesNode.jsx:36 | M |
| R1.10 | [Cl] In sola lettura i controlli restano attivi ma non fanno nulla (solo ProjectInputNode li gestisce) | nodi vari | M |
| R1.11 | [Cl] Eliminazione incoerente: con conferma per Table, FL e Result; senza per Carica, Input, Annotation, DataSource e ApiEnrich ✦; lo switch DB↔REST cancella la config | CaricanNode.jsx:62,105; ApiEnrichNode.jsx:332 | M |
| R1.12 | [Cl] Il cambio di tipo dell'input rimuove gli edge prima dello snapshot; `removeBinding` senza snapshot | ProjectInputNode.jsx:157 | B |

**Test di enumerazione:** per ogni controllo modificabile di ogni tipo di nodo, modifica → `isDirty === true` e undo ripristina; per ogni effetto derivato, l'apertura del progetto lascia `isDirty === false`.

---

## R2 — Tabella dichiarativa dei tipi di nodo

**Radice.** `nodeFactories.js` (deserializzazione), `canvasSerializer.js` (serializzazione), gli edge handler e `nodeTypes` di ciascun editor elencano campi e nomi di tipo separatamente, e le liste si sono già differenziate.

**Correzione unica.** Un registro `NODE_TYPES = { canonico, alias[], campi:{nome, default, persistito} }`. Factory, serializer e controlli di tipo derivano da lì. Test di round-trip: **per ogni tipo e ogni campo**, serializzazione seguita da deserializzazione restituisce lo stesso valore. Anche `access_denied*` viene conservato da un helper unico.

| id | voce | file | gravità |
|---|---|---|---|
| R2.1 | ✦ Carica: `create_unique_index` e `unique_key_column` non deserializzati → persi e poi azzerati al salvataggio | nodeFactories.js:193, canvasSerializer.js:203 | A |
| R2.2 | [Cl] Le factory scartano i flag `access_denied*` → nessun banner, salvataggio 400 senza spiegazione | nodeFactories.js:96-333 | A |
| R2.3 | [Cx] DataSource: `file_offset` non persistito | nodeFactories.js:311, canvasSerializer.js:81 | A |
| R2.4 | [Cl] Arricchisci "+ Enrichment → Data Source" crea `project_source` con `datasource_config` non serializzato → nodo vuoto dopo il reload | ArricchisciEditor.jsx:390,408 | A |
| R2.5 | ✦ `carica_arcgis` contro `caricaArcgis`: il ricollegamento apre JoinModal; il primo salvataggio passa dal fallback (mancano i default, `availableColumns` persistito) | useEdgeHandlers.js:143,183; canvasSerializer.js:215 | M |
| R2.6 | [Cl] `datasource_node` contro `datasourceNode`: due formati per lo stesso nodo | ManipolaEditor.jsx:468 | B |
| R2.7 | [Cl] Carica: `update_columns`, `if_table_exists` e `allow_unsafe_append` azzerati a ogni salvataggio; input legacy senza `input_kind` diventa `'file'` | canvasSerializer.js | B |
| R2.8 | [Cl] `spatial_distance` 0 diventa null; `connection_id` della tabella perso | nodeFactories.js:84 | B |
| R2.9 | [Cx] ApiEnrich: `parameters: []` normalizzato blocca il fallback alla definizione API | ApiEnrichNode.jsx:71, nodeFactories.js:449 | M |
| R2.10 | [Cl] Fotografia della connessione API mai aggiornata nel nodo, quindi i parametri restano vecchi | ApiEnrichNode.jsx:87 | M |

---

## R3 — Un solo modulo per colonne e grafo

**Radice.** Esistono almeno 5 calcoli diversi delle colonne a valle:
- `columnUtils.calculateOutputColumnsRecursive`;
- il serializer (solo il primo edge);
- `useColumnPropagation`;
- gli effetti `availableColumns` di ManipolaEditor e ArricchisciEditor;
- `QueryPreview`.

Ciascuno ha regole diverse su visibilità, alias, maiuscole e minuscole, edge e tipi.

**Correzione unica.** Un modulo `graphColumns` puro: colonne effettive di un nodo, risalendo gli edge, conservando `id`, `visible`, `alias` e `type`, con una sola normalizzazione dei nomi. Tutti i consumatori leggono da lì: Result, `availableColumns` di Carica, GROUP BY, colonne geometriche, preview SQL, validazione dell'enrichment. Le firme di confronto includono `name|alias|type|visible`.

| id | voce | file | gravità |
|---|---|---|---|
| R3.1 | [Cl] Manipola: `refreshSchemas` riscrive le colonne di tutti i nodi → le colonne nascoste tornano visibili e si perdono `id` e `alias` | ManipolaEditor.jsx:204 | A |
| R3.2 | ✦ Arricchisci: `availableColumns` aggrega tutte le sorgenti, anche scollegate; ignora `schema_fields` e StaticValues | ArricchisciEditor.jsx:217 | M |
| R3.3 | ✦ Arricchisci: validazione e stima usano il primo `api_enrich` e la prima sorgente dell'array, non il ramo collegato | ArricchisciEditor.jsx:265 | M |
| R3.4 | [Cl] Anteprima di Arricchisci: la mappa delle etichette usa `node.id`, ma `source` è un UUID o `'api_enrich'` → gruppi sbagliati | ArricchisciEditor.jsx:68 | M |
| R3.5 | [Cl] Manipola: `availableColumns` di Output e Carica usa lo schema **salvato**, non le colonne locali | ManipolaEditor.jsx:216 | M |
| R3.6 | ✦ Carica (Estrai): `availableColumns` vuoto dopo il reload a causa dell'early return | EstraiEditor.jsx:177 | M |
| R3.7 | ✦ Tre calcoli divergenti: minuscolo solo per tableNode; il serializer usa il primo edge | columnUtils.js:99, canvasSerializer.js:282 | M |
| R3.8 | ✦ La firma del ResultNode considera solo i nomi (alias e tipi ignorati), legge il primo edge, include le colonne nascoste | ResultNode.jsx:86 | M |
| R3.9 | ✦ La propagazione ignora i cambi di tipo | useColumnPropagation.js:178 | M |
| R3.10 | ✦ Multi-join: ordine delle sorgenti diverso tra UI (array) e propagazione (handle) → SX e DX invertiti; chiavi disallineate; `join_pairs` fantasma con 3 o più sorgenti | MultiJoinNode.jsx:33-100 | A |
| R3.11 | [Cx] La preview del multi-join elimina colonne che la propagazione mantiene con suffisso | MultiJoinNode.jsx:135 | M |
| R3.12 | [Cx] La rinomina automatica con regex riscrive anche i **letterali** nelle espressioni *verif.* | DynamicConfig.jsx:129 | A |
| R3.13 | ✦ La preview SQL diverge: mancano ORDER BY e HAVING, ignora i featureLayer, include tabelle scollegate, il filtro con valore 0 sparisce, nessun `AS`, `carica_arcgis` mostrato come JOIN | QueryPreview.jsx:145-302 | M |
| R3.14 | ✦ ApiEnrich: lo schema di output non viene svuotato quando l'input sparisce | ApiEnrichNode.jsx:283 | M |
| R3.15 | ✦ FeatureLayer: `relationships` mai popolato al drop → "+ Join" non compare mai; 3 mappature dei tipi ArcGIS; alias ArcGIS applicato come rinomina | FeatureLayerNode.jsx:79, EstraiEditor.jsx:190 | M |
| R3.16 | [Cl] GROUP BY e ORDER BY deduplicati per `alias\|\|name` → `comuni.id` e `province.id` collassano | ResultNodeStandardView.jsx:126 | M |
| R3.17 | [Cl] GROUP BY disattivato ma HAVING ancora inviato; alias `count_N` che collide e sovrascrive quello dell'utente; colonne stantie | GroupOrderHavingPanel.jsx:67 | M |
| R3.18 | [Cl] La select della geometria mostra `[0]` quando il valore è null o stantio | OutputSettingsPanel.jsx:146 | M |
| R3.19 | [Cl] Il progetto corrente compare tra le sorgenti trascinabili (ciclo) | useEditorData.js:185 | M |
| R3.20 | [Cl] Self-join senza alias perde colonne; `mapApiTypeToStandard('int')` restituisce `'str'` | columnUtils.js:163 | B |
| R3.21 | [Cl] ColumnSelector: colonna sparita a monte → la UI mostra il placeholder ma invia il nome vecchio | parameter-inputs/ColumnSelector.jsx:91 | M |

---

## R4 — Risorse e task asincroni: un solo hook

**Radice.** Ogni pagina gestisce fetch, polling ed errori a mano. Il problema è stato risolto in alcuni punti (generation counter in ScheduleList e useProjectListData) ma non negli altri.

**Correzione unica.** Due primitive condivise.

1. **`useResource(fetcher, deps, {pollMs, pauseHidden})`**, che gestisce:
   - un id di richiesta o AbortController;
   - `loading` solo al primo caricamento e `refreshing` non bloccante per i successivi;
   - `error` distinto da `empty`, con `retry`;
   - lo scope sul parametro della route.

   Va affiancata da un componente `<ListState>` per gli stati loading, error+retry, empty e no-results.

2. **`useTask`**, che gestisce:
   - tutti gli stati terminali (compresi `revoked` e `queued`);
   - backoff e limite di errori;
   - l'annullamento;
   - lo stop alla chiusura o all'unmount;
   - lo scope sul progetto.

**Race: risposte vecchie che sovrascrivono quelle nuove**

| id | voce | file | gravità |
|---|---|---|---|
| R4.1 | [Cx] Ticket: dati e id delle azioni appartenenti a ticket diversi; la bozza del commento passa da un ticket all'altro; l'invio cancella il testo scritto dopo | TicketDetail.jsx:60-123 | A |
| R4.2 | [Cx] Eseguibili: l'analisi del file A viene applicata al file B (l'esecuzione invia il `file_id` di A) | ExecutableDynamicRunForm.jsx:46 | A |
| R4.3 | [Cx] FileManager: la preview di A viene usata per caricare B; layer e foglio non vengono azzerati ✦ | FileManager.jsx:137,209 | A |
| R4.4 | ✦ ColumnBuilder: la SQL tradotta appartiene ai parametri precedenti | ColumnBuilderModal.jsx:137 | A |
| R4.5 | ✦ Log, Ticket, browser dei Data Source, Object Storage, System Status, Task Runtime, RunHistory, servizi e layer ArcGIS, test del connettore e seed del wizard, validazione dell'enrichment | vari | M |
| R4.6 | [Cx] Cambio progetto nella stessa route: polling, salvataggio e related layer di A agiscono su B. Manca anche `key={projectId}` ✦ | useExecution.js:24, useCanvasStore.js:65, EstraiEditor.jsx:202 | A |

**Caricamento ed errori**

| id | voce | file | gravità |
|---|---|---|---|
| R4.7 | [Cl] Ogni reload sostituisce tutta la pagina con lo spinner: la sidebar si rimonta, lo scroll e l'ancora si perdono, i contatori vanno a 0 | ProjectList, ConnectionManager, FileManager, ScheduleList, TicketDetail, UserManager | M |
| R4.8 | ✦ Errore mostrato come stato vuoto ("Nessun connettore", "Nessun progetto", "contatta un amministratore"); TicketDetail mostra una pagina bianca; i filtri dei ticket spariscono | vari, useEditorData.js:148,233 | M |
| R4.9 | [Cx] ProjectList: un errore negli stati di esecuzione diventa "Mai eseguito"; il filtro di stato è incoerente con la card [Cl] | useProjectListData.js:114 | M |
| R4.10 | ✦ `useInlinePreview` usa `humanizeError` senza importarlo → spinner infinito *verif.* | hooks/useInlinePreview.js:77 | A |
| R4.11 | ✦ Il modale di esecuzione di Estrai non riceve `onCancel` né il progresso reale; polling senza `onError` né timeout → modale bloccato | EstraiEditor.jsx:707, useExecution.js:168 | A |
| R4.12 | ✦ Polling infinito su 404 o 5xx; le anteprime non trattano `revoked`; `cancelledRef` di usePreview mai impostato | useTaskPolling.js:88, usePreview.js:56 | M |
| R4.13 | [Cl] LogViewer: "Live" riporta a pagina 1 ogni 5 s; la riga espansa è indicizzata per posizione ✦; i log tecnici vengono chiesti anche ai non-admin ✦; il dettaglio della run non si aggiorna [Cx] | LogViewer.jsx:105-286 | M |
| R4.14 | [Cl] Polling admin anche con la tab nascosta; "Aggiorna" in race con il poll ✦; TaskRuntime cancella l'errore dell'azione ✦ | AdminSystemStatus, AdminTaskRuntime | M |
| R4.15 | ✦ AdminBackups: "Caricamento..." infinito in caso di errore; `setTimeout` non annullato e con stato stantio; backup non seguito fino alla fine [Cx] | AdminBackupsPage.jsx:100-278 | M |
| R4.16 | [Cl] Schedule: il tempo trascorso parte da quando la pagina vede la run (dopo F5 mostra 0s); `queued` mostrato come running [Cx]; le nuove run automatiche non vengono rilevate [Cx]; tick ogni secondo su tutta la lista | ScheduleList.jsx:124-199 | M |
| R4.17 | [Cl] CARICA ArcGIS, schema e tabelle del Carica DB, auto-mapping: errori solo in console, nessuno spinner | CaricanArcGISNode.jsx:174, useCaricaSchemaSelect.js | M |
| R4.18 | [Cl] useEditorData: caricamenti in serie (4 fetch) anche se indipendenti | useEditorData.js:203 | M |
| R4.19 | [Cx] Il caricamento dei connection set si perde per sempre dopo una modifica ai nodi (flag "caricato" impostato prima della risposta) | EstraiEditor.jsx:98 | M |
| R4.20 | ✦ Registry delle trasformazioni in errore → "Caricamento..." permanente | TransformNode.jsx:23 | M |

**Test di enumerazione:** nessuna pagina in `pages/` usa `useEffect` più fetch senza passare da `useResource` (test di architettura); ogni `<ListState>` ha i 4 stati.

---

## R5 — Toast, sessione ed errori: un solo contratto

| id | voce | file | gravità |
|---|---|---|---|
| R5.1 | ✦ Il valore del ToastContext dipende da `toasts` → `useCallback([toast])` cambia a ogni toast → loop errore→toast→fetch; dopo un successo il modale aperto viene smontato *verif.* | context/ToastContext.jsx:80 | **A** |
| R5.2 | ✦ Un 401 tardivo con token vecchio fa logout della sessione nuova; `onUnauthorized` invocato N volte | api/http.js:125 | M |
| R5.3 | [Cl] Ruolo mai riletto (`getMe` inutilizzato): un admin declassato vede le pagine admin per 8 h | context/AuthContext.jsx:75 | M |
| R5.4 | [Cx] La cache dei connection set sopravvive al cambio di account | ProjectInputNode.jsx:33 | M |
| R5.5 | [Cl] Il logout non resetta `useCanvasStore`; `reset()` non azzera `lastTaskResult` | store | B |
| R5.6 | [Cl] Violazioni delle Rules of Hooks: `AllowedGroupsSelector` (crash "Rendered more hooks" quando lo stesso montaggio passa da senza permesso a con permesso; al logout invece no, corretto in fase di correzione), `DynamicConfig` (crash con op sconosciuta) | shared/AllowedGroupsSelector.jsx:41, transforms/DynamicConfig.jsx:29 | A |
| R5.7 | [Cl] Un solo ErrorBoundary fuori dal Router; nessun `Sentry.captureException`; nessun handler `vite:preloadError` (chunk mancante dopo il deploy); testo "URBY-ETL" | App.jsx:154, ErrorBoundary.jsx | M |
| R5.8 | [Cl] Normalizzazione degli errori in 6 o più versioni; `err.message` grezzo nei toast ("Request failed with status code 422", "Network Error"); il 404 di AllowedGroupsSelector viene cercato con regex su un `message` già riscritto → mai riconosciuto *verif.* | http.js, errorMessages.js, useCanvasSave.js, AdminBackupsPage.jsx:36, … | M |
| R5.9 | [Cl] Il value di AuthContext non è memoizzato e riparsa sessionStorage a ogni render | AuthContext.jsx:32 | B |
| R5.10 | [Cl] `useTheme`: localStorage senza try/catch → crash con lo storage bloccato | hooks/useTheme.js:29 | B |

**Test:** l'identità di `useToast()` è stabile dopo `toast.success`; `no-undef` e `react-hooks/rules-of-hooks` nel gate di lint (avrebbero trovato R4.10 ed R5.6); nessun `err.message` passato direttamente ai toast.

---

## R6 — Stack di modali e wizard sicuri di default

| id | voce | file | gravità |
|---|---|---|---|
| R6.1 | [Cl] Ogni modale ascolta Esc su `document` senza stack → Esc sul ConfirmDialog chiude anche JoinModal; `body.overflow` ripristinato troppo presto; id `modal-title` duplicato | ModalWrapper.jsx, useDialogFocus.js:63 | A |
| R6.2 | ✦ Il Wizard permette di saltare in avanti senza validare (connettore senza parametri o senza file) | common/Wizard.jsx:43,106 | A |
| R6.3 | ✦ Il Wizard non riceve `completing`: doppio click su Salva crea due connettori | ApiConnectionWizard.jsx:351,604 | A |
| R6.4 | [Cl] `closeOnOverlay` e `closeOnEsc` true per i wizard a 6 step: un click fuori fa perdere tutto | Wizard.jsx:49 | M |
| R6.5 | ✦ Il click sull'overlay chiude anche se il mousedown era dentro il modale | ModalWrapper.jsx:70 | M |
| R6.6 | [Cx] La X resta attiva durante il busy → la risposta vecchia chiude o resetta il form riaperto; UploadModal chiudibile durante l'upload [Cl] | ModalWrapper.jsx:103, UploadModal.jsx:175 | M |
| R6.7 | ✦ Scorciatoie del canvas (Canc, Ctrl+Z) attive con modale aperto o focus su select o button → cancella nodi dietro il modale | useCanvasKeyboard.js:57 | M |
| R6.8 | [Cl] Il focus iniziale va sempre sulla "×" e annulla gli `autoFocus` | useDialogFocus.js | B |
| R6.9 | [Cl] `useConfirmModal`: una seconda `confirm()` lascia la prima promise pending | useConfirmModal.js:26 | B |
| R6.10 | [Cl] Conferme incoerenti: `window.prompt` (TaskRuntime), `window.confirm`, dialog fatti a mano, TemplateLibraryModal senza focus né Esc | vari | M |

---

## R7 — Guard di navigazione unico

| id | voce | file | gravità |
|---|---|---|---|
| R7.1 | ✦ Modifiche non salvate perse uscendo da sidebar, CommandPalette, tasto Indietro del browser, logout, link `<a href>` e "Riprova" con reload | useUnsavedChangesGuard.js:24, CommandPalette.jsx:67, EditorSidebar.jsx:66 | A |
| R7.2 | ✦ Versioni del canvas: "Salva versione attuale" non salva prima; il ripristino fa `location.reload()` → se si annulla il prompt, il salvataggio successivo sovrascrive il ripristino | CanvasVersionsModal.jsx:50-72 | A |
| R7.3 | [Cx] 4 link "crea connessione" verso `/api-connections`, che non esiste *verif.* | ArricchisciEditor.jsx:625-628 | B |
| R7.4 | ✦ LogViewer apre sempre `/project/:id` (Estrai) anche per i progetti Manipola e Arricchisci | LogViewer.jsx:322 | M |
| R7.5 | [Cl] Dopo login o 401 non si torna alla rotta richiesta; catch-all senza pagina 404; `navigate(-1)` esce dall'app | App.jsx:135,146 | B |

**Correzione:** passare a un data router (`createBrowserRouter`) con `useBlocker`, tutte le uscite instradate da lì, e una costante `ROUTES` per i link.

---

## R8 — Form dichiarativi e input con buffer locale

**Correzione unica.**
- Input di lista e di chiave con testo grezzo locale e commit su blur o Invio.
- Tabelle dichiarative dei campi per tipo (Data Source, connessioni, input runtime, parametri delle trasformazioni) che definiscono campi ammessi, obbligatori, segreti, coercizione dei tipi e default scritto nella config.
- Generatore unico di chiavi (`uniqueKey(prefix, existing)`).

| id | voce | file | gravità |
|---|---|---|---|
| R8.1 | ✦ ProjectInputNode: le virgole spariscono nelle liste e `_` non si può digitare ("file_clienti" impossibile) | ProjectInputNode.jsx:24,230 | A |
| R8.2 | ✦ Connection Set: la key della riga include `selector_key` → focus perso a ogni carattere; `_` rimosso; chiave derivata dal solo primo carattere | ConnectionSetsPanel.jsx:227 | A |
| R8.3 | [Cl] Pivot: il nome di output prende solo il primo carattere ("canone" diventa "c") | PivotMappingBuilder.jsx:292 | A |
| R8.4 | [Cl] `parseInt(v)\|\|default`: svuotando il campo torna subito il default ("1005" invece di "5") | Step1BasicInfo, Step4Batch, ScheduleWizard, ConnectionManager, DbAdvancedOptions, RestDestinationSection | B |
| R8.5 | ✦ DataSourceManager: impossibile svuotare un campo; invia i campi degli altri tipi; nessuna validazione degli obbligatori | DataSourceManager.jsx:113 | M |
| R8.6 | ✦ DataSourceConfigModal: `upd() && upd('credentials',{})` → credenziali mai azzerate al cambio di tipo | DataSourceConfigModal.jsx:93 | M |
| R8.7 | ✦ ConnectionManager: cambiare categoria in modifica crea una nuova connessione | ConnectionManager.jsx:136,331 | M |
| R8.8 | ✦ Wizard API: `static_parameters` orfani dopo una rinomina | ApiConnectionWizard.jsx:459 | M |
| R8.9 | [Cx] Wizard API: il metodo batch di default non è quello mostrato; il default del template trasforma un parametro mappabile in costante | ApiConnectionWizard.jsx:105,422 | M |
| R8.10 | ✦ Input runtime degli eseguibili: `min`, `max` e `pattern` non verificati; numeri e booleani inviati come stringhe; analisi del file manuale | ExecutableDynamicRunForm.jsx:109,185 | M |
| R8.11 | ✦ Cataloghi dei formati di output diversi in 3 file | ExecutableDynamicRunForm, EseguibileExecuteWizard, PublishEseguibileModal | M |
| R8.12 | ✦ ScheduleWizard invia sempre `enabled: true` → la modifica riattiva uno schedule disattivato | ScheduleWizard.jsx:159 | **A** |
| R8.13 | [Cl] ScheduleWizard: se il caricamento in modifica fallisce, salvando si sovrascrive con i default; cron non validato; 3 soli fusi orari; 6 step obbligatori | ScheduleWizard.jsx:87-295 | M |
| R8.14 | [Cl] ParameterInput mostra il default senza scriverlo; select obbligatoria senza opzione vuota; `min`/`max` ignorati | ParameterInput.jsx:78 | M |
| R8.15 | ✦ RenameList: `stripLRSuffixes` crea duplicati rispetto alle colonne esistenti; righe con `new_name` vuoto | RenameListInput.jsx:27 | M |
| R8.16 | [Cl] ColumnOrderInput non segue le colonne a monte | ColumnOrderInput.jsx:116 | M |
| R8.17 | [Cx] JoinModal: le condizioni incomplete vengono scartate in silenzio; [Cl] preselezione prima colonna con prima colonna, filtri ON nascosti in modalità spaziale, edge duplicato scartato in silenzio | JoinModal.jsx:89-537 | M |
| R8.18 | [Cl] FilterModal: tipi non normalizzati; BETWEEN e IN impossibili sulle date | FilterModal.jsx:86 | M |
| R8.19 | [Cl] Chiavi `input_N` e `count_N` duplicate dopo una cancellazione (3 editor) | EstraiEditor.jsx:321, ManipolaEditor.jsx:443, ArricchisciEditor.jsx:423 | M |
| R8.20 | [Cx] UserManager: il gruppo è modificabile nel form ma scartato dal payload; [Cl] select del ruolo senza option per `admin` (mostra "Esecutore" e invia `admin`); badge `esecutore` mancante | UserManager.jsx:23-151 | M |
| R8.21 | ✦ Placeholder della password "Minimo 6" contro una regola di 12 | UserManager.jsx:206 | B |
| R8.22 | [Cx] Login: la richiesta MFA resta attiva dopo il cambio di username | LoginPage.jsx:131 | M |
| R8.23 | [Cl] SRID a testo libero → NaN; limite negativo ammesso; nome file non sanificato; StringList perde il testo non confermato; `between` a testo libero | FileManager.jsx:201, OutputSettingsPanel.jsx:68, … | B |

---

## R9 — Macchina a stati unica per le esecuzioni

**Correzione unica.**
- Un hook `useRunFlow` con `phase: config | running | success | failed | revoked | cancelled`, usato dai 3 editor e dal wizard e dal form degli eseguibili.
- Input runtime estratti da un helper unico.
- Gestione dell'errore di salvataggio pre-esecuzione.
- Flag busy per azione o per id.

| id | voce | file | gravità |
|---|---|---|---|
| R9.1 | ✦ Manipola e Arricchisci non passano `inputDefinitions`; `executeEnrichmentAsync` scarta `inputs` | ManipolaEditor.jsx:617, ArricchisciEditor.jsx:589, api/enrichment.js:83 | **A** |
| R9.2 | ✦ `permission_revoked` impostato come `taskResult` → "✅ Completata" con "Scarica" | EseguibileExecuteWizard.jsx:159,422 | A |
| R9.3 | [Cl] /run: `cleanupTaskCompletely` chiamato solo dopo il download → i file restano sul server, contro quanto promesso in pagina | EsecutoreHome.jsx:117, EseguibileExecuteWizard.jsx:184 | A |
| R9.4 | ✦ Il modale di Manipola non ha progresso né annullamento; l'annullamento è legato al tipo di progetto, non al `taskId` | ManipolaEditor.jsx:617, ExecutionModal.jsx:77 | M |
| R9.5 | [Cl] Arricchisci: 3 passaggi e 3 salvataggi per eseguire; il passo "Avvia" è superfluo anche in Manipola | ArricchisciEditor.jsx:312 | M |
| R9.6 | [Cl] Fallimento del salvataggio pre-esecuzione senza toast (promise non gestita) | ArricchisciEditor.jsx:326, ResultNode.jsx:200 | M |
| R9.7 | [Cl] Valida, Preview ed Esegui senza busy né protezione dal doppio click; Esegui salva due volte | ExecuteActionsPanel.jsx:342 | M |
| R9.8 | [Cl] Il modale di esecuzione chiuso non si può riaprire; un nuovo Esegui mentre il precedente fa polling; `onClose` riceve l'evento come `resetResult` | useExecution.js:255, EstraiEditor.jsx:708 | M |
| R9.9 | [Cl] EnrichmentConfirmModal non mostra `validation.errors`; senza `apiId` risulta valido per default | EnrichmentConfirmModal.jsx:34 | M |
| R9.10 | ✦ Busy a id singolo (`testingId`, `busyKey`, `resolving`): la prima operazione che termina riabilita la seconda | ConnectionManager.jsx:190, DataSourceManager.jsx:126, AdminTaskRuntimePage.jsx:50, ExecutableDynamicRunForm | M |
| R9.11 | [Cx] Clonazione progetto e rinomina eseguibile senza busy per id (doppia clonazione; il completamento chiude la rinomina sbagliata) | useProjectListData.js:161, EseguibiliList.jsx:109 | M |
| R9.12 | ✦ Toggle schedule: l'aggiornamento ottimistico scrive `s.enabled` mentre la UI legge `s.schedule.enabled` | ScheduleList.jsx:220 | A |
| R9.13 | [Cx] Rollback ottimistico che annulla una modifica successiva riuscita | useProjectListData.js:138 | M |
| R9.14 | ✦ Download senza catch: Schedule, ExecutionModal (410 "scaduto" non mostrato) | ScheduleList.jsx:434, ExecutionModal.jsx:465 | M |
| R9.15 | [Cl] Ripristina e Ripubblica una versione dell'eseguibile senza conferma | EseguibileVersionsModal.jsx:188 | M |
| R9.16 | [Cl] Quick test API cliccabile più volte, senza feedback ✦ | ApiConnectionManager.jsx:222 | M |

---

## R10 — Permessi solo tramite `can.*`, perimetri espliciti

| id | voce | file | gravità |
|---|---|---|---|
| R10.4 | ✦ "Bonifica orfani": la conferma conta sul filtro di gruppo, la richiesta è globale; orfano riconosciuto da `project_name === 'Progetto eliminato'` | AdminObjectStoragePage.jsx:50,169 | **A** |
| R10.6 | [Cx] AllowedGroupsSelector: se `getShares` fallisce, la selezione parte vuota ed è salvabile → sovrascrive le condivisioni *verif.* | shared/AllowedGroupsSelector.jsx:95,317 | **A** |
| R10.7 | [Cx] Il gruppo proprietario viene dedotto dall'utente invece che dalla risorsa (caso superadmin) | AllowedGroupsSelector.jsx:55 | M |
| R10.1 | [Cl] Azioni degli schedule (modifica, elimina, toggle, esegui) visibili ai viewer; delete del progetto visibile a viewer ed esecutori; `can.deleteFile`, `deleteOwnProject` e `createEseguibile` mai usati; controlli `user.role === 'admin'` grezzi nei ticket | ScheduleList.jsx:485-538, ProjectCard.jsx:156 | M |
| R10.2 | [Cl] EseguibileCard della dashboard mostra il delete senza `can_edit` | ProjectList | M |
| R10.3 | ✦ Eliminazione gruppo: il testo chiede il nome ma confronta l'id; il report del backend viene scartato (`deleteGroup` ignora il body); confermabile senza aver caricato l'impatto [Cx] | UserManager.jsx:312-456, api/users.js:25 | M |
| R10.5 | [Cl] "Autorizza retry" CARICA incerto con `window.prompt`, senza descrivere le conseguenze | AdminTaskRuntimePage.jsx:39 | M |
| R10.8 | [Cl] Header REST "Secret" salvati in chiaro in `node.data` e nelle versioni; `devtools` di zustand attivo in produzione | RestDestinationSection.jsx:201, useCanvasStore.js:31 | M |
| R10.9 | [Cl] Rimozione degli allegati dei ticket visibile a tutti | TicketDetail.jsx | B |

**Test di enumerazione:** ogni azione distruttiva o di scrittura in `pages/` e `components/` è legata a una capability `can.*` (test statico); `role ===` vietato fuori da `permissions`.

---

## R11 — Formatter unici

| id | voce | file | gravità |
|---|---|---|---|
| R11.1 | [Cl] I log mostrano l'ora UTC (ISO senza `Z` tagliato) come se fosse locale: 1-2 ore indietro; lo storico degli schedule interpreta `YYYYMMDD_HHMMSS` UTC come ora locale; le altre pagine usano `toLocale*` → la stessa run appare con orari diversi | utils/logViewerHelpers.jsx:36, RunDetail.jsx:101, ScheduleList.jsx:44 | A |
| R11.2 | [Cl] 5 formattatori di dimensione (nessuno gestisce i GB) e 6 di data | FileManager/helpers, ScheduleList, DataSourceManager, TicketDetail, … | B |
| R11.3 | [Cl] Enum grezzi mostrati (`in_progress`) nei chip dei ticket, nel dettaglio, ecc. | TicketsList.jsx:150 | B |
| R11.4 | [Cl] AdminBackups: `finished_at`, `next_run` e `last_run` non formattati | AdminBackupsPage.jsx | B |

---

## P — Difetti puntuali (fuori dalle regole)

| id | voce | file | gravità |
|---|---|---|---|
| P1 | ✦ **XSS**: il tooltip Leaflet viene costruito come HTML con chiavi e valori non sottoposti a escape *verif.* | components/modals/PreviewModal.jsx:318 | **A** |
| P2 | ✦ "Testa Connessione" del Data Source è finto (attende 1,5 s, poi "riuscita") *verif.* | components/arricchisci/DataSourceConfigModal.jsx:62 | **A** |
| P3 | [Cl] TransformNode: cliccare una tab di categoria cambia l'operazione e cancella la config ✦ | TransformNode.jsx:181 | A |
| P4 | ✦ FileManager multilayer: il selettore si apre dopo aver cancellato i layer; il file caricato non compare se si annulla; errori delle colonne solo in console | FileManager.jsx:262-355 | A |
| P5 | [Cl] PreviewModal: `data` ricreato a ogni render → pagina, ordinamento, ricerca e tab azzerati a ogni toast | EstraiEditor.jsx:726, PreviewModal.jsx:540 | A |
| P6 | ✦ Leaflet senza `map.remove()` né cleanup dei timer; `resetStyle` ripristina il colore iniziale; `getColor` O(n²) | PreviewModal.jsx:209-348 | M |
| P7 | [Cx] Il parser WKT e WKB corrompe MULTIPOINT e le varianti ISO Z/M | PreviewModal.jsx:43,124 | M |
| P8 | ✦ Gruppo di colonne collassato senza intestazione per riaprirlo | PreviewTable.jsx:121 | M |
| P9 | ✦ Clipboard senza guardia (HTTP in LAN → TypeError) e "Copiato" mostrato anche in caso di fallimento | QueryPreview.jsx:311 | M |
| P10 | ✦ Import OpenAPI: accetta YAML ma fa `JSON.parse`; nessun `$ref` nel body; colonne foglia duplicate; nessun autodetect dell'auth da file; Swagger 2.0 promesso ma non supportato; operazioni omesse [Cx]; posizione dell'API key ignorata [Cx]; checkbox degli endpoint inerte [Cx] | OpenAPIImportModal.jsx | M |
| P11 | ✦ Preview di response-config: senza iterazione restituisce null; path `[*]` e `iterate_on` incompatibili con il parser del backend; `onChange` al mount azzera `output_mapping`; materializza tutte le righe [Cx] | responseConfigEngine.js:150-184 | A |
| P12 | ✦ ConnectionSelectorModal va in crash con una connessione senza `auth` | ConnectionSelectorModal.jsx:233 | M |
| P13 | ✦ Il wizard API non azzera `testRawResponse` tra connettori diversi (mostra la raw di A dentro B) | ApiConnectionWizard.jsx:252 | M |
| P14 | ✦ Creazione di un DataSource da /external-connectors: errori solo in console e nessun toast | ApiConnectionManager.jsx:361 | M |
| P15 | [Cx] Quota dichiarata esaurita al 90% di utilizzo | ApiConnectionManager.jsx:48 | B |
| P16 | ✦ FileManager: "upload multiplo" promesso, usato solo `files[0]`; stato vuoto che ignora il tab di formato | FileManager.jsx:129,455 | M |
| P17 | [Cx] Dashboard: gli eseguibili spariscono quando non ci sono progetti | ProjectList.jsx:390,506 | M |
| P18 | [Cl] Dashboard: con un filtro attivo, lo stato vuoto dice "Crea il primo progetto"; il testo cita un pulsante 📢 che non esiste; toast doppio alla pubblicazione | ProjectList.jsx:405, PublishEseguibileModal.jsx:44 | B |
| P19 | [Cx] System Status: riepilogo OK anche con componenti mancanti | AdminSystemStatusPage.jsx:361 | M |
| P20 | [Cl] Tema scuro rotto in object-storage e task-runtime (variabili `--bg-primary` e `--surface` non definite); colori chiari hardcoded in PermissionRevokedBanner, UserManager e nel fallback di Suspense | styles/AdminObjectStoragePage.css, … | M |
| P21 | [Cl] LogViewer: "Export CSV" esporta solo la pagina corrente; RunsTab con tile "questa pagina" fuorvianti ✦ | LogViewer.jsx:131, RunsTab.jsx:77 | M |
| P22 | [Cl] Ticket: limite di 500 senza paginazione né avviso; deselezionando tutti gli stati compaiono anche i chiusi; [Cx] allegati aperti in popup bloccabile | TicketsList.jsx:85, tickets.js:130 | M |
| P23 | [Cl] Object Storage: un fetch per tasto senza debounce; il download carica tutto in un blob e chiama `revokeObjectURL` subito | AdminObjectStoragePage.jsx:54, api/adminObjectStorage.js:25 | M |
| P24 | [Cl] DataSourceNode: `DsSelector` e `BreadCrumb` definiti dentro il render (rimontati a ogni render) | DataSourceNode.jsx:172 | M |
| P25 | [Cx] Gli errori di eliminazione omettono il conteggio delle dipendenze nascoste | ProjectList.jsx:113 | B |
| P26 | [Cl] Hex canvas: il ResizeObserver non si collega mai (ref null al mount) | utils/hexCanvas.js:80 | B |
| P27 | [Cl] Anteprima inline senza indicatore "obsoleta"; AnnotationNode `autoFocus` al load | ResultInlinePreview.jsx, AnnotationNode.jsx:40 | B |
| P28 | [Cl] Ricerca incoerente tra le pagine (Invio contro digitazione in tempo reale; alcune pagine non l'hanno); badge della sidebar che cambiano da pagina a pagina; CommandPalette attiva su /login e /run | vari | B |
| P29 | [Cl] Sidebar di Estrai: il placeholder dice "tabelle e viste" ma filtra solo le tabelle; impossibile aggiungere due volte la stessa tabella (self-join); nessun Undo/Redo in toolbar | EstraiEditor.jsx:391,529 | B |
| P30 | [Cl] Toolbar diverse tra Manipola e Arricchisci (Carica ArcGIS, Nota, badge); nodi aggiunti in posizioni fisse fuori vista in Arricchisci | ArricchisciEditor.jsx:417 | B |
| P31 | [Cl] index.html: Google Fonts esterni (on-prem offline, GDPR), nessuna CSP; Sentry inizializzato in modo asincrono | index.html | B |

**Accessibilità** (✦ per la sidebar):
- `<div onClick>` al posto di link o button in: AppSidebar, card di EsecutoreHome [Cx], righe dei ticket [Cx], cartelle dei Data Source, ProjectCard, cambio tema nei 3 editor;
- label non associate in UserManager, form senza `<form>` (Invio non invia);
- campo MFA senza focus automatico;
- toggle della password con `tabIndex=-1`.

**Performance** (oltre a quelle già elencate):
- [Cl] selettori su tutto `nodes`/`edges` (RuntimeInputBindingControl ✦, CaricanNode, ProjectInputNode, FilterModal, CaricanArcGIS) → ri-render a ogni frame di trascinamento;
- QueryPreview con circa 40 regex per riga a ogni render;
- FilterModal e ColumnBuilderModal montati anche da chiusi;
- modali lazy sempre montati: i chunk e Leaflet vengono scaricati subito ✦;
- `useSmoothProgress` con setState a 60 fps;
- `applyFilters` doppio e `files.find` O(n·m) in ProjectList;
- `useValidationHighlights` ricrea tutti i nodi.

---

## D — Codice morto da rimuovere

Va rimosso, non corretto: gli elementi qui sotto contengono già bug latenti.

- **Percorso "CARICA manuale"** in tutti e 3 gli editor: `onCaricanExecute` è registrato ma nessun nodo lo chiama *verif.* Comprende `useCaricarica` (timeout fisso di 90 s, Annulla inerte, canvas non serializzato), `CaricaricaModal`, `caricaExecution` ed `executeCaricanTask` di Manipola.
- EstraiEditor: `handleFeatureSchemaLoaded`, `handleUpdateFeatureWhere`. FeatureServicePanel: `loadLayerSchema`, `onLayerReady`. CaricanArcGISNode: `data.executeResult`.
- ManipolaEditor: `onUpdateProjectSource*` e le callback `data.onChange` messe nei dati del nodo. ArricchisciEditor: `exportFormat`, il ramo `api_enrich` di `onDrop`, i fingerprint su tipi non registrati.
- `useSchemaInference.fetchNodeSchema`, che ombreggia l'import API [Cx]; `Step2Params.wizardStepError`; lo step "Conferma" di UploadModal mai raggiunto.
- UserManager: `ChangePasswordModal` e `showPwdModal` mai aperti. `api/config.js DEFAULT_HEADERS` (romperebbe FormData). `can.assignAdminRole`, `manageGroups` e `createEsecutore` mai usati.
- **Dipendenze e build:** `maplibre-gl` dichiarato ma mai importato; `manualChunks` per deck.gl e maplibre e commenti su `ImdfPreviewModal`, che non esistono più; il mock di login MSW con una shape diversa da AuthContext (solo dev); nome del pacchetto `canvas-query-builder-frontend`.

---

## Ordine di correzione proposto

1. **Subito, puntuali e senza rischio:** P1 (XSS), P2 (test finto), R4.10 (import), R5.1 (toast), R5.6 (rules of hooks), R8.12 (schedule `enabled`), R9.12 (toggle), R10.6 (condivisioni), R7.3 (route). Più il gate di lint `no-undef` e `rules-of-hooks`.
2. **R1 + R2:** sono quelli che fanno perdere dati sul canvas. Store con azioni esplicite e registro dei tipi di nodo, con test di round-trip.
3. **R4 + R5 + R6:** l'infrastruttura condivisa (risorse e task, contratto degli errori, stack dei modali). Le pagine di gestione si sistemano quasi da sole.
4. **R3 + R9:** il modulo delle colonne e il flusso di esecuzione unico, insieme alla deduplicazione Manipola/Arricchisci.
5. **R7, R8, R10, R11,** poi D (rimozioni) e le voci basse.

Al termine: **un solo giro di conferma** su questo elenco e sulle regressioni, come previsto dalla regola.

---

## U — Review dell'interfaccia dal vivo (172.16.28.6, utente superadmin, 03/11/2026)

Sola navigazione: nessun salvataggio, esecuzione o modifica.

### Conferme dal vivo di voci dell'audit
- **R1.8 confermato:** aprendo Manipola e muovendo solo la vista, all'uscita compare "modifiche non salvate". Estrai non ha il problema.
- **P29/B1 confermato:** sidebar di Estrai con "Mostra altri 15 di 1287 rimasti" e un secondo campo di ricerca solo per le viste.
- **R3.13 confermato:** la Query Preview dice "2 tabelle, 1 join" su un canvas con 1 tabella e l'Output.
- **P21/M29 confermato:** il LogViewer mostra "RUN TOTALI 280 — questa pagina" accanto a "COMPLETATE 30".
- **R11.3 confermato:** chip dei ticket con enum grezzi (`in_progress`, `waiting_user`).
- **R8.20/M13 confermato:** il ruolo `esecutore` compare senza badge in UserManager.
- **P20 confermato:** Object Storage e Code e Worker hanno uno stile diverso (bianco piatto, altra tipografia, barra di scroll orizzontale).
- **L12 confermato:** i badge della sidebar compaiono solo sulla pagina corrente (Connessioni 3 lì, altrove nulla).

### Manipola (pagina più critica)
| id | osservazione | proposta |
|---|---|---|
| U1 | Tutti i nodi si chiamano "Trasformazione"; si capisce cosa fanno solo leggendo il menu a tendina | Titolo modificabile più riassunto leggibile ("Filtra: ctr_period ∉ {UNIQUE, CUSTOM}") |
| U2 | Ogni nodo ripete tab Base/Spaziale/Privacy, descrizione **duplicata** (grigia e viola), "Input runtime…" su ogni parametro, "Cronologia run" | Nodo compatto; configurazione in un pannello laterale; una sola descrizione; input runtime dietro un'icona |
| U3 | Join → Rinomina da 77 righe (togli `_L`) → Elimina colonne → Rinomina da ~80 righe (etichette) → Output. Suffissi che si accumulano (`_L_L`) | Join con scelta delle colonne e gestione dei nomi uguali; griglia "Seleziona e rinomina" nell'Output (come in Estrai) |
| U4 | Due Filtra in fila sulla stessa colonna, due Formula in fila, Case When usato come tabella di corrispondenze | Filtro con più valori o condizioni, Formula con più colonne, suggerimento Lookup |
| U5 | Catene quasi identiche ricostruite a mano per ogni sorgente | "Duplica catena" / "applica a un'altra sorgente" |
| U6 | Collegamenti lunghi e incrociati; "Adatta vista" non mostra tutto; manca la minimappa (in Estrai c'è) | Ordinamento automatico (elkjs/dagre), minimappa |
| U7 | Il join indica le sorgenti come "SX: Formula / DX: Join" (tipo del nodo a monte) | Usare il titolo del passaggio a monte |
| U8 | Il nodo Output è ben fatto (compatto, formato a pulsanti, sezioni richiudibili) | Modello per tutti gli altri nodi |

### Estrai
- È l'editor più pulito: nodo tabella con checklist delle colonne, alias, badge WHERE, colonne calcolate; Output compatto. **Buon riferimento per Manipola.**
- Apertura: schermata bianca "Caricamento progetto…" per circa 5-7 s (catalogo di 1303 tabelle e 177 viste caricato in serie, vedi R4.18). Proposta: mostrare subito il canvas e caricare il catalogo della sidebar in parallelo.
- Toolbar: "Output" sbiadito senza spiegazione quando l'Output esiste già; il pulsante "Connessioni" in testata porta fuori dall'editor senza guard (R7.1).

### Dashboard
- **Il cestino è un'icona minuscola attaccata ad "Apri"** (in review ho aperto per sbaglio la conferma di eliminazione, poi annullata). Proposta: spostare "Elimina" in un menu "⋯", tenere a vista solo "Apri" (e magari "Duplica").
- 9 tile KPI, 5 delle quali a 0 (Arricchisci, Schedule, API, File...): poco informative. Proposta: tile solo per ciò che esiste, oppure cliccabili come filtro.
- Ogni card ripete "Nessuna descrizione", "+ tag", "Completato" e 3 icone senza etichetta: tanto rumore. Proposta: nascondere i campi vuoti; aggiungere una **vista a elenco/tabella** (14 card Estrai quasi identiche si scorrono meglio in tabella).
- Titoli lunghi troncati sotto il badge (`SC_A_patrimonio_unita_AMM ↓ ESTR`).
- Le voci di sidebar Estrai/Manipola/Arricchisci/Eseguibili scorrono solo a un'ancora della dashboard: il titolo resta "Dashboard".
- La sezione Arricchisci vuota occupa un grande riquadro bianco.

### Pagine di sistema
- **Eseguibili: pagina fuori dal design system.** È sempre in tema scuro anche con il tema chiaro attivo, la sidebar è rientrata con un margine, c'è una barra di scroll orizzontale, il link "← Dashboard" e un titolo con emoji. Va riallineata alle altre pagine.
- **Ticket:** intestazione diversa dalle altre pagine (titolo grande, nessun "Indietro"); il messaggio "Nessun ticket con questi filtri" sta **sopra** l'intestazione della tabella; enum grezzi.
- **Connessioni:** stato "Non testata" per tutte (l'esito del test non viene conservato); colonna AZIONI con l'intestazione a sinistra e i pulsanti a destra. Il tab Connection Set vuoto mostra solo un testo piccolo in alto a sinistra, mentre API Esterne, Data Source, File e Scheduler hanno uno stato vuoto centrato con il pulsante d'azione.
- **API Esterne e Scheduler:** con zero elementi mostrano comunque tile KPI a 0 e filtri.
- **Log Viewer:** data senza anno (`09-29 19:11:18`) e senza fuso (R11.1); colonna Progetto troncata (`SC_H_ticket_202607…`, che mostra l'ID con il timestamp invece del nome) mentre metà riga è vuota.
- **UserManager:** nessun titolo di pagina; ruolo `esecutore` senza badge.
- **Object Storage:** "Elimina tutte le preview" (azione distruttiva) è il primo pulsante in alto, in rosso, accanto ad "Aggiorna".
- **Ovunque:** "← Indietro" su pagine di primo livello (con la sidebar sempre presente non serve, e `navigate(-1)` può uscire dall'app).

### Linee guida di design emerse
1. **Un solo layout di pagina:** titolo e sottotitolo, azioni a destra, nessun "Indietro" sulle pagine di primo livello, tema rispettato ovunque.
2. **Un solo componente per lo stato vuoto,** centrato e con il pulsante d'azione; KPI e filtri nascosti quando non ci sono dati.
3. **Azioni distruttive mai a vista accanto a quelle primarie:** sempre in un menu "⋯" o separate, sempre con conferma.
4. **Nodi del canvas sul modello di Estrai/Output:** compatti, con titolo e riassunto, dettaglio nel pannello laterale.
5. **Formati uniformi:** data con anno e fuso, enum tradotti, nomi di progetto al posto degli ID.
