import { detectCycle, getNodeName } from './graphUtils';
import {
  RULE_IDS,
  SEVERITY,
  isCanvasEmpty,
  createIssue,
  buildResult,
} from './canvasValidationContract';

/**
 * validateManipolaCanvas.js
 *
 * Validazione canvas MANIPOLA. Ritorna un `CanvasValidationResult`
 * (vedi `canvasValidationContract.js`). Ogni issue ha un `id` stabile
 * (`RULE_IDS.*`).
 *
 * CHECKS:
 *  Strutturali:
 *   S1 - Canvas vuoto (CANVAS_EMPTY)
 *   S2 - Nodo result max 1 (RESULT_NODE_DUPLICATED)
 *   S3 - Nodo carica max 1 (CARICA_DUPLICATED)
 *   S4 - Cicli (CYCLE_DETECTED)
 *   S5 - Sorgenti isolate / transform orfani (SOURCE_ISOLATED, TRANSFORM_ORPHAN)
 *
 *  Result:
 *   R1 - Result connesso (RESULT_NOT_CONNECTED)
 *   R2 - Sorgente nel percorso (RESULT_NO_SOURCE)
 *   R3 - Formato geo senza geometry (RESULT_GEO_NO_GEOMETRY)
 *
 *  Carica:
 *   C1 - Connesso a result (CARICA_NOT_CONNECTED)
 *   C2-C5 - DB (CARICA_DB_*) e ArcGIS (CARICA_ARCGIS_*)
 *
 *  Transform (T):
 *   T1 - operazione mancante (TRANSFORM_OP_MISSING)
 *   T2 - param required mancante (TRANSFORM_PARAM_REQUIRED)
 *   T3 - colonna referenziata non esiste (TRANSFORM_COLUMN_NOT_FOUND)
 *   T4 - geometry column non e' geometry (TRANSFORM_GEOMETRY_TYPE_INVALID)
 *
 *  Source nel percorso:
 *   P1 - nessuna colonna visibile (SOURCE_NO_VISIBLE_COLUMNS)
 */

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

function validateStructure(nodes, edges) {
  const issues = [];

  const resultNodes = nodes.filter(n => n.type === 'result');
  if (resultNodes.length > 1) {
    issues.push(createIssue({
      id: RULE_IDS.RESULT_NODE_DUPLICATED,
      severity: SEVERITY.ERROR,
      message: `Troppi nodi Output (${resultNodes.length}). Può esserci solo un nodo Output per canvas.`,
    }));
  }

  const caricaNodes = nodes.filter(n => n.type === 'carica');
  if (caricaNodes.length > 1) {
    issues.push(createIssue({
      id: RULE_IDS.CARICA_DUPLICATED,
      severity: SEVERITY.ERROR,
      message: `Troppi nodi Carica (${caricaNodes.length}). Può esserci solo un nodo Carica per canvas.`,
    }));
  }

  if (detectCycle(nodes, edges)) {
    issues.push(createIssue({
      id: RULE_IDS.CYCLE_DETECTED,
      severity: SEVERITY.ERROR,
      message: 'Rilevato ciclo nel grafo. Rimuovi una connessione per risolvere.',
    }));
  }

  return issues;
}

function validateResult(resultNode, edges, connectedToResult, nodes) {
  const issues = [];

  const incomingEdges = edges.filter(e => e.target === resultNode.id);
  if (incomingEdges.length === 0) {
    issues.push(createIssue({
      id: RULE_IDS.RESULT_NOT_CONNECTED,
      severity: SEVERITY.ERROR,
      message: "Nodo Output non connesso. Collega almeno una trasformazione o sorgente all'output.",
      nodeId: resultNode.id,
      nodeName: 'Output',
    }));
    return issues;
  }

  const sourceTypes = ['project_source', 'file_source', 'staticValues', 'datasourceNode'];
  const sourcesInPath = nodes.filter(n => sourceTypes.includes(n.type) && connectedToResult.has(n.id));
  if (sourcesInPath.length === 0) {
    issues.push(createIssue({
      id: RULE_IDS.RESULT_NO_SOURCE,
      severity: SEVERITY.ERROR,
      message: 'Nessuna sorgente dati collegata al percorso di output.',
      nodeId: resultNode.id,
      nodeName: 'Output',
    }));
  }

  const format = resultNode.data?.format || 'csv';
  if (['geojson', 'gpkg', 'shp'].includes(format)) {
    if (!hasGeometryInPath(connectedToResult, nodes)) {
      issues.push(createIssue({
        id: RULE_IDS.RESULT_GEO_NO_GEOMETRY,
        severity: SEVERITY.ERROR,
        message: `Formato "${format.toUpperCase()}" richiede una colonna geometry, ma nessuna è disponibile nel percorso (o è stata deselezionata).`,
        nodeId: resultNode.id,
        nodeName: 'Output',
      }));
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
      issues.push(createIssue({
        id: RULE_IDS.CARICA_NOT_CONNECTED,
        severity: SEVERITY.ERROR,
        message: 'Nodo Carica non collegato al nodo Output. Connetti Output → Carica.',
        nodeId: caricaNode.id,
        nodeName,
      }));
    }
  }

  if (d.dest_type === 'rest') {
    if (!d.rest_url || !/^https?:\/\//i.test(d.rest_url)) {
      issues.push(createIssue({
        id: RULE_IDS.CARICA_REST_URL_MISSING,
        severity: SEVERITY.ERROR,
        message: 'Carica REST: specifica un endpoint HTTP/HTTPS valido.',
        nodeId: caricaNode.id,
        nodeName,
      }));
    }
    if (!['POST', 'PUT', 'PATCH'].includes(d.rest_method || 'POST')) {
      issues.push(createIssue({
        id: RULE_IDS.CARICA_REST_METHOD_INVALID,
        severity: SEVERITY.ERROR,
        message: 'Carica REST: metodo non supportato.',
        nodeId: caricaNode.id,
        nodeName,
      }));
    }
    return issues;
  }

  if (!d.connection_id) {
    issues.push(createIssue({
      id: RULE_IDS.CARICA_DB_CONNECTION_MISSING,
      severity: SEVERITY.ERROR,
      message: 'Carica DB: seleziona una connessione database.',
      nodeId: caricaNode.id,
      nodeName,
    }));
  }
  if (!d.schema_name) {
    issues.push(createIssue({
      id: RULE_IDS.CARICA_DB_SCHEMA_MISSING,
      severity: SEVERITY.ERROR,
      message: 'Carica DB: seleziona uno schema di destinazione.',
      nodeId: caricaNode.id,
      nodeName,
    }));
  }
  if (!d.table_name) {
    issues.push(createIssue({
      id: RULE_IDS.CARICA_DB_TABLE_MISSING,
      severity: SEVERITY.ERROR,
      message: 'Carica DB: specifica il nome della tabella di destinazione.',
      nodeId: caricaNode.id,
      nodeName,
    }));
  }
  if (['upsert', 'update'].includes(d.write_mode || 'create')) {
    if (!(d.match_columns || []).length) {
      issues.push(createIssue({
        id: RULE_IDS.CARICA_DB_MATCH_COLUMNS_MISSING,
        severity: SEVERITY.ERROR,
        message: `Carica DB: modalità "${d.write_mode}" richiede almeno una colonna chiave in "Colonne di corrispondenza".`,
        nodeId: caricaNode.id,
        nodeName,
      }));
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
      issues.push(createIssue({
        id: RULE_IDS.CARICA_ARCGIS_NOT_CONNECTED,
        severity: SEVERITY.ERROR,
        message: 'Nodo Carica ArcGIS non collegato al nodo Output.',
        nodeId: node.id,
        nodeName,
      }));
    }
  }

  if (!d.arcgis_connection_id) {
    issues.push(createIssue({
      id: RULE_IDS.CARICA_ARCGIS_CONNECTION_MISSING,
      severity: SEVERITY.ERROR,
      message: 'Carica ArcGIS: seleziona una connessione ArcGIS.',
      nodeId: node.id,
      nodeName,
    }));
  }

  const op = d.arcgis_operation || 'add';

  if (['add', 'update', 'upsert', 'delete'].includes(op) && !d.arcgis_layer_url) {
    issues.push(createIssue({
      id: RULE_IDS.CARICA_ARCGIS_LAYER_URL_MISSING,
      severity: SEVERITY.ERROR,
      message: 'Carica ArcGIS: seleziona il layer di destinazione.',
      nodeId: node.id,
      nodeName,
    }));
  }

  if (op === 'create_service' && !d.arcgis_new_service_name) {
    issues.push(createIssue({
      id: RULE_IDS.CARICA_ARCGIS_NEW_SERVICE_NAME_MISSING,
      severity: SEVERITY.ERROR,
      message: 'Carica ArcGIS: specifica il nome del nuovo Feature Service.',
      nodeId: node.id,
      nodeName,
    }));
  }

  if (['update', 'upsert', 'delete'].includes(op) && !d.arcgis_match_column) {
    issues.push(createIssue({
      id: RULE_IDS.CARICA_ARCGIS_MATCH_COLUMN_MISSING,
      severity: SEVERITY.ERROR,
      message: `Carica ArcGIS: seleziona la colonna chiave per l'operazione "${op}".`,
      nodeId: node.id,
      nodeName,
    }));
  }

  return issues;
}

function validateTransforms(transformNodes, registry) {
  const issues = [];
  if (!registry) return issues;

  for (const node of transformNodes) {
    const opId = node.data?.transform_op;
    if (!opId) {
      issues.push(createIssue({
        id: RULE_IDS.TRANSFORM_OP_MISSING,
        severity: SEVERITY.ERROR,
        message: 'Nodo trasformazione senza operazione selezionata.',
        nodeId: node.id,
        nodeName: 'Trasformazione',
      }));
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

      if (param.required && isEmpty) {
        issues.push(createIssue({
          id: RULE_IDS.TRANSFORM_PARAM_REQUIRED,
          severity: SEVERITY.ERROR,
          message: `"${nodeName}": parametro obbligatorio "${param.label || param.id}" non configurato.`,
          nodeId: node.id,
          nodeName,
          field: param.id,
        }));
        continue;
      }
      if (isEmpty || !inputColumns.length) continue;

      if (param.type === 'column' && val) {
        if (!columnExists(val, inputColumns)) {
          issues.push(createIssue({
            id: RULE_IDS.TRANSFORM_COLUMN_NOT_FOUND,
            severity: SEVERITY.WARNING,
            message: `"${nodeName}": la colonna "${val}" non è disponibile nell'input del nodo.`,
            nodeId: node.id,
            nodeName,
            field: param.id,
          }));
        }
      }

      if ((param.type === 'columns' || param.type === 'string_list') && Array.isArray(val)) {
        for (const colName of val) {
          if (colName && !columnExists(colName, inputColumns)) {
            issues.push(createIssue({
              id: RULE_IDS.TRANSFORM_COLUMN_NOT_FOUND,
              severity: SEVERITY.WARNING,
              message: `"${nodeName}": la colonna "${colName}" non è disponibile nell'input del nodo.`,
              nodeId: node.id,
              nodeName,
              field: param.id,
            }));
          }
        }
      }

      if (param.type === 'geometry_column' && val) {
        if (!columnExists(val, inputColumns)) {
          issues.push(createIssue({
            id: RULE_IDS.TRANSFORM_COLUMN_NOT_FOUND,
            severity: SEVERITY.WARNING,
            message: `"${nodeName}": la colonna geometry "${val}" non è disponibile nell'input del nodo.`,
            nodeId: node.id,
            nodeName,
            field: param.id,
          }));
        } else if (!isGeometryColumn(val, inputColumns)) {
          issues.push(createIssue({
            id: RULE_IDS.TRANSFORM_GEOMETRY_TYPE_INVALID,
            severity: SEVERITY.ERROR,
            message: `"${nodeName}": la colonna "${val}" non è di tipo geometry.`,
            nodeId: node.id,
            nodeName,
            field: param.id,
          }));
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
        issues.push(createIssue({
          id: RULE_IDS.SOURCE_NO_VISIBLE_COLUMNS,
          severity: SEVERITY.ERROR,
          message: 'Tutte le colonne sono state deselezionate. Seleziona almeno una colonna.',
          nodeId: node.id,
          nodeName: getNodeName(node),
        }));
      }
    }
  }
  return issues;
}

function validateOrphans(nodes, edges, connectedToResult, registry = null) {
  const issues = [];
  const sourceTypes = ['project_source', 'file_source', 'staticValues', 'datasourceNode'];
  const transformTypes = ['transform', 'multi_join'];
  const allEdgeSources = new Set(edges.map(e => e.source));
  const allEdgeTargets = new Set(edges.map(e => e.target));

  for (const node of nodes.filter(n => transformTypes.includes(n.type))) {
    if (!connectedToResult.has(node.id)) {
      issues.push(createIssue({
        id: RULE_IDS.TRANSFORM_ORPHAN,
        severity: SEVERITY.WARNING,
        message: "Nodo trasformazione non collegato al percorso di output — verrà ignorato durante l'esecuzione.",
        nodeId: node.id,
        nodeName: getNodeName(node, registry),
      }));
    }
  }

  for (const node of nodes.filter(n => sourceTypes.includes(n.type))) {
    if (!allEdgeSources.has(node.id) && !allEdgeTargets.has(node.id)) {
      issues.push(createIssue({
        id: RULE_IDS.SOURCE_ISOLATED,
        severity: SEVERITY.WARNING,
        message: "Sorgente non collegata ad alcun nodo — verrà ignorata durante l'esecuzione.",
        nodeId: node.id,
        nodeName: getNodeName(node, registry),
      }));
    }
  }

  return issues;
}

/**
 * Valida il canvas MANIPOLA.
 *
 * @param {Array} nodes
 * @param {Array} edges
 * @param {Object|null} registry — Oggetto transforms dal registry JSON (opzionale)
 * @returns {import('./canvasValidationContract').CanvasValidationResult}
 */
export function validateManipolaCanvas(nodes, edges, registry = null) {
  if (isCanvasEmpty(nodes)) {
    return buildResult([createIssue({
      id: RULE_IDS.CANVAS_EMPTY,
      severity: SEVERITY.ERROR,
      message: 'Canvas vuoto. Aggiungi almeno una sorgente e un nodo Output.',
    })]);
  }

  const issues = [];

  issues.push(...validateStructure(nodes, edges));

  const resultNode = nodes.find(n => n.type === 'result');
  if (!resultNode) {
    issues.push(createIssue({
      id: RULE_IDS.RESULT_NODE_MISSING,
      severity: SEVERITY.ERROR,
      message: 'Nodo Output mancante. Aggiungi un nodo "Output Manipola".',
    }));
    return buildResult(issues);
  }

  const connectedToResult = getNodesConnectedToResult(resultNode.id, edges);

  issues.push(...validateResult(resultNode, edges, connectedToResult, nodes));

  const caricaNode = nodes.find(n => n.type === 'carica');
  if (caricaNode) {
    issues.push(...validateCarica(caricaNode, edges, nodes));
  }

  const caricaArcgisNode = nodes.find(n => n.type === 'caricaArcgis');
  if (caricaArcgisNode) {
    issues.push(...validateCaricanArcGIS(caricaArcgisNode, edges, nodes));
  }

  const connectedTransforms = nodes.filter(n =>
    ['transform', 'multi_join'].includes(n.type) && connectedToResult.has(n.id)
  );
  issues.push(...validateTransforms(connectedTransforms, registry));

  const connectedSources = nodes.filter(n =>
    ['project_source', 'file_source', 'staticValues', 'datasourceNode'].includes(n.type) && connectedToResult.has(n.id)
  );
  issues.push(...validateSources(connectedSources));

  issues.push(...validateOrphans(nodes, edges, connectedToResult, registry));

  return buildResult(issues);
}
