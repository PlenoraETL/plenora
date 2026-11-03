/**
 * useCanvasStore.js — Store Zustand per il canvas plenora-etl
 *
 * Centralizza lo stato nodes/edges e tutte le azioni comuni ai tre editor.
 * I componenti nodo leggono direttamente dallo store invece di ricevere
 * callback come props, eliminando il prop drilling e i re-render inutili.
 *
 * Subscriptions selettive:
 *   const deleteNode = useCanvasStore(s => s.deleteNode);   // non re-renderizza
 *   const nodes      = useCanvasStore(s => s.nodes);        // re-renderizza solo se nodes cambia
 *
 * Devtools:
 *   Apri Redux DevTools nel browser per ispezionare ogni action in tempo reale.
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { applyNodeChanges, applyEdgeChanges } from 'reactflow';
import { updateCanvas } from '../api';
import { serializeCanvas } from '../utils/canvasSerializer';

// Undo/redo: profondità massima dello stack (oltre, si scarta il più vecchio).
const HISTORY_LIMIT = 50;
// Coalesce leading-edge: edit rapidi entro questa finestra (es. digitazione in
// un campo del nodo) producono UNA sola snapshot (pre-burst), non una per
// keystroke. lastSnapshotTs è module-level: lo store è singleton.
const SNAPSHOT_COALESCE_MS = 350;
let lastSnapshotTs = 0;

const useCanvasStore = create(
  devtools(
    (set, get) => {

      // Helper privato: user-edit marca dirty E svuota gli highlight
      // di validazione post-validation (amber borders). Evita churn
      // su subscriber se gli highlight sono gia' vuoti.
      const dirtyAndClearHighlights = () => {
        const { validationHighlights, lastValidationSummary } = get();
        const hasHighlights = validationHighlights.length > 0 || lastValidationSummary;
        return hasHighlights
          ? { isDirty: true, validationHighlights: [], lastValidationSummary: null }
          : { isDirty: true };
      };

      // Un solo writer per il canvas. Qualsiasi chiamante (editor, nodo,
      // pre-flight execute) passa da qui: se arriva una modifica durante un
      // PUT, il loop salva lo snapshot piu' recente prima di dichiarare clean.
      let saveInFlight = null;
      let saveRequested = false;
      const saveCanvasSerialized = async () => {
        saveRequested = true;
        if (saveInFlight) return saveInFlight;

        saveInFlight = (async () => {
          while (saveRequested) {
            saveRequested = false;
            const snapshot = get();
            // Si salva per modifiche utente (isDirty) E per correzioni
            // automatiche da persistere (pendingSync).
            if (snapshot.isReadOnly || (!snapshot.isDirty && !snapshot.pendingSync) || !snapshot.projectId) continue;

            const payload = serializeCanvas(snapshot.nodes, snapshot.edges, {
              mode: snapshot.mode,
            });
            await updateCanvas(snapshot.projectId, payload);

            const current = get();
            const sameSnapshot = current.projectId === snapshot.projectId
              && current.nodes === snapshot.nodes
              && current.edges === snapshot.edges;
            if (sameSnapshot) {
              set({ isDirty: false, pendingSync: false }, false, 'handleSaveQuiet:saved');
            } else if ((current.isDirty || current.pendingSync) && !current.isReadOnly) {
              // Un edit e' arrivato mentre il PUT era in volo.
              saveRequested = true;
            }
          }
        })();

        try {
          return await saveInFlight;
        } finally {
          saveInFlight = null;
          // Copre anche una richiesta arrivata nella micro-finestra tra la
          // risoluzione del runner e questo finally.
          if (saveRequested) await saveCanvasSerialized();
        }
      };

      return {

      nodes:     [],
      edges:     [],
      projectId: null,
      mode:      'manipola',   // 'estrai' | 'manipola' | 'arricchisci'

      // Undo/redo (#24): stack di snapshot {nodes, edges}. takeSnapshot()
      // registra lo stato PRE-mutazione (chiamato dalle azioni discrete +
      // drag-start/connect/delete); dedup per reference (gli array sono
      // immutabili → due snapshot consecutivi senza modifiche sono === e
      // vengono saltati). Cap a HISTORY_LIMIT per non crescere illimitato.
      past:   [],
      future: [],

      /**
       * Ultimo task result completato per questo canvas. Aggiornato da
       * useExecution quando il task termina. I nodi del canvas possono
       * leggerlo via `useCanvasStore(s => s.lastTaskResult)` per mostrare
       * info post-esecuzione (es. il ResultNode in modalita' IMDF legge
       * `lastTaskResult.imdf` per popolare i modali manifest/validation).
       */
      lastTaskResult: null,

      /**
       * Modalità sola lettura. Quando true:
       *  - tutte le azioni di mutazione UTENTE del canvas (addNodes, addEdges,
       *    removeEdges, editNodes, editEdges, updateEdge, patchNodeConfig,
       *    onNodesChange/onEdgesChange "rilevanti", updateNodeData, deleteNode,
       *    toggleColumn, toggleAllColumns, addCalculatedColumn,
       *    removeCalculatedColumn, updateColumnAlias, handleSaveQuiet)
       *    diventano no-op.
       *  - i nodi possono leggere questo flag via useCanvasStore(s => s.isReadOnly)
       *    per nascondere pulsanti X / disabilitare input.
       *
       * Settato da useCanvasCore in base a `!can.editCanvas(user)`
       * (vedi utils/permissions.js per la policy table).
       */
      isReadOnly: false,

      /**
       * Flag dirty: true se l'utente ha modificato qualcosa dopo l'ultimo
       * save/load. Usato da `handleSaveQuiet` per evitare PUT canvas inutili
       * quando l'utente clicca "Esegui" senza aver modificato nulla.
       *
       * - Settato a true da: azioni utente (updateNodeData, deleteNode,
       *   toggleColumn, addCalculatedColumn, removeCalculatedColumn,
       *   updateColumnAlias) e da onNodesChange/onEdgesChange ma SOLO per
       *   changes "rilevanti" (position/add/remove/resize esplicito, NON
       *   select ne' dimensioni misurate automaticamente).
       * - Settato a false da: markClean() (chiamato dopo load) e
       *   handleSaveQuiet dopo un save riuscito.
       *
       * I dati DERIVATI (applyDerivedNodes/Edges: colonne propagate, schemi,
       * highlight) e il load (loadGraph) non settano mai dirty: sono
       * deterministici dal canvas caricato e vengono riderivati al load.
       */
      isDirty: false,

      /**
       * Correzioni AUTOMATICHE da salvare (opzione {derived:true} di
       * updateNodeData/patchNodeConfig): es. formato riportato a CSV senza
       * geometria, colonna geometrica auto-selezionata, espressione
       * riscritta dopo una rinomina a monte. Sono dati persistiti, quindi
       * handleSaveQuiet li invia (Esegui/Valida/autosave), ma NON sono
       * modifiche dell'utente: niente avviso "modifiche non salvate" ne'
       * voce di undo. Distinto da isDirty (revisione Fase 3).
       */
      pendingSync: false,

      /**
       * ID dei nodi con issue di validazione. Popolato da ResultNode dopo
       * ogni `runValidation()` (manuale, execute, preview). Consumato dagli
       * editor (Manipola/Arricchisci) per applicare un outline amber sui
       * nodi problematici. Si svuota:
       *  - al prossimo validation pass riuscito (ResultNode);
       *  - automaticamente su user edit (via `dirtyAndClearHighlights`).
       */
      validationHighlights: [],

      /**
       * Sommario dell'ultima validazione. Popolato da ResultNode dopo ogni
       * `runValidation()`. Consumato dal `ValidationStatusBadge` nella toolbar.
       * Shape: `{ errorCount, warningCount, infoCount } | null`.
       * Si svuota su user edit (via `dirtyAndClearHighlights`) e `reset()`.
       */
      lastValidationSummary: null,

      /**
       * Lista connessioni DB disponibili nell'editor corrente.
       * Popolato via setConnections() — evita di passarla come prop
       * a ogni CaricanNode causando re-render inutili.
       */
      connections: [],

      /**
       * Ref mutabile non-reattiva per le azioni dell'editor corrente.
       * Usiamo un oggetto stabile (ref) invece di stato Zustand per evitare
       * che setEditorActions() causi re-render sui nodi subscriber.
       *
       * I nodi leggono le azioni via: useCanvasStore.getState().editorActionsRef.current.onPreview()
       * oppure tramite il getter getEditorAction(name).
       */
      editorActionsRef: { current: {} },

      init: (projectId, mode) => set({ projectId, mode }, false, 'init'),

      /** Imposta la modalità sola lettura. Chiamata da useCanvasCore in base al ruolo. */
      setReadOnly: (value) => set({ isReadOnly: !!value }, false, 'setReadOnly'),

      /** Reset completo — chiamato al mount di ogni editor */
      reset: () => {
        get().editorActionsRef.current = {};
        // NOTA: non resettiamo isReadOnly perché viene impostato subito dopo da
        // useCanvasCore (dipende dal ruolo utente, non dal progetto).
        set({ nodes: [], edges: [], projectId: null, mode: null, connections: [], isDirty: false, pendingSync: false, validationHighlights: [], lastValidationSummary: null, past: [], future: [], lastTaskResult: null }, false, 'reset');
      },

      // ── Undo/Redo (#24) ───────────────────────────────────────────────
      /** Registra lo stato corrente nello stack `past` (da chiamare PRIMA di
       *  mutare). Dedup per reference: se lo stato non è cambiato dall'ultimo
       *  snapshot, no-op. Svuota `future` (nuovo ramo). No-op in sola lettura. */
      takeSnapshot: () => {
        const { nodes, edges, past, isReadOnly } = get();
        if (isReadOnly) return;
        const top = past[past.length - 1];
        // dedup per reference: nessuna modifica dall'ultima snapshot.
        if (top && top.nodes === nodes && top.edges === edges) return;
        // coalesce: burst di edit rapidi (digitazione) → una sola snapshot.
        const now = Date.now();
        if (now - lastSnapshotTs < SNAPSHOT_COALESCE_MS) return;
        lastSnapshotTs = now;
        set({ past: [...past, { nodes, edges }].slice(-HISTORY_LIMIT), future: [] }, false, 'takeSnapshot');
      },

      /** Annulla l'ultima modifica strutturale del canvas. */
      undo: () => {
        const { past, isReadOnly } = get();
        if (isReadOnly || past.length === 0) return;
        lastSnapshotTs = 0;  // il prossimo edit dopo un undo deve poter snapshottare subito
        set(
          state => {
            const previous = state.past[state.past.length - 1];
            return {
              nodes: previous.nodes,
              edges: previous.edges,
              past: state.past.slice(0, -1),
              future: [{ nodes: state.nodes, edges: state.edges }, ...state.future].slice(0, HISTORY_LIMIT),
              ...dirtyAndClearHighlights(),
            };
          },
          false,
          'undo',
        );
      },

      /** Ripristina la modifica annullata. */
      redo: () => {
        const { future, isReadOnly } = get();
        if (isReadOnly || future.length === 0) return;
        lastSnapshotTs = 0;
        set(
          state => {
            const next = state.future[0];
            return {
              nodes: next.nodes,
              edges: next.edges,
              past: [...state.past, { nodes: state.nodes, edges: state.edges }].slice(-HISTORY_LIMIT),
              future: state.future.slice(1),
              ...dirtyAndClearHighlights(),
            };
          },
          false,
          'redo',
        );
      },

      /** Imposta le connessioni DB disponibili nell'editor. */
      setConnections: (connections) =>
        set({ connections }, false, 'setConnections'),

      /** Aggiorna la lista di nodi con issue post-validazione. */
      setValidationHighlights: (ids) =>
        set({ validationHighlights: ids }, false, 'setValidationHighlights'),

      /** Aggiorna il sommario dell'ultima validazione (badge toolbar). */
      setLastValidationSummary: (summary) =>
        set({ lastValidationSummary: summary }, false, 'setLastValidationSummary'),

      /**
       * Registra le azioni dell'editor (preview, execute...).
       * Muta la ref direttamente — NESSUN set() Zustand, nessun re-render.
       * Chiama questo ogni volta che le funzioni cambiano senza preoccuparti
       * di stabilità: i nodi leggono sempre l'ultima versione via getState().
       */
      setEditorActions: (actions) => {
        const ref = get().editorActionsRef;
        ref.current = { ...ref.current, ...actions };
      },

      /** Shortcut per leggere un'azione specifica (utile nei nodi) */
      getEditorAction: (name) => get().editorActionsRef.current[name],

      // Gestiscono drag, resize, selezione — delegano ad applyNodeChanges/applyEdgeChanges
      onNodesChange: (changes) => {
        // In sola lettura accettiamo solo i changes innocui (select) per
        // permettere all'utente di selezionare i nodi e ispezionarli.
        if (get().isReadOnly) {
          const safeChanges = changes.filter(c => c.type === 'select');
          if (safeChanges.length === 0) return;
          set(
            state => ({ nodes: applyNodeChanges(safeChanges, state.nodes) }),
            false,
            'onNodesChange:readonly'
          );
          return;
        }
        // Marca dirty solo per changes "rilevanti" (escludi select, che è
        // solo highlight UI e non modifica i dati salvabili).
        const relevant = changes.some(c =>
          c.type === 'position'
          || c.type === 'add'
          || c.type === 'remove'
          // React Flow emette `dimensions` anche per misure automatiche del DOM
          // (es. preview inline post-run). Solo NodeResizer imposta updateStyle,
          // cioe' una dimensione scelta dall'utente e quindi salvabile.
          || (c.type === 'dimensions' && c.updateStyle === true)
        );
        set(
          state => ({
            nodes: applyNodeChanges(changes, state.nodes),
            ...(relevant ? dirtyAndClearHighlights() : {})
          }),
          false,
          'onNodesChange'
        );
      },

      onEdgesChange: (changes) => {
        if (get().isReadOnly) {
          const safeChanges = changes.filter(c => c.type === 'select');
          if (safeChanges.length === 0) return;
          set(
            state => ({ edges: applyEdgeChanges(safeChanges, state.edges) }),
            false,
            'onEdgesChange:readonly'
          );
          return;
        }
        const relevant = changes.some(c =>
          c.type === 'add' || c.type === 'remove' || c.type === 'reset'
        );
        set(
          state => ({
            edges: applyEdgeChanges(changes, state.edges),
            ...(relevant ? dirtyAndClearHighlights() : {})
          }),
          false,
          'onEdgesChange'
        );
      },

      // ── Mutazioni del grafo: TRE canali espliciti (audit R1) ─────────────
      // Prima esistevano solo setNodes/setEdges e lo store indovinava se una
      // modifica era dell'utente guardando gli id (strutturale = dirty). La
      // regola sbagliava in entrambi i versi: modificare un join esistente
      // (stessi id) non marcava dirty -> il salvataggio veniva saltato; gli
      // effetti automatici all'apertura marcavano dirty. Ora ogni chiamante
      // dichiara cosa sta facendo:
      //
      //  1. LOAD     loadGraph            -> grafo dal backend: pulito, history vuota
      //  2. UTENTE   addNodes/addEdges/removeEdges/editNodes/editEdges
      //              (+ updateEdge, updateNodeData, patchNodeConfig, deleteNode…)
      //              -> snapshot undo + dirty; no-op in sola lettura
      //  3. DERIVATO applyDerivedNodes/applyDerivedEdges
      //              -> colonne propagate, schemi, highlight: MAI dirty ne'
      //                 snapshot; ammesso anche in sola lettura
      //
      // setNodes/setEdges non esistono piu': un test di enumerazione vieta di
      // reintrodurli (tests/shared/canvasMutationChannels.contract.test.js).

      /** Carica il grafo dal backend: stato pulito e cronologia azzerata. */
      loadGraph: (nodes, edges) => {
        lastSnapshotTs = 0;
        set(
          { nodes, edges, isDirty: false, pendingSync: false, past: [], future: [], validationHighlights: [], lastValidationSummary: null },
          false,
          'loadGraph',
        );
      },

      /** Mutazione utente generica dei nodi (updater funzionale). */
      editNodes: (updater) => {
        if (get().isReadOnly) return;
        get().takeSnapshot();
        set(
          state => ({ nodes: updater(state.nodes), ...dirtyAndClearHighlights() }),
          false,
          'editNodes',
        );
      },

      /** Mutazione utente generica degli edge (updater funzionale). */
      editEdges: (updater) => {
        if (get().isReadOnly) return;
        get().takeSnapshot();
        set(
          state => ({ edges: updater(state.edges), ...dirtyAndClearHighlights() }),
          false,
          'editEdges',
        );
      },

      /** Aggiunge uno o piu' nodi (azione utente). */
      addNodes: (nodeOrNodes) => {
        const added = Array.isArray(nodeOrNodes) ? nodeOrNodes : [nodeOrNodes];
        get().editNodes(nodes => nodes.concat(added));
      },

      /** Aggiunge uno o piu' edge (azione utente). */
      addEdges: (edgeOrEdges) => {
        const added = Array.isArray(edgeOrEdges) ? edgeOrEdges : [edgeOrEdges];
        get().editEdges(edges => edges.concat(added));
      },

      /** Rimuove edge per id (stringa o lista) o per predicato (azione utente). */
      removeEdges: (idsOrPredicate) => {
        const shouldRemove = typeof idsOrPredicate === 'function'
          ? idsOrPredicate
          : (edge => [].concat(idsOrPredicate).includes(edge.id));
        get().editEdges(edges => edges.filter(edge => !shouldRemove(edge)));
      },

      /** Aggiornamento DERIVATO dei nodi: mai dirty, mai snapshot. */
      applyDerivedNodes: (updater) =>
        set(state => {
          const next = updater(state.nodes);
          return next === state.nodes ? state : { nodes: next };
        }, false, 'applyDerivedNodes'),

      /** Aggiornamento DERIVATO degli edge: mai dirty, mai snapshot. */
      applyDerivedEdges: (updater) =>
        set(state => {
          const next = updater(state.edges);
          return next === state.edges ? state : { edges: next };
        }, false, 'applyDerivedEdges'),

      /** Aggiorna un edge esistente come mutazione utente salvabile. */
      updateEdge: (edgeId, updater) => {
        if (get().isReadOnly) return;
        get().takeSnapshot();
        set(
          state => ({
            edges: state.edges.map(edge => {
              if (edge.id !== edgeId) return edge;
              const changes = typeof updater === 'function'
                ? updater(edge)
                : updater;
              return { ...edge, ...(changes || {}) };
            }),
            ...dirtyAndClearHighlights(),
          }),
          false,
          'updateEdge',
        );
      },

      // NODE OPERATIONS
      // Usate direttamente dai componenti nodo via useCanvasStore(s => s.xxx)
      /** Aggiorna i data di un nodo. Sostituisce onUpdate/onChange nei props. */
      updateNodeData: (nodeId, updates, opts = {}) => {
        // opts.derived = true: dato calcolato (schema caricato, default
        // automatico, stato UI) -> niente dirty ne' snapshot (audit R1.8).
        const derived = opts.derived === true;
        if (!derived) {
          if (get().isReadOnly) return;
          get().takeSnapshot();
        }
        set(
          state => ({
            nodes: state.nodes.map(n =>
              n.id === nodeId ? { ...n, data: { ...n.data, ...updates } } : n
            ),
            ...(derived ? { pendingSync: true } : dirtyAndClearHighlights())
          }),
          false,
          derived ? 'updateNodeData:derived' : 'updateNodeData'
        );
      },

      /**
       * Aggiorna `data.config` di un nodo fondendo la patch con la config
       * CORRENTE dello store (non con quella del render): piu' patch nello
       * stesso tick non si sovrascrivono (audit R1.5). `patch` puo' essere un
       * oggetto o una funzione (config) => patch.
       *
       * opts.derived = true: patch automatica (es. auto-selezione di una
       * colonna di default) -> niente dirty ne' snapshot.
       */
      patchNodeConfig: (nodeId, patch, opts = {}) => {
        const derived = opts.derived === true;
        if (!derived) {
          if (get().isReadOnly) return;
          get().takeSnapshot();
        }
        set(
          state => ({
            nodes: state.nodes.map(n => {
              if (n.id !== nodeId) return n;
              const current = n.data?.config || {};
              const changes = typeof patch === 'function' ? patch(current) : patch;
              return { ...n, data: { ...n.data, config: { ...current, ...(changes || {}) } } };
            }),
            ...(derived ? { pendingSync: true } : dirtyAndClearHighlights()),
          }),
          false,
          derived ? 'patchNodeConfig:derived' : 'patchNodeConfig',
        );
      },

      /** Salva l'ultimo task result completato (chiamato da useExecution). */
      setLastTaskResult: (result) => set({ lastTaskResult: result || null }, false, 'setLastTaskResult'),

      /** Elimina nodo e tutti gli edges collegati. Sostituisce onDelete nei props. */
      deleteNode: (nodeId) => {
        if (get().isReadOnly) return;
        get().takeSnapshot();
        set(
          state => ({
            nodes: state.nodes.filter(n => n.id !== nodeId),
            edges: state.edges.filter(e => e.source !== nodeId && e.target !== nodeId),
            ...dirtyAndClearHighlights()
          }),
          false,
          'deleteNode'
        );
      },

      /** Toggle visibilità di una singola colonna. Sostituisce onToggleColumn. */
      toggleColumn: (nodeId, columnIndex) => {
        if (get().isReadOnly) return;
        get().takeSnapshot();
        set(
          state => ({
            nodes: state.nodes.map(n => {
              if (n.id !== nodeId) return n;
              const cols = [...n.data.columns];
              cols[columnIndex] = { ...cols[columnIndex], visible: !cols[columnIndex].visible };
              return { ...n, data: { ...n.data, columns: cols } };
            }),
            ...dirtyAndClearHighlights()
          }),
          false,
          'toggleColumn'
        );
      },

      /** Toggle visibilità di tutte le colonne. Sostituisce onToggleAllColumns. */
      toggleAllColumns: (nodeId, makeVisible) => {
        if (get().isReadOnly) return;
        get().takeSnapshot();
        set(
          state => ({
            nodes: state.nodes.map(n => {
              if (n.id !== nodeId) return n;
              return {
                ...n,
                data: {
                  ...n.data,
                  columns: n.data.columns.map(col => ({ ...col, visible: makeVisible }))
                }
              };
            }),
            ...dirtyAndClearHighlights()
          }),
          false,
          'toggleAllColumns'
        );
      },

      /** Aggiunge una colonna calcolata a un nodo. Sostituisce onAddCalculatedColumn. */
      addCalculatedColumn: (nodeId, columnDef) => {
        if (get().isReadOnly) return;
        get().takeSnapshot();
        set(
          state => ({
            nodes: state.nodes.map(n => {
              if (n.id !== nodeId) return n;
              return {
                ...n,
                data: {
                  ...n.data,
                  calculated_columns: [...(n.data.calculated_columns || []), columnDef]
                }
              };
            }),
            ...dirtyAndClearHighlights()
          }),
          false,
          'addCalculatedColumn'
        );
      },

      /** Rimuove una colonna calcolata da un nodo. Sostituisce onDeleteCalculatedColumn. */
      removeCalculatedColumn: (nodeId, columnIndex) => {
        if (get().isReadOnly) return;
        get().takeSnapshot();
        set(
          state => ({
            nodes: state.nodes.map(n => {
              if (n.id !== nodeId) return n;
              return {
                ...n,
                data: {
                  ...n.data,
                  calculated_columns: (n.data.calculated_columns || []).filter((_, i) => i !== columnIndex)
                }
              };
            }),
            ...dirtyAndClearHighlights()
          }),
          false,
          'removeCalculatedColumn'
        );
      },

      /** Aggiorna alias colonna. Sostituisce onUpdateColumnAlias. */
      updateColumnAlias: (nodeId, columnIndex, newAlias) => {
        if (get().isReadOnly) return;
        get().takeSnapshot();
        set(
          state => ({
            nodes: state.nodes.map(n => {
              if (n.id !== nodeId) return n;
              const cols = [...n.data.columns];
              cols[columnIndex] = { ...cols[columnIndex], alias: newAlias };
              return { ...n, data: { ...n.data, columns: cols } };
            }),
            ...dirtyAndClearHighlights()
          }),
          false,
          'updateColumnAlias'
        );
      },

      /**
       * Salvataggio silenzioso — serializza il canvas e lo invia al backend.
       * Usa il mode corrente per determinare il formato di serializzazione.
       *
       * Skip intelligente: se `isDirty` è false, l'utente non ha modificato
       * nulla dall'ultimo load/save → salta il PUT. Questo evita PUT canvas
       * inutili quando l'utente clicca "Esegui" senza aver modificato nulla
       * (l'esecuzione passa per questo come onBeforeExecute, ma il save
       * vuoto è solo overhead + causa 403 inutili in casi edge come un
       * user di gruppo che esegue un progetto creato dall'admin).
       *
       * NOTA: rilancia l'eccezione così chi la usa come onBeforeExecute
       * può bloccare l'esecuzione se il salvataggio fallisce.
       */
      handleSaveQuiet: saveCanvasSerialized,

      /**
       * Marca il canvas come "in sync con il backend" (isDirty = false).
       * Da chiamare DOPO che l'editor ha popolato lo store con i dati
       * caricati dal backend. In questo modo handleSaveQuiet non farà PUT
       * inutili finché l'utente non modifica davvero qualcosa.
       */
      markClean: () => set({ isDirty: false, pendingSync: false }, false, 'markClean'),

      /** Restituisce il nodo corrente per ID (lettura sincrona, non reattiva) */
      getNodeById: (nodeId) => get().nodes.find(n => n.id === nodeId),

      /** Snapshot nodes/edges correnti (utile per evitare stale closure) */
      getCanvas: () => ({ nodes: get().nodes, edges: get().edges }),

    };
    },
    { name: 'UrbyETL-Canvas' }
  )
);

export default useCanvasStore;
