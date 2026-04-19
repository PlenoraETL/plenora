/**
 * validateArricchisciCanvas.js — Validazione canvas ARRICCHISCI
 *
 * Controlla:
 *  - Presenza di almeno un nodo sorgente (project_source, file_source, staticValues)
 *  - Presenza del nodo result
 *  - Ogni nodo api_enrich ha una connessione API configurata (apiId)
 *  - Tutti i nodi sono connessi (nessun nodo isolato)
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
  // Mappa nodeId → Set di colonne disponibili IN INGRESSO a quel nodo
  const available = {};
  nodes.forEach(n => { available[n.id] = new Set(); });

  // Colonne prodotte da ogni nodo (sorgenti + output enricher)
  function getProducedColumns(node) {
    const cols = new Set();
    // Sorgenti: usano schema_columns o columns dal data
    if (['project_source', 'file_source', 'staticValues'].includes(node.type)) {
      (node.data?.columns || node.data?.schema_columns || []).forEach(c => {
        const name = typeof c === 'string' ? c : (c.name || c.column);
        if (name) cols.add(name);
      });
    }
    // Enricher: produce le colonne aggiunte dall'API.
    // Fonte primaria: data.columns con fromApi:true (scritte da ApiEnrichNode via useEffect).
    // Fallback: output_mapping (sempre presente, non dipende dal render).
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

  // Ordine topologico semplice: BFS dai nodi senza ingresso
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

    // Le colonne prodotte da questo nodo vanno ai successori
    const produced = getProducedColumns(node);
    const outEdges = edges.filter(e => e.source === id);
    outEdges.forEach(e => {
      if (available[e.target]) {
        // Il successore eredita tutto ciò che era disponibile qui + le colonne prodotte
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
 * @param {Set|null} availableConnectionIds - Set degli ID connessioni esistenti (opzionale)
 * @returns {{ isValid: boolean, issues: Array<{severity: string, message: string}> }}
 */
export function validateArricchisciCanvas(nodes, edges, availableConnectionIds = null) {
  const issues = [];

  // Canvas vuoto
  if (!nodes || nodes.length === 0) {
    return { isValid: false, issues: [{ severity: 'error', message: 'Canvas vuoto. Aggiungi almeno una sorgente dati e un nodo Output.' }] };
  }

  // Nodo result obbligatorio
  const resultNode = nodes.find(n => n.type === 'result');
  if (!resultNode) {
    issues.push({ severity: 'error', message: 'Nodo Output mancante. Aggiungi un nodo "Output Arricchisci".' });
  }

  // Almeno una sorgente — obbligatoria solo se qualche enricher ha parametri mappabili non coperti
  const sourceTypes = ['project_source', 'file_source', 'staticValues'];
  const hasSources = nodes.some(n => sourceTypes.includes(n.type));
  const enrichNodes = nodes.filter(n => n.type === 'api_enrich');
  const anyEnricherNeedsSource = enrichNodes.some(nodeNeedsSource);
  if (!hasSources && anyEnricherNeedsSource) {
    issues.push({ severity: 'error', message: 'Nessuna sorgente dati. Aggiungi un nodo "Progetto", "File" o "Valori Statici".' });
  }

  // Calcola colonne disponibili in ingresso a ogni nodo (B-FE-69)
  const availableColumnsMap = buildAvailableColumnsMap(nodes, edges);

  for (const node of enrichNodes) {
    const label = node.data?.label || node.data?.api_name || node.id;
    const apiId = node.data?.api_id;
    if (!apiId) {
      issues.push({ severity: 'error', message: `Nodo "${label}": nessuna API connessa. Seleziona una connessione API.`, nodeId: node.id, nodeName: label });
    } else if (availableConnectionIds && !availableConnectionIds.has(apiId)) {
      issues.push({ severity: 'error', message: `Nodo "${label}": la connessione API è stata eliminata. Seleziona una nuova connessione.`, nodeId: node.id, nodeName: label });
    }

    // Controlla mapping solo per i parametri che non sono coperti da static_parameters
    if (nodeNeedsSource(node)) {
      const paramMapping = node.data?.param_mapping || {};
      const staticColumns = new Set(node.data?.static_columns || []);
      const staticParameters = node.data?.static_parameters || {};
      const parameters = node.data?.parameters || [];
      // B-FE-69: considera anche le colonne prodotte dai nodi upstream
      const upstreamColumns = availableColumnsMap[node.id] || new Set();
      const missingRequired = parameters
        .filter(p => p.required && p.mode !== 'fixed' && !staticColumns.has(p.name))
        .filter(p => {
          // Coperto da static_parameters → ok
          if (p.name in staticParameters) return false;
          const mappedCol = paramMapping[p.name];
          // Nessun mapping impostato → mancante
          if (!mappedCol) return true;
          // Mapping impostato: se abbiamo info upstream, verifica che la colonna esista
          // Se non abbiamo info upstream (upstreamColumns vuoto) → fidiamoci del mapping utente
          if (upstreamColumns.size === 0) return false;
          // Template syntax: "{X},{Y}" / "address: {via}" — supportata da backend _build_params.
          // Estrai tutti i placeholder {...} e verifica che ogni colonna referenziata esista upstream.
          const templateRefs = String(mappedCol).match(/\{([^}]+)\}/g);
          if (templateRefs) {
            return templateRefs.some(m => !upstreamColumns.has(m.slice(1, -1).trim()));
          }
          // Il dropdown memorizza il valore con il suffisso tipo es. "country (Testo)" → strip prima del confronto
          const mappedColName = mappedCol.replace(/\s*\(.*\)$/, '').trim();
          return !upstreamColumns.has(mappedColName);
        })
        .map(p => p.name);
      if (missingRequired.length > 0) {
        issues.push({
          severity: 'error',
          message: `Nodo "${label}": parametri obbligatori senza mapping — ${missingRequired.join(', ')}.`,
          nodeId: node.id,
          nodeName: label,
        });
      }
    }
  }

  // Nodi isolati — controlla sorgenti, enricher E result
  const connectedIds = new Set(edges.flatMap(e => [e.source, e.target]));
  const nodesToCheck = nodes.filter(n => sourceTypes.includes(n.type) || n.type === 'api_enrich' || n.type === 'result');
  for (const node of nodesToCheck) {
    if (!connectedIds.has(node.id)) {
      const label = node.data?.label || node.data?.api_name || node.type;
      const severity = node.type === 'result' || node.type === 'api_enrich' ? 'error' : 'warning';
      issues.push({ severity, message: `Nodo "${label}" non collegato ad altri nodi.`, nodeId: node.id, nodeName: label });
    }
  }

  return {
    isValid: !issues.some(i => i.severity === 'error'),
    issues
  };
}
