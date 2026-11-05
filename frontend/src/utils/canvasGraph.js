/**
 * canvasGraph — lettura del flusso di un canvas (Fase 4, M6), comune ai tre
 * editor: chi arriva all'Output, i rami, le corsie della vista Passi.
 *
 * Regole uniche:
 *  - RUOLI: una sola tabella dice cosa e' ogni nodo (nodeRole), anche in
 *    base ai dati (input di tipo file, Data Source in scrittura). Grafo,
 *    vista Passi e pannello leggono solo i ruoli.
 *  - FLUSSO: contano solo gli edge di dati tra nodi esistenti (i binding
 *    degli input runtime e gli edge verso nodi cancellati no).
 *  - CORSIE: ogni nodo del flusso sta in esattamente una corsia. Una corsia
 *    parte da una sorgente, da un'unione (piu' ingressi) o dopo un bivio;
 *    l'Output prosegue nei suoi caricamenti. Ordine: quello del flusso.
 *  - Un'unica analisi (analyzeCanvas) per tutti: EditorShell la calcola
 *    solo quando cambia la struttura (structureKey), non a ogni drag.
 */
import { dataFlowEdges } from './runtimeBindingEdges';
import { projectInputRole } from './runtimeInputKinds';

export const OUTPUT_TYPES = ['result', 'resultNode'];

/**
 * Ruolo di un nodo:
 *  - 'source'  porta dati nel flusso (sorgenti, tabelle, layer, input file);
 *  - 'step'    trasforma (ha ingressi e uscite);
 *  - 'output'  l'Output;
 *  - 'sink'    scrive dopo l'Output (Carica, Data Source in scrittura);
 *  - 'param'   parametri globali (Valori statici: valgono anche scollegati);
 *  - 'off'     fuori dal flusso (note, input runtime scalari).
 */
export function nodeRole(node) {
  const t = node?.type;
  const d = node?.data || {};
  if (t === 'comment') return 'off';
  if (t === 'project_input') return projectInputRole(node) === 'source' ? 'source' : 'off';
  if (OUTPUT_TYPES.includes(t)) return 'output';
  if (['carica', 'carica_arcgis', 'caricaArcgis'].includes(t)) return 'sink';
  if (t === 'datasourceNode' || t === 'datasource_node') return d.mode === 'carica' ? 'sink' : 'source';
  if (t === 'staticValues') return 'param';
  if (['project_source', 'projectSource', 'file_source', 'fileSource', 'tableNode', 'featureLayerNode'].includes(t)) return 'source';
  return 'step';
}

const inFlow = (n) => nodeRole(n) !== 'off';

/** Firma della STRUTTURA (nodi, ruoli, collegamenti): cambia poco, non a ogni drag. */
export function structureKey(nodes, edges) {
  const ns = (nodes || []).map(n => `${n.id}:${nodeRole(n)}`).join('|');
  const es = dataFlowEdges(edges || []).map(e => `${e.source}>${e.target}:${e.targetHandle || ''}`).join('|');
  return `${ns}#${es}`;
}

function index(nodes, edges) {
  const byId = new Map((nodes || []).filter(inFlow).map(n => [n.id, n]));
  const outs = new Map();
  const ins = new Map();
  // input-0 prima di input-1: il "primo ingresso" e' quello di sinistra.
  const flow = dataFlowEdges(edges || [])
    .filter(e => byId.has(e.source) && byId.has(e.target))
    .slice()
    .sort((a, b) => String(a.targetHandle || '').localeCompare(String(b.targetHandle || '')));
  // Un collegamento ripetuto tra gli stessi nodi (dati salvati con edge
  // doppi) e' un solo ingresso.
  const seenPairs = new Set();
  for (const e of flow) {
    const pair = `${e.source}>${e.target}`;
    if (seenPairs.has(pair)) continue;
    seenPairs.add(pair);
    if (!outs.has(e.source)) outs.set(e.source, []);
    outs.get(e.source).push(e.target);
    if (!ins.has(e.target)) ins.set(e.target, []);
    ins.get(e.target).push(e.source);
  }
  return { byId, outs, ins };
}

/** Ordine del flusso (sorgenti prima); i cicli in coda, nell'ordine dei nodi. */
function flowOrder({ byId, outs, ins }) {
  const indeg = new Map([...byId.keys()].map(id => [id, (ins.get(id) || []).length]));
  const queue = [...byId.keys()].filter(id => indeg.get(id) === 0);
  const order = [];
  while (queue.length) {
    const id = queue.shift();
    order.push(id);
    for (const next of outs.get(id) || []) {
      indeg.set(next, indeg.get(next) - 1);
      if (indeg.get(next) === 0) queue.push(next);
    }
  }
  for (const id of byId.keys()) if (!order.includes(id)) order.push(id);
  return new Map(order.map((id, i) => [id, i]));
}

/**
 * Analisi completa del canvas.
 * @returns {{
 *   reaching: Set<string>, dangling: Set<string>,
 *   lanes: Array<{ id, kind: 'source'|'join'|'fork', headId, stepIds: string[], lastId, addAfterId, dangling }>,
 *   laneOf: Map<string, object>, isEnd: (id) => boolean,
 * }}
 */
export function analyzeCanvas(nodes, edges) {
  const idx = index(nodes, edges);
  const { byId, outs, ins } = idx;
  const role = (id) => nodeRole(byId.get(id));

  // Chi arriva a un Output (i caricamenti vengono dopo: non contano).
  const reaching = new Set();
  const queue = [...byId.keys()].filter(id => role(id) === 'output');
  // Senza Output il canvas e' in costruzione: nessuno e' "scollegato".
  const hasOutput = queue.length > 0;
  queue.forEach(id => reaching.add(id));
  while (queue.length) {
    for (const prev of ins.get(queue.shift()) || []) {
      if (!reaching.has(prev)) { reaching.add(prev); queue.push(prev); }
    }
  }
  const dangling = new Set(hasOutput
    ? [...byId.keys()].filter(id => !reaching.has(id) && ['source', 'step'].includes(role(id)))
    : []);

  // Corsie: testa = nessun ingresso, piu' ingressi, o predecessore che si biforca.
  // Un ingresso da un parametro (Valori statici) non e' un altro ramo.
  const flowPreds = (id) => (ins.get(id) || []).filter(p => role(p) !== 'param');
  const isHead = (id) => {
    const pred = flowPreds(id);
    if (pred.length !== 1) return true;
    return (outs.get(pred[0]) || []).length > 1;
  };
  const order = flowOrder(idx);
  const lanes = [];
  const laneOf = new Map();
  const heads = [...byId.keys()].filter(isHead).sort((a, b) => order.get(a) - order.get(b));
  const placeLane = (headId, kind) => {
    const stepIds = [headId];
    let cur = headId;
    while (true) {
      // Un parametro (Valori statici) e' una corsia a se': non si prende il
      // ramo che alimenta, qualunque sia l'ordine dei nodi.
      if (role(cur) === 'param') break;
      const next = outs.get(cur) || [];
      if (next.length !== 1 || isHead(next[0]) || laneOf.has(next[0]) || stepIds.includes(next[0])) break;
      stepIds.push(next[0]);
      cur = next[0];
    }
    const steps = stepIds.map(id => byId.get(id));
    const lastId = stepIds[stepIds.length - 1];
    const addable = [...stepIds].reverse().find(id => !['output', 'sink'].includes(role(id)));
    const lane = { id: headId, kind, headId, stepIds, lastId, addAfterId: addable || null, dangling: dangling.has(lastId) };
    lanes.push(lane);
    stepIds.forEach(id => laneOf.set(id, lane));
    return steps;
  };
  for (const h of heads) {
    if (laneOf.has(h)) continue;
    const nIn = flowPreds(h).length;
    placeLane(h, nIn === 0 ? 'source' : nIn > 1 ? 'join' : 'fork');
  }
  // Cicli senza testa: ogni nodo rimasto apre la sua corsia.
  for (const id of [...byId.keys()].sort((a, b) => order.get(a) - order.get(b))) {
    if (!laneOf.has(id)) placeLane(id, 'fork');
  }

  const walk = (start, links) => {
    const seen = new Set();
    const q = [start];
    while (q.length) {
      for (const next of links.get(q.shift()) || []) if (!seen.has(next)) { seen.add(next); q.push(next); }
    }
    seen.delete(start);
    return seen;
  };
  // Estremita' di un ramo: nessuna uscita verso altri passi del flusso
  // (un'uscita diretta verso un caricamento non conta).
  const isEnd = (id) => byId.has(id) && !(outs.get(id) || []).some(t => ['source', 'step', 'output'].includes(role(t)));
  return {
    reaching, dangling, lanes, laneOf, isEnd,
    /** Nodi a monte (i loro dati arrivano qui): mai destinazioni di "Collega" (ciclo). */
    ancestorsOf: (id) => walk(id, ins),
    /** Estremita' scollegate raggiungibili da qui (anche oltre un bivio). */
    danglingEndsFrom: (id) => [...walk(id, outs), id].filter(x => isEnd(x) && dangling.has(x)),
  };
}

/** Id dei nodi che, seguendo il flusso, arrivano a un Output (Output compresi). */
export const reachingOutput = (nodes, edges) => analyzeCanvas(nodes, edges).reaching;

/** Id dei nodi i cui dati non arrivano a nessun Output (sorgenti e passi). */
export const danglingNodeIds = (nodes, edges) => analyzeCanvas(nodes, edges).dangling;

/** Corsie della vista Passi (con i nodi). */
export function stepLanes(nodes, edges) {
  const byId = new Map((nodes || []).map(n => [n.id, n]));
  return analyzeCanvas(nodes, edges).lanes.map(l => ({
    ...l, head: byId.get(l.headId), steps: l.stepIds.map(id => byId.get(id)), last: byId.get(l.lastId),
  }));
}

/**
 * Passi del ramo di una sorgente: quelli alimentati SOLO da lei (anche oltre
 * un bivio), fino a un'unione con altri rami, all'Output o ai caricamenti.
 * E' cio' che "Duplica ramo" copia.
 */
export function branchSteps(nodes, edges, sourceId) {
  const { byId, outs, ins } = index(nodes, edges);
  if (!byId.has(sourceId)) return [];
  const inside = new Set([sourceId]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const id of inside) {
      for (const next of outs.get(id) || []) {
        if (inside.has(next) || nodeRole(byId.get(next)) !== 'step') continue;
        // Ingressi da parametri (Valori statici) ammessi: non sono altri rami.
        if ((ins.get(next) || []).every(p => inside.has(p) || nodeRole(byId.get(p)) === 'param')) { inside.add(next); grew = true; }
      }
    }
  }
  const order = flowOrder({ byId, outs, ins });
  return [...inside].filter(id => id !== sourceId).sort((a, b) => order.get(a) - order.get(b)).map(id => byId.get(id));
}
