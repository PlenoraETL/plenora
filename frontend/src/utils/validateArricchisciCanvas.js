import {
  RULE_IDS,
  SEVERITY,
  isCanvasEmpty,
  createIssue,
  buildResult,
} from './canvasValidationContract';

/**
 * validateArricchisciCanvas.js — Validazione canvas ARRICCHISCI.
 *
 * Ritorna un `CanvasValidationResult` (vedi `canvasValidationContract.js`).
 * Ogni issue ha un `id` stabile (`RULE_IDS.*`).
 *
 * Controlla:
 *  - Canvas vuoto (CANVAS_EMPTY)
 *  - Nodo result presente (RESULT_NODE_MISSING)
 *  - Almeno una sorgente se qualche enricher la richiede (ARRICCHISCI_NO_SOURCES)
 *  - Ogni nodo api_enrich ha api_id (ARRICCHISCI_API_NOT_SELECTED)
 *  - Connessione API non eliminata (ARRICCHISCI_API_CONNECTION_STALE)
 *  - Mapping parametri richiesti (ARRICCHISCI_PARAM_MAPPING_MISSING)
 *  - Nessun nodo isolato (ARRICCHISCI_NODE_ISOLATED)
 */

/**
 * Ritorna true se il nodo ha parametri required che NON sono coperti da static_parameters.
 * Se tutti i required sono in static_parameters (o non ci sono required), il nodo
 * è autonomo e non ha bisogno di una sorgente dati.
 */
function nodeNeedsSource(node) {
  const params = node.data?.parameters || [];
  const staticParameters = node.data?.static_parameters || {};
  return params.some(p => p.required && p.mode !== 'fixed' && !(p.name in staticParameters));
}

/**
 * Calcola le colonne disponibili per ogni nodo percorrendo il grafo in ordine topologico.
 * Le sorgenti contribuiscono le loro colonne; ogni enricher aggiunge le colonne
 * prodotte dal suo output_mapping ai nodi successivi.
 */
function buildAvailableColumnsMap(nodes, edges) {
  const available = {};
  nodes.forEach(n => { available[n.id] = new Set(); });

  function getProducedColumns(node) {
    const cols = new Set();
    if (['project_source', 'file_source', 'staticValues'].includes(node.type)) {
      (node.data?.columns || node.data?.schema_columns || []).forEach(c => {
        const name = typeof c === 'string' ? c : (c.name || c.column);
        if (name) cols.add(name);
      });
    }
    if (node.type === 'api_enrich') {
      const fromApiCols = (node.data?.columns || []).filter(c => c.fromApi && c.name);
      if (fromApiCols.length > 0) {
        fromApiCols.forEach(c => cols.add(c.name));
      } else {
        (node.data?.output_mapping || []).forEach(m => {
          if (m.column) cols.add(m.column);
        });
      }
    }
    return cols;
  }

  const inDegree = {};
  nodes.forEach(n => { inDegree[n.id] = 0; });
  edges.forEach(e => { if (inDegree[e.target] !== undefined) inDegree[e.target]++; });

  const queue = nodes.filter(n => inDegree[n.id] === 0).map(n => n.id);
  const visited = new Set();

  while (queue.length > 0) {
    const id = queue.shift();
    if (visited.has(id)) continue;
    visited.add(id);
    const node = nodes.find(n => n.id === id);
    if (!node) continue;

    const produced = getProducedColumns(node);
    const outEdges = edges.filter(e => e.source === id);
    outEdges.forEach(e => {
      if (available[e.target]) {
        available[id].forEach(c => available[e.target].add(c));
        produced.forEach(c => available[e.target].add(c));
      }
      inDegree[e.target]--;
      if (inDegree[e.target] === 0) queue.push(e.target);
    });
  }

  return available;
}

/**
 * @param {Array} nodes
 * @param {Array} edges
 * @param {Set|null} availableConnectionIds — Set degli ID connessioni esistenti (opzionale)
 * @returns {import('./canvasValidationContract').CanvasValidationResult}
 */
export function validateArricchisciCanvas(nodes, edges, availableConnectionIds = null) {
  if (isCanvasEmpty(nodes)) {
    return buildResult([createIssue({
      id: RULE_IDS.CANVAS_EMPTY,
      severity: SEVERITY.ERROR,
      message: 'Canvas vuoto. Aggiungi almeno una sorgente dati e un nodo Output.',
    })]);
  }

  const issues = [];

  const resultNode = nodes.find(n => n.type === 'result');
  if (!resultNode) {
    issues.push(createIssue({
      id: RULE_IDS.RESULT_NODE_MISSING,
      severity: SEVERITY.ERROR,
      message: 'Nodo Output mancante. Aggiungi un nodo "Output Arricchisci".',
    }));
  }

  const sourceTypes = ['project_source', 'file_source', 'staticValues'];
  const hasSources = nodes.some(n => sourceTypes.includes(n.type));
  const enrichNodes = nodes.filter(n => n.type === 'api_enrich');
  const anyEnricherNeedsSource = enrichNodes.some(nodeNeedsSource);
  if (!hasSources && anyEnricherNeedsSource) {
    issues.push(createIssue({
      id: RULE_IDS.ARRICCHISCI_NO_SOURCES,
      severity: SEVERITY.ERROR,
      message: 'Nessuna sorgente dati. Aggiungi un nodo "Progetto", "File" o "Valori Statici".',
    }));
  }

  const availableColumnsMap = buildAvailableColumnsMap(nodes, edges);

  for (const node of enrichNodes) {
    const label = node.data?.label || node.data?.api_name || node.id;
    const apiId = node.data?.api_id;
    if (!apiId) {
      issues.push(createIssue({
        id: RULE_IDS.ARRICCHISCI_API_NOT_SELECTED,
        severity: SEVERITY.ERROR,
        message: `Nodo "${label}": nessuna API connessa. Seleziona una connessione API.`,
        nodeId: node.id,
        nodeName: label,
      }));
    } else if (availableConnectionIds && !availableConnectionIds.has(apiId)) {
      issues.push(createIssue({
        id: RULE_IDS.ARRICCHISCI_API_CONNECTION_STALE,
        severity: SEVERITY.ERROR,
        message: `Nodo "${label}": la connessione API è stata eliminata. Seleziona una nuova connessione.`,
        nodeId: node.id,
        nodeName: label,
      }));
    }

    if (nodeNeedsSource(node)) {
      const paramMapping = node.data?.param_mapping || {};
      const staticColumns = new Set(node.data?.static_columns || []);
      const staticParameters = node.data?.static_parameters || {};
      const parameters = node.data?.parameters || [];
      const upstreamColumns = availableColumnsMap[node.id] || new Set();
      const missingRequired = parameters
        .filter(p => p.required && p.mode !== 'fixed' && !staticColumns.has(p.name))
        .filter(p => {
          if (p.name in staticParameters) return false;
          const mappedCol = paramMapping[p.name];
          if (!mappedCol) return true;
          if (upstreamColumns.size === 0) return false;
          const templateRefs = String(mappedCol).match(/\{([^}]+)\}/g);
          if (templateRefs) {
            return templateRefs.some(m => !upstreamColumns.has(m.slice(1, -1).trim()));
          }
          const mappedColName = mappedCol.replace(/\s*\(.*\)$/, '').trim();
          return !upstreamColumns.has(mappedColName);
        })
        .map(p => p.name);
      if (missingRequired.length > 0) {
        issues.push(createIssue({
          id: RULE_IDS.ARRICCHISCI_PARAM_MAPPING_MISSING,
          severity: SEVERITY.ERROR,
          message: `Nodo "${label}": parametri obbligatori senza mapping — ${missingRequired.join(', ')}.`,
          nodeId: node.id,
          nodeName: label,
        }));
      }
    }
  }

  const connectedIds = new Set(edges.flatMap(e => [e.source, e.target]));
  const nodesToCheck = nodes.filter(n => sourceTypes.includes(n.type) || n.type === 'api_enrich' || n.type === 'result');
  for (const node of nodesToCheck) {
    if (!connectedIds.has(node.id)) {
      const label = node.data?.label || node.data?.api_name || node.type;
      const severity = node.type === 'result' || node.type === 'api_enrich' ? SEVERITY.ERROR : SEVERITY.WARNING;
      issues.push(createIssue({
        id: RULE_IDS.ARRICCHISCI_NODE_ISOLATED,
        severity,
        message: `Nodo "${label}" non collegato ad altri nodi.`,
        nodeId: node.id,
        nodeName: label,
      }));
    }
  }

  return buildResult(issues);
}
