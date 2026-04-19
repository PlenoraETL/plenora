import { detectCycle, getNodeName } from './graphUtils';

/**
 * validateManipolaCanvas.js
 * 
 * Validazione canvas MANIPOLA — livello 3 (strutturale + parametri + semantico colonne).
 * 
 * Ogni issue: { severity: 'error'|'warning'|'info', message, nodeId?, nodeName? }
 * 
 * CHECKS:
 *  Strutturali:
 *   S1 - Nodo result presente (max 1)
 *   S2 - Nodo carica max 1
 *   S3 - Almeno una sorgente connessa al result
 *   S4 - Nodi trasformazione orfani (non nel percorso → result)
 *   S5 - Sorgenti isolate (nessun edge)
 *   S6 - Cicli nel grafo
 *
 *  Nodo result:
 *   R1 - Result connesso (almeno 1 edge in ingresso)
 *   R2 - Formato geo (GeoJSON/GPKG/SHP) ma nessuna colonna geometry visibile nel percorso
 *
 *  Nodo carica:
 *   C1 - Carica connesso al result
 *   C2 - DB: connection_id, schema_name, table_name presenti
 *   C3 - DB upsert/update: match_columns non vuoto
 *   C4 - ArcGIS: arcgis_connection_id presente
 *   C5 - ArcGIS add/update/delete: arcgis_layer_url presente
 *   C6 - ArcGIS create_service: arcgis_new_service_name presente
 *   C7 - ArcGIS update/delete: OBJECTID presente nelle colonne input
 *
 *  Nodi transform nel percorso:
 *   T1 - Parametri required compilati (dal registry)
 *   T2 - Parametri tipo 'column': la colonna referenziata esiste in data.inputColumns
 *   T3 - Parametri tipo 'columns': ogni colonna referenziata esiste in data.inputColumns
 *   T4 - Parametri tipo 'geometry_column': la colonna è effettivamente di tipo geometry
 *
 *  Sorgenti nel percorso:
 *   P1 - Almeno una colonna visibile
 */

// ============================================================
// HELPERS
// ============================================================

function getNodesConnectedToResult(resultId, edges) {
  const connected = new Set();
  const queue = [resultId];
  while (queue.length > 0) {
    const nodeId = queue.shift();
    for (const edge of edges) {
      if (edge.target === nodeId && !connected.has(edge.source)) {
        connected.add(edge.source);
        queue.push(edge.source);
      }
    }
  }
  return connected;
}

function columnExists(colName, inputColumns) {
  if (!colName || !inputColumns) return false;
  return inputColumns.some(c => (c.name || c) === colName);
}

function isGeometryColumn(colName, inputColumns) {
  if (!colName || !inputColumns) return false;
  const col = inputColumns.find(c => (c.name || c) === colName);
  if (!col) return false;
  return (col.type || '').startsWith('geometry');
}

function hasGeometryInPath(connectedIds, nodes) {
  for (const nodeId of connectedIds) {
    const node = nodes.find(n => n.id === nodeId);
    if (!node) continue;
    const cols = node.data?.columns || [];
    const hasGeom = cols.some(c => {
      if (c.visible === false) return false;
      const type = typeof c === 'string' ? '' : (c.type || '');
      return type.startsWith('geometry');
    });
    if (hasGeom) return true;
  }
  return false;
}

// ============================================================
// VALIDATORI PER CATEGORIA
// ============================================================

function validateStructure(nodes, edges) {
  const issues = [];

  const resultNodes = nodes.filter(n => n.type === 'result');
  if (resultNodes.length > 1) {
    issues.push({ severity: 'error', message: `Troppi nodi Output (${resultNodes.length}). Può esserci solo un nodo Output per canvas.` });
  }

  const caricaNodes = nodes.filter(n => n.type === 'carica');
  if (caricaNodes.length > 1) {
    issues.push({ severity: 'error', message: `Troppi nodi Carica (${caricaNodes.length}). Può esserci solo un nodo Carica per canvas.` });
  }

  if (detectCycle(nodes, edges)) {
    issues.push({ severity: 'error', message: 'Rilevato ciclo nel grafo. Rimuovi una connessione per risolvere.' });
  }

  return issues;
}

function validateResult(resultNode, edges, connectedToResult, nodes) {
  const issues = [];

  const incomingEdges = edges.filter(e => e.target === resultNode.id);
  if (incomingEdges.length === 0) {
    issues.push({
      severity: 'error',
      message: "Nodo Output non connesso. Collega almeno una trasformazione o sorgente all'output.",
      nodeId: resultNode.id, nodeName: 'Output'
    });
    return issues;
  }

  const sourceTypes = ['project_source', 'file_source', 'staticValues'];
  const sourcesInPath = nodes.filter(n => sourceTypes.includes(n.type) && connectedToResult.has(n.id));
  if (sourcesInPath.length === 0) {
    issues.push({
      severity: 'error',
      message: 'Nessuna sorgente dati collegata al percorso di output.',
      nodeId: resultNode.id, nodeName: 'Output'
    });
  }

  const format = resultNode.data?.format || 'csv';
  if (['geojson', 'gpkg', 'shp'].includes(format)) {
    if (!hasGeometryInPath(connectedToResult, nodes)) {
      issues.push({
        severity: 'error',
        message: `Formato "${format.toUpperCase()}" richiede una colonna geometry, ma nessuna è disponibile nel percorso (o è stata deselezionata).`,
        nodeId: resultNode.id, nodeName: 'Output'
      });
    }
  }

  return issues;
}

function validateCarica(caricaNode, edges, nodes) {
  const issues = [];
  const d = caricaNode.data || {};
  const nodeName = getNodeName(caricaNode);

  const resultNode = nodes.find(n => n.type === 'result');
  if (resultNode) {
    const caricaIncoming = edges.filter(e => e.target === caricaNode.id);
    const connectedToResult = caricaIncoming.some(e => e.source === resultNode.id);
    if (!connectedToResult) {
      issues.push({
        severity: 'error',
        message: 'Nodo Carica non collegato al nodo Output. Connetti Output → Carica.',
        nodeId: caricaNode.id, nodeName
      });
    }
  }

  if (!d.connection_id) issues.push({ severity: 'error', message: 'Carica DB: seleziona una connessione database.', nodeId: caricaNode.id, nodeName });
  if (!d.schema_name)   issues.push({ severity: 'error', message: 'Carica DB: seleziona uno schema di destinazione.', nodeId: caricaNode.id, nodeName });
  if (!d.table_name)    issues.push({ severity: 'error', message: 'Carica DB: specifica il nome della tabella di destinazione.', nodeId: caricaNode.id, nodeName });
  if (['upsert', 'update'].includes(d.write_mode || 'create')) {
    if (!(d.match_columns || []).length) {
      issues.push({
        severity: 'error',
        message: `Carica DB: modalità "${d.write_mode}" richiede almeno una colonna chiave in "Colonne di corrispondenza".`,
        nodeId: caricaNode.id, nodeName
      });
    }
  }

  return issues;
}

function validateCaricanArcGIS(node, edges, nodes) {
  const issues = [];
  const d = node.data || {};
  const nodeName = 'Carica ArcGIS';

  const resultNode = nodes.find(n => n.type === 'result');
  if (resultNode) {
    const incoming = edges.filter(e => e.target === node.id);
    if (!incoming.some(e => e.source === resultNode.id)) {
      issues.push({ severity: 'error', message: 'Nodo Carica ArcGIS non collegato al nodo Output.', nodeId: node.id, nodeName });
    }
  }

  if (!d.arcgis_connection_id) {
    issues.push({ severity: 'error', message: 'Carica ArcGIS: seleziona una connessione ArcGIS.', nodeId: node.id, nodeName });
  }

  const op = d.arcgis_operation || 'add';

  if (['add', 'update', 'upsert', 'delete'].includes(op) && !d.arcgis_layer_url) {
    issues.push({ severity: 'error', message: 'Carica ArcGIS: seleziona il layer di destinazione.', nodeId: node.id, nodeName });
  }

  if (op === 'create_service' && !d.arcgis_new_service_name) {
    issues.push({ severity: 'error', message: 'Carica ArcGIS: specifica il nome del nuovo Feature Service.', nodeId: node.id, nodeName });
  }

  if (['update', 'upsert', 'delete'].includes(op) && !d.arcgis_match_column) {
    issues.push({ severity: 'error', message: `Carica ArcGIS: seleziona la colonna chiave per l'operazione "${op}".`, nodeId: node.id, nodeName });
  }

  return issues;
}

function validateTransforms(transformNodes, registry) {
  const issues = [];
  if (!registry) return issues;

  for (const node of transformNodes) {
    const opId = node.data?.transform_op;
    if (!opId) {
      issues.push({ severity: 'error', message: 'Nodo trasformazione senza operazione selezionata.', nodeId: node.id, nodeName: 'Trasformazione' });
      continue;
    }
    const opDef = registry[opId];
    if (!opDef) continue;
    const config = node.data?.config || {};
    const inputColumns = node.data?.inputColumns || [];
    const nodeName = opDef.name || opId;

    for (const param of (opDef.parameters || [])) {
      const val = config[param.id];
      const isEmpty = val === undefined || val === null || val === '' || (Array.isArray(val) && val.length === 0);

      // T1 - Required mancante
      if (param.required && isEmpty) {
        issues.push({
          severity: 'error',
          message: `"${nodeName}": parametro obbligatorio "${param.label || param.id}" non configurato.`,
          nodeId: node.id, nodeName
        });
        continue;
      }
      if (isEmpty || !inputColumns.length) continue;

      // T2 - Colonna singola
      if (param.type === 'column' && val) {
        if (!columnExists(val, inputColumns)) {
          issues.push({ severity: 'warning', message: `"${nodeName}": la colonna "${val}" non è disponibile nell'input del nodo.`, nodeId: node.id, nodeName });
        }
      }

      // T3 - Lista colonne
      if ((param.type === 'columns' || param.type === 'string_list') && Array.isArray(val)) {
        for (const colName of val) {
          if (colName && !columnExists(colName, inputColumns)) {
            issues.push({ severity: 'warning', message: `"${nodeName}": la colonna "${colName}" non è disponibile nell'input del nodo.`, nodeId: node.id, nodeName });
          }
        }
      }

      // T4 - Geometry column
      if (param.type === 'geometry_column' && val) {
        if (!columnExists(val, inputColumns)) {
          issues.push({ severity: 'warning', message: `"${nodeName}": la colonna geometry "${val}" non è disponibile nell'input del nodo.`, nodeId: node.id, nodeName });
        } else if (!isGeometryColumn(val, inputColumns)) {
          issues.push({ severity: 'error', message: `"${nodeName}": la colonna "${val}" non è di tipo geometry.`, nodeId: node.id, nodeName });
        }
      }
    }
  }

  return issues;
}

function validateSources(connectedSources) {
  const issues = [];
  for (const node of connectedSources) {
    const cols = node.data?.columns || [];
    if (cols.length > 0) {
      const visibleCols = cols.filter(c => typeof c === 'string' || c.visible !== false);
      if (visibleCols.length === 0) {
        issues.push({
          severity: 'error',
          message: 'Tutte le colonne sono state deselezionate. Seleziona almeno una colonna.',
          nodeId: node.id, nodeName: getNodeName(node)
        });
      }
    }
  }
  return issues;
}

function validateOrphans(nodes, edges, connectedToResult, registry = null) {
  const issues = [];
  const sourceTypes = ['project_source', 'file_source', 'staticValues'];
  const transformTypes = ['transform', 'multi_join'];
  const allEdgeSources = new Set(edges.map(e => e.source));
  const allEdgeTargets = new Set(edges.map(e => e.target));

  for (const node of nodes.filter(n => transformTypes.includes(n.type))) {
    if (!connectedToResult.has(node.id)) {
      issues.push({
        severity: 'warning',
        message: "Nodo trasformazione non collegato al percorso di output — verrà ignorato durante l'esecuzione.",
        nodeId: node.id, nodeName: getNodeName(node, registry)
      });
    }
  }

  for (const node of nodes.filter(n => sourceTypes.includes(n.type))) {
    if (!allEdgeSources.has(node.id) && !allEdgeTargets.has(node.id)) {
      issues.push({
        severity: 'warning',
        message: "Sorgente non collegata ad alcun nodo — verrà ignorata durante l'esecuzione.",
        nodeId: node.id, nodeName: getNodeName(node, registry)
      });
    }
  }

  return issues;
}

// ============================================================
// FUNZIONE PRINCIPALE
// ============================================================

/**
 * Valida il canvas MANIPOLA — livello 3.
 * 
 * @param {Array} nodes - Nodi del canvas
 * @param {Array} edges - Edges del canvas
 * @param {Object|null} registry - Oggetto transforms dal registry JSON (opzionale)
 * @returns {{ isValid: boolean, issues: Array }}
 */
export function validateManipolaCanvas(nodes, edges, registry = null) {
  const issues = [];

  if (!nodes || nodes.length === 0) {
    return { isValid: false, issues: [{ severity: 'error', message: 'Canvas vuoto. Aggiungi almeno una sorgente e un nodo Output.' }] };
  }

  // Strutturali
  issues.push(...validateStructure(nodes, edges));

  // Result
  const resultNode = nodes.find(n => n.type === 'result');
  if (!resultNode) {
    issues.push({ severity: 'error', message: 'Nodo Output mancante. Aggiungi un nodo "Output Manipola".' });
    return { isValid: false, issues };
  }

  const connectedToResult = getNodesConnectedToResult(resultNode.id, edges);

  issues.push(...validateResult(resultNode, edges, connectedToResult, nodes));

  // Carica
  const caricaNode = nodes.find(n => n.type === 'carica');
  if (caricaNode) {
    issues.push(...validateCarica(caricaNode, edges, nodes));
  }

  const caricaArcgisNode = nodes.find(n => n.type === 'caricaArcgis');
  if (caricaArcgisNode) {
    issues.push(...validateCaricanArcGIS(caricaArcgisNode, edges, nodes));
  }

  // Trasformazioni nel percorso
  const connectedTransforms = nodes.filter(n =>
    ['transform', 'multi_join'].includes(n.type) && connectedToResult.has(n.id)
  );
  issues.push(...validateTransforms(connectedTransforms, registry));

  // Sorgenti nel percorso
  const connectedSources = nodes.filter(n =>
    ['project_source', 'file_source', 'staticValues'].includes(n.type) && connectedToResult.has(n.id)
  );
  issues.push(...validateSources(connectedSources));

  // Orfani e isolati
  issues.push(...validateOrphans(nodes, edges, connectedToResult, registry));

  return {
    isValid: !issues.some(i => i.severity === 'error'),
    issues
  };
}
