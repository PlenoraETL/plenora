import { detectCycle, getNodeName } from './graphUtils';
import {
  RULE_IDS,
  SEVERITY,
  isCanvasEmpty,
  createIssue,
  buildResult,
} from './canvasValidationContract';

/**
 * validateEstraiCanvas.js
 *
 * Validazione canvas ESTRAI — livello 3 (strutturale + join + semantico colonne).
 * Ritorna un `CanvasValidationResult` (vedi `canvasValidationContract.js`).
 * Ogni issue ha un `id` stabile (`RULE_IDS.*`) per parita' FE/BE futura.
 *
 * CHECKS:
 *  Strutturali:
 *   S1 - Canvas vuoto (CANVAS_EMPTY)
 *   S2 - resultNode max 1 (RESULT_NODE_DUPLICATED)
 *   S3 - carica max 1 (CARICA_DUPLICATED)
 *   S4 - Cicli (CYCLE_DETECTED)
 *   S5 - Sorgenti isolate (SOURCE_ISOLATED)
 *
 *  Result:
 *   R1 - resultNode connesso (RESULT_NOT_CONNECTED)
 *   R2 - Formato geo senza geometry (RESULT_GEO_NO_GEOMETRY)
 *
 *  Edge join:
 *   J1 - Join non configurato (JOIN_NOT_CONFIGURED)
 *   J2 - Join spaziale senza geom (JOIN_SPATIAL_NO_GEOM)
 *   J3 - Colonna join non trovata (JOIN_COLUMN_NOT_FOUND)
 *
 *  Source:
 *   P1 - Nessuna colonna visibile (SOURCE_NO_VISIBLE_COLUMNS)
 *   P2 - Conflitti nomi colonne (COLUMN_NAME_CONFLICT)
 *
 *  Carica:
 *   C1 - Carica non collegato a output (CARICA_NOT_CONNECTED)
 *   C2 - DB connection/schema/table missing (CARICA_DB_*)
 *   C3 - DB upsert/update senza match_columns (CARICA_DB_MATCH_COLUMNS_MISSING)
 *   C4 - ArcGIS connection/layer/service/match (CARICA_ARCGIS_*)
 */

const SOURCE_TYPES = ['tableNode', 'featureLayerNode'];
const UNSUPPORTED_SOURCE_TYPES = ['datasourceNode', 'datasource_node', 'staticValues'];

function isGeometryType(type) {
  if (!type) return false;
  return type.startsWith('geometry') || type === 'wkb_geometry' || type === 'geom';
}

function hasGeometryInNodes(nodes) {
  for (const node of nodes) {
    const cols = node.data?.columns || [];
    if (cols.some(c => c.visible !== false && isGeometryType(c.type || ''))) return true;
  }
  return false;
}

function validateStructure(nodes, edges) {
  const issues = [];

  const resultNodes = nodes.filter(n => n.type === 'resultNode');
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

  for (const node of nodes.filter(n => UNSUPPORTED_SOURCE_TYPES.includes(n.type))) {
    issues.push(createIssue({
      id: RULE_IDS.SOURCE_UNSUPPORTED,
      severity: SEVERITY.ERROR,
      message: 'ESTRAI supporta solo tabelle database e layer ArcGIS come sorgenti.',
      nodeId: node.id,
      nodeName: getNodeName(node),
    }));
  }

  const allEdgeSources = new Set(edges.map(e => e.source));
  const allEdgeTargets = new Set(edges.map(e => e.target));
  for (const node of nodes.filter(n => SOURCE_TYPES.includes(n.type))) {
    if (!allEdgeSources.has(node.id) && !allEdgeTargets.has(node.id)) {
      issues.push(createIssue({
        id: RULE_IDS.SOURCE_ISOLATED,
        severity: SEVERITY.WARNING,
        message: "Sorgente non collegata ad alcun nodo — verrà ignorata durante l'esecuzione.",
        nodeId: node.id,
        nodeName: getNodeName(node),
      }));
    }
  }

  return issues;
}

function validateResultNode(resultNode, edges, nodes) {
  const issues = [];

  const incoming = edges.filter(e => e.target === resultNode.id);
  if (incoming.length === 0) {
    issues.push(createIssue({
      id: RULE_IDS.RESULT_NOT_CONNECTED,
      severity: SEVERITY.ERROR,
      message: "Nodo Output non connesso. Collega almeno una tabella all'output.",
      nodeId: resultNode.id,
      nodeName: 'Output',
    }));
    return issues;
  }

  const format = resultNode.data?.format || 'csv';
  if (['geojson', 'gpkg', 'shp'].includes(format)) {
    const sourceNodes = nodes.filter(n => SOURCE_TYPES.includes(n.type));
    if (!hasGeometryInNodes(sourceNodes)) {
      issues.push(createIssue({
        id: RULE_IDS.RESULT_GEO_NO_GEOMETRY,
        severity: SEVERITY.ERROR,
        message: `Formato "${format.toUpperCase()}" richiede una colonna geometry, ma nessuna è disponibile (o tutte sono state deselezionate).`,
        nodeId: resultNode.id,
        nodeName: 'Output',
      }));
    }
  }

  return issues;
}

function validateEdges(edges, nodes) {
  const issues = [];
  const nodeMap = new Map(nodes.map(n => [n.id, n]));

  for (const edge of edges) {
    const targetNode = nodeMap.get(edge.target);
    if (targetNode && (targetNode.type === 'resultNode' || targetNode.type === 'carica' || targetNode.type === 'caricaArcgis')) continue;

    const isSpatial = edge.data?.is_spatial;

    if (isSpatial) {
      if (!edge.data?.source_geom_column || !edge.data?.target_geom_column) {
        issues.push(createIssue({
          id: RULE_IDS.JOIN_SPATIAL_NO_GEOM,
          severity: SEVERITY.ERROR,
          message: `Join spaziale non configurato: mancano le colonne geometry.`,
          nodeId: edge.target,
          nodeName: getNodeName(targetNode),
          edgeId: edge.id,
        }));
      }
    } else {
      if (!edge.data?.source_column || !edge.data?.target_column) {
        issues.push(createIssue({
          id: RULE_IDS.JOIN_NOT_CONFIGURED,
          severity: SEVERITY.ERROR,
          message: `Join non configurato: mancano le colonne di collegamento. Clicca sulla freccia per configurarlo.`,
          nodeId: edge.target,
          nodeName: getNodeName(targetNode),
          edgeId: edge.id,
        }));
      } else {
        const sourceNode = nodeMap.get(edge.source);
        if (sourceNode) {
          const sourceCols = (sourceNode.data?.columns || []).map(c => c.alias || c.name);
          if (!sourceCols.includes(edge.data.source_column)) {
            issues.push(createIssue({
              id: RULE_IDS.JOIN_COLUMN_NOT_FOUND,
              severity: SEVERITY.WARNING,
              message: `Join: colonna "${edge.data.source_column}" non trovata in "${getNodeName(sourceNode)}".`,
              nodeId: edge.source,
              nodeName: getNodeName(sourceNode),
              edgeId: edge.id,
              field: edge.data.source_column,
            }));
          }
        }
        if (targetNode) {
          const targetCols = (targetNode.data?.columns || []).map(c => c.alias || c.name);
          if (targetNode.type !== 'resultNode' && !targetCols.includes(edge.data.target_column)) {
            issues.push(createIssue({
              id: RULE_IDS.JOIN_COLUMN_NOT_FOUND,
              severity: SEVERITY.WARNING,
              message: `Join: colonna "${edge.data.target_column}" non trovata in "${getNodeName(targetNode)}".`,
              nodeId: edge.target,
              nodeName: getNodeName(targetNode),
              edgeId: edge.id,
              field: edge.data.target_column,
            }));
          }
        }
      }
    }
  }

  return issues;
}

function validateSources(nodes) {
  const issues = [];
  const sourceNodes = nodes.filter(n => SOURCE_TYPES.includes(n.type));

  for (const node of sourceNodes) {
    const cols = node.data?.columns || [];
    if (cols.length > 0) {
      const visible = cols.filter(c => c.visible !== false);
      if (visible.length === 0) {
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

  const columnUsage = new Map();
  for (const node of sourceNodes) {
    const tableName = getNodeName(node);
    const visibleCols = (node.data?.columns || []).filter(c => c.visible !== false);
    for (const col of visibleCols) {
      const finalName = col.alias || col.name;
      if (!columnUsage.has(finalName)) columnUsage.set(finalName, []);
      columnUsage.get(finalName).push(tableName);
    }
  }
  columnUsage.forEach((tables, colName) => {
    if (tables.length > 1) {
      issues.push(createIssue({
        id: RULE_IDS.COLUMN_NAME_CONFLICT,
        severity: SEVERITY.WARNING,
        message: `Conflitto nome colonna: "${colName}" appare in ${tables.join(', ')}. Aggiungi un alias per disambiguare.`,
        field: colName,
      }));
    }
  });

  return issues;
}

function validateCarica(caricaNode, edges, nodes) {
  const issues = [];
  const d = caricaNode.data || {};
  const nodeName = getNodeName(caricaNode);

  const resultNode = nodes.find(n => n.type === 'resultNode');
  if (resultNode) {
    const incoming = edges.filter(e => e.target === caricaNode.id);
    if (!incoming.some(e => e.source === resultNode.id)) {
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
  if (['upsert', 'update'].includes(d.write_mode || 'create') && !(d.match_columns || []).length) {
    issues.push(createIssue({
      id: RULE_IDS.CARICA_DB_MATCH_COLUMNS_MISSING,
      severity: SEVERITY.ERROR,
      message: `Carica DB: modalità "${d.write_mode}" richiede almeno una colonna chiave in "Colonne di corrispondenza".`,
      nodeId: caricaNode.id,
      nodeName,
    }));
  }

  return issues;
}

function validateCaricanArcGIS(node, edges, nodes) {
  const issues = [];
  const d = node.data || {};
  const nodeName = 'Carica ArcGIS';

  const resultNode = nodes.find(n => n.type === 'resultNode');
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

/**
 * Valida il canvas ESTRAI.
 *
 * @param {Array} nodes
 * @param {Array} edges
 * @returns {import('./canvasValidationContract').CanvasValidationResult}
 */
export function validateEstraiCanvas(nodes, edges) {
  if (isCanvasEmpty(nodes)) {
    return buildResult([createIssue({
      id: RULE_IDS.CANVAS_EMPTY,
      severity: SEVERITY.ERROR,
      message: 'Canvas vuoto. Aggiungi almeno una tabella e un nodo Output.',
    })]);
  }

  const issues = [];

  issues.push(...validateStructure(nodes, edges));

  const resultNode = nodes.find(n => n.type === 'resultNode');
  if (!resultNode) {
    issues.push(createIssue({
      id: RULE_IDS.RESULT_NODE_MISSING,
      severity: SEVERITY.ERROR,
      message: 'Nodo Output mancante. Aggiungi un nodo "Output".',
    }));
    return buildResult(issues);
  }

  issues.push(...validateResultNode(resultNode, edges, nodes));
  issues.push(...validateEdges(edges, nodes));
  issues.push(...validateSources(nodes));

  const caricaNode = nodes.find(n => n.type === 'carica');
  if (caricaNode) {
    issues.push(...validateCarica(caricaNode, edges, nodes));
  }

  const caricaArcgisNode = nodes.find(n => n.type === 'caricaArcgis');
  if (caricaArcgisNode) {
    issues.push(...validateCaricanArcGIS(caricaArcgisNode, edges, nodes));
  }

  return buildResult(issues);
}
