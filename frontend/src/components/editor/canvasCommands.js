/**
 * canvasCommands — comandi del canvas comuni ai tre editor (UI-3, U27):
 * seleziona tutto, copia, incolla, duplica passo, cerca nel canvas,
 * collega da tastiera, guida dell'editor.
 *
 * Tabella UNICA (CANVAS_COMMANDS): scorciatoia (utils/shortcuts, la stessa
 * della guida dei tasti), nome, se modifica il canvas e
 * quando si puo' usare (`available` -> null o il motivo per cui no). La
 * leggono la tastiera (components/editor/CanvasOverlays), il menu "⋯" della
 * barra (stessi nomi, voci spente col motivo) e la guida dell'editor
 * (sezione «Scorciatoie»). Il test tests/shared/canvasCommands.contract.test.js
 * controlla che ogni comando abbia tasto, voce e azione.
 *
 * Le azioni lavorano sullo store e passano dai canali utente esistenti
 * (store.addSubgraph: un blocco, un solo Ctrl+Z, posizioni libere).
 */
import { create } from 'zustand';
import useCanvasStore from '../../store/useCanvasStore';
import { isParameterBindingEdge } from '../../utils/runtimeBindingEdges';
import { isSingleUseNode } from './editorNodeCatalog';
import { ICON } from '../../utils/icons';
import { projectType } from '../../utils/projectTypes';
import { tableNodeAlias } from '../../utils/estraiJoins';
import { newProjectInputData, uniqueName } from '../../utils/uniqueName';
import { registerSessionReset } from '../../utils/session';
import { matchesShortcut, shortcutParts } from '../../utils/shortcuts';

// ── Stato dell'interfaccia: finestra di ricerca/collegamento e guida ───────
export const useCanvasUi = create((set) => ({
  picker: null, // null | 'search' | 'connect'
  help: null, // null | { section: string|null }
  openPicker: (picker) => set({ picker }),
  closePicker: () => set({ picker: null }),
  openHelp: (section = null) => set({ help: { section } }),
  closeHelp: () => set({ help: null }),
}));

// Appunti del canvas: nodi e collegamenti copiati (in memoria, per scheda).
// Dati dell'utente: la fine sessione li svuota (utils/session).
// `mode`: l'editor da cui vengono (estrai, manipola, arricchisci): si
// incollano solo in un editor dello stesso tipo (B-K2), perche' i tipi di
// nodo di un editor non valgono negli altri (una tabella di Estrai in
// Manipola, un'operazione di Manipola in Arricchisci).
let clipboard = null;
const clipboardSize = () => clipboard?.nodes.length || 0;
registerSessionReset('canvasCommands', () => {
  clipboard = null;
  useCanvasUi.setState({ picker: null, help: null });
});

/** Motivo per cui gli appunti non si incollano qui, o null. */
function pasteReason(st) {
  if (!clipboardSize()) return 'Niente da incollare: copia prima dei nodi';
  if (clipboard.mode !== st.mode) {
    return `I nodi copiati vengono da un progetto ${projectType(clipboard.mode).label}: si incollano solo in un progetto dello stesso tipo`;
  }
  return null;
}

const selectedNodes = (st) => (st.nodes || []).filter(n => n.selected);
const selectedSteps = (st) => selectedNodes(st).filter(n => n.type !== 'comment');

// I nodi unici per progetto (Output e caricamenti di ogni destinazione) non
// si copiano: un secondo Output non e' un progetto valido. Stessa regola del
// catalogo (editorNodeCatalog.isSingleUseNode).
const copiable = (n) => !isSingleUseNode(n);

const clone = (value) => JSON.parse(JSON.stringify(value ?? null));

/*
 * IDENTITA' DEI NODI COPIATI (B-K2, B-K3): i campi che due nodi dello stesso
 * canvas non possono condividere, per tipo di nodo. Ogni copia (Incolla,
 * Duplica) passa da qui, nell'ordine, contro il canvas e le copie gia' fatte:
 *  - Input runtime: `input_key` (la chiave che l'esecuzione e i collegamenti
 *    leggono) libera e ordine dopo il piu' alto (utils/uniqueName);
 *  - tabella di Estrai: alias libero `tabella_N` se la tabella o il suo alias
 *    sono gia' nel canvas (utils/estraiJoins.tableNodeAlias, stessa forma del
 *    backend: core/estrai/query_aliases).
 * Il test tests/shared/editorCommands.contract.test.js copia ogni tipo di
 * nodo con un campo d'identita' e verifica che il canvas resti univoco.
 */
export const COPY_IDENTITY = Object.freeze({
  project_input: (data, canvas) => {
    const inputs = canvas.filter(n => n.type === 'project_input');
    const fresh = newProjectInputData(canvas, { kind: data.input_kind });
    const taken = inputs.map(n => n.data?.input_key);
    return { ...data, input_key: uniqueName(data.input_key || 'input', taken, { bareFirst: true }), order: fresh.order };
  },
  tableNode: (data, canvas) => ({ ...data, alias: tableNodeAlias(data, canvas.filter(n => n.type === 'tableNode')) }),
});

/**
 * Copie dei nodi `source` con id nuovi, spostate di `offset`, con i
 * collegamenti tra loro; `withInputs`: anche i collegamenti che entrano nei
 * nodi da fuori (Duplica: il passo copiato riceve gli stessi dati).
 * I collegamenti degli input runtime restano sullo stesso input.
 * `canvas`: i nodi gia' nel canvas, per i campi d'identita' (COPY_IDENTITY).
 */
function copiesOf(source, edges, { offset = 40, withInputs = false, canvas = [] } = {}) {
  const stamp = Date.now();
  const idMap = new Map(source.map((n, i) => [n.id, `${n.type}_${stamp}_${i}`]));
  const nodes = [];
  for (const n of source) {
    const identity = COPY_IDENTITY[n.type];
    const data = clone(n.data);
    nodes.push({
      id: idMap.get(n.id), type: n.type,
      position: { x: (n.position?.x || 0) + offset, y: (n.position?.y || 0) + offset },
      data: identity ? identity(data, [...canvas, ...nodes]) : data,
      ...(n.style ? { style: clone(n.style) } : {}),
      ...(n.zIndex !== undefined ? { zIndex: n.zIndex } : {}),
    });
  }
  const copied = edges
    .filter(e => idMap.has(e.target) && (idMap.has(e.source) || withInputs || isParameterBindingEdge(e)))
    .map((e, i) => ({
      ...clone(e),
      id: `${e.id}__copy_${stamp}_${i}`,
      source: idMap.get(e.source) || e.source,
      target: idMap.get(e.target),
      selected: false,
    }));
  return { nodes, edges: copied };
}

const readOnlyReason = (st) => (st.isReadOnly ? 'Progetto in sola lettura' : null);

export const CANVAS_COMMANDS = Object.freeze([
  {
    id: 'select_all', matches: (e) => matchesShortcut('select_all', e), name: 'Seleziona tutto', icon: ICON.select_all, edits: false,
    available: (st) => ((st.nodes || []).length ? null : 'Il canvas è vuoto'),
    run: (st) => {
      const changes = (st.nodes || []).filter(n => !n.selected).map(n => ({ id: n.id, type: 'select', selected: true }));
      if (changes.length) st.onNodesChange(changes);
      return `${st.nodes.length} nodi selezionati`;
    },
  },
  {
    id: 'copy', matches: (e) => matchesShortcut('copy', e), name: 'Copia', icon: ICON.copy, edits: false,
    available: (st) => (selectedNodes(st).some(copiable) ? null : 'Seleziona i nodi da copiare (Output e caricamenti esclusi)'),
    run: (st) => {
      const nodes = selectedNodes(st).filter(copiable);
      const ids = new Set(nodes.map(n => n.id));
      clipboard = { mode: st.mode, nodes: clone(nodes), edges: clone((st.edges || []).filter(e => ids.has(e.target) && (ids.has(e.source) || isParameterBindingEdge(e)))) };
      return `${nodes.length} ${nodes.length === 1 ? 'nodo copiato' : 'nodi copiati'}`;
    },
  },
  {
    id: 'paste', matches: (e) => matchesShortcut('paste', e), name: 'Incolla', icon: ICON.paste, edits: true,
    available: (st) => readOnlyReason(st) || pasteReason(st),
    run: (st) => {
      const block = copiesOf(clipboard.nodes, clipboard.edges, { offset: 60, canvas: st.nodes || [] });
      // Gli input runtime collegati che non sono piu' nel canvas: collegamento tolto.
      const alive = new Set([...(st.nodes || []).map(n => n.id), ...block.nodes.map(n => n.id)]);
      st.addSubgraph({ nodes: block.nodes, edges: block.edges.filter(e => alive.has(e.source)), selectIds: block.nodes.map(n => n.id) });
      return `${block.nodes.length} ${block.nodes.length === 1 ? 'nodo incollato' : 'nodi incollati'}`;
    },
  },
  {
    id: 'duplicate', matches: (e) => matchesShortcut('duplicate', e), name: 'Duplica passo', icon: ICON.duplicate, edits: true,
    available: (st) => readOnlyReason(st) || (selectedSteps(st).some(copiable) ? null : 'Seleziona il passo da duplicare (Output e caricamenti esclusi)'),
    run: (st) => {
      const source = selectedSteps(st).filter(copiable);
      const block = copiesOf(source, st.edges || [], { offset: 40, withInputs: true, canvas: st.nodes || [] });
      st.addSubgraph({ nodes: block.nodes, edges: block.edges, selectIds: block.nodes.map(n => n.id) });
      return source.length === 1 ? 'Passo duplicato con gli stessi ingressi' : `${source.length} passi duplicati`;
    },
  },
  {
    id: 'search', shortcut: 'search_canvas', matches: (e) => matchesShortcut('search_canvas', e), name: 'Cerca nel canvas', icon: ICON.search, edits: false,
    available: (st) => ((st.nodes || []).length ? null : 'Il canvas è vuoto'),
    run: () => { useCanvasUi.getState().openPicker('search'); return null; },
  },
  {
    id: 'connect', matches: (e) => matchesShortcut('connect', e), name: 'Collega a…', icon: ICON.connect, edits: true,
    available: (st) => readOnlyReason(st)
      || (selectedSteps(st).length === 1 ? null : 'Seleziona un solo nodo da cui collegare'),
    run: () => { useCanvasUi.getState().openPicker('connect'); return null; },
  },
  {
    id: 'help', shortcut: 'editor_guide', matches: (e) => matchesShortcut('editor_guide', e), name: 'Guida dell\'editor', icon: ICON.editor_guide, edits: false,
    available: () => null,
    run: (st) => {
      const sel = selectedSteps(st);
      useCanvasUi.getState().openHelp(sel.length === 1 ? sel[0].type : null);
      return null;
    },
  },
]);

export const commandById = (id) => CANVAS_COMMANDS.find(c => c.id === id);

/** Esegue un comando se disponibile: { ok, message }. */
export function runCanvasCommand(id) {
  const cmd = commandById(id);
  const st = useCanvasStore.getState();
  const reason = cmd.available(st);
  if (reason) return { ok: false, message: reason };
  return { ok: true, message: cmd.run(st) };
}

/** Tasto premuto -> id del comando (null se nessuno). */
export function commandForKey(event) {
  return CANVAS_COMMANDS.find(c => c.matches(event))?.id || null;
}

/** Tasti di un comando, dalla tabella unica delle scorciatoie (utils/shortcuts). */
export function commandKeys(cmd) {
  return shortcutParts(cmd.shortcut || cmd.id)[0];
}

/**
 * REGOLA dei tasti del canvas (B-K7): un tasto della tabella si prende
 * (preventDefault) SOLO se il comando parte davvero; altrimenti resta al
 * browser (Ctrl+F cerca nella pagina, Ctrl+D aggiunge ai segnalibri,
 * Ctrl+A/C/V restano quelli di sempre).
 *  - comando che modifica, in sola lettura: niente (nessun avviso: il
 *    comando non e' offerto);
 *  - comando non disponibile: il tasto resta al browser; l'avviso col motivo
 *    solo con il fuoco DENTRO il canvas (`onFlow`), mai dalla pagina (body);
 *  - disponibile: il comando parte.
 * @returns {{ take: boolean, warn: string|null }}
 */
export function canvasKeyDecision(id, st, { onFlow = false } = {}) {
  const cmd = commandById(id);
  if (!cmd || (cmd.edits && st.isReadOnly)) return { take: false, warn: null };
  const reason = cmd.available(st);
  if (reason) return { take: false, warn: onFlow ? reason : null };
  return { take: true, warn: null };
}

/**
 * Voci del menu "⋯" per i comandi (nome e tasto, spente col motivo).
 * `edits` false resta anche in sola lettura.
 */
export function commandMenuItems(st, onRun) {
  return CANVAS_COMMANDS
    .filter(c => c.id !== 'help' && (!st.isReadOnly || !c.edits))
    .map((c) => {
      const reason = c.available(st);
      return {
        key: `cmd-${c.id}`, icon: c.icon, label: `${c.name} (${commandKeys(c)})`,
        title: reason || c.name, disabled: Boolean(reason), reason, onClick: () => onRun(c.id),
      };
    });
}
