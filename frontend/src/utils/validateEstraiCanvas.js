import { detectCycle, getNodeName } from './graphUtils';

/**
 * validateEstraiCanvas.js
 *
 * Validazione canvas ESTRAI — livello 3 (strutturale + join + semantico colonne).
 * Stesso formato di validateManipolaCanvas: { isValid, issues[] }
 * Ogni issue: { severity: 'error'|'warning'|'info', message, nodeId?, nodeName? }
 *
 * CHECKS:
 *  Strutturali:
 *   S1 - Canvas vuoto
 *   S2 - resultNode presente (max 1)
 *   S3 - carica max 1
 *   S4 - Cicli nel grafo
 *   S5 - Nodi sorgente isolati (nessun edge)
 *
 *  Nodo resultNode:
 *   R1 - Connesso (almeno 1 edge in ingresso)
 *   R2 - Formato geo (GeoJSON/GPKG/SHP) senza colonne geometry disponibili
 *
 *  Edge join:
 *   J1 - Join standard non configurato (source_column o target_column vuoti)
 *   J2 - Join spaziale senza geometry columns configurate
 *   J3 - Colonna join referenziata non esiste nel nodo sorgente
 *
 *  Nodi sorgente:
 *   P1 - Nessuna colonna visibile
 *   P2 - Conflitti nomi colonne tra tabelle (stesso nome in 2+ tabelle senza alias)
 *
 *  Nodo carica:
 *   C1 - Carica non connesso al resultNode
 *   C2 - DB: connection_id, schema_name, table_name
 *   C3 - DB upsert/update: match_columns non vuoto
 *   C4 - ArcGIS: arcgis_connection_id
 *   C5 - ArcGIS add/update/delete: arcgis_layer_url
 *   C6 - ArcGIS create_service: arcgis_new_service_name
 *   C7 - ArcGIS update/delete: OBJECTID nelle colonne input
 */

// ============================================================
// HELPERS
// ============================================================

const SOURCE_TYPES = ['tableNode', 'datasourceNode', 'featureLayerNode', 'staticValues'];

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

// ============================================================
// VALIDATORI PER CATEGORIA
// ============================================================

function validateStructure(nodes, edges) {
  const issues = [];

  // S2 - resultNode max 1
  const resultNodes = nodes.filter(n => n.type === 'resultNode');
  if (resultNodes.length > 1) {
    issues.push({ severity: 'error', message: `Troppi nodi Output (${resultNodes.length}). Può esserci solo un nodo Output per canvas.` });
  }

  // S3 - carica max 1
  const caricaNodes = nodes.filter(n => n.type === 'carica');
  if (caricaNodes.length > 1) {
    issues.push({ severity: 'error', message: `Troppi nodi Carica (${caricaNodes.length}). Può esserci solo un nodo Carica per canvas.` });
  }

  // S4 - Cicli
  if (detectCycle(nodes, edges)) {
    issues.push({ severity: 'error', message: 'Rilevato ciclo nel grafo. Rimuovi una connessione per risolvere.' });
  }

  // S5 - Sorgenti isolate
  const allEdgeSources = new Set(edges.map(e => e.source));
  const allEdgeTargets = new Set(edges.map(e => e.target));
  for (const node of nodes.filter(n => SOURCE_TYPES.includes(n.type))) {
    if (!allEdgeSources.has(node.id) && !allEdgeTargets.has(node.id)) {
      issues.push({
        severity: 'warning',
        message: "Sorgente non collegata ad alcun nodo — verrà ignorata durante l'esecuzione.",
        nodeId: node.id, nodeName: getNodeName(node)
      });
    }
  }

  return issues;
}

function validateResultNode(resultNode, edges, nodes) {
  const issues = [];

  // R1 - Connesso
  const incoming = edges.filter(e => e.target === resultNode.id);
  if (incoming.length === 0) {
    issues.push({
      severity: 'error',
      message: "Nodo Output non connesso. Collega almeno una tabella all'output.",
      nodeId: resultNode.id, nodeName: 'Output'
    });
    return issues;
  }

  // R2 - Formato geo senza geometry
  const format = resultNode.data?.format || 'csv';
  if (['geojson', 'gpkg', 'shp'].includes(format)) {
    const sourceNodes = nodes.filter(n => SOURCE_TYPES.includes(n.type));
    if (!hasGeometryInNodes(sourceNodes)) {
      issues.push({
        severity: 'error',
        message: `Formato "${format.toUpperCase()}" richiede una colonna geometry, ma nessuna è disponibile (o tutte sono state deselezionate).`,
        nodeId: resultNode.id, nodeName: 'Output'
      });
    }
  }

  return issues;
}

function validateEdges(edges, nodes) {
  const issues = [];
  const nodeMap = new Map(nodes.map(n => [n.id, n]));

  for (const edge of edges) {
    const targetNode = nodeMap.get(edge.target);
    // Skip edge verso resultNode, carica DB e carica ArcGIS
    if (targetNode && (targetNode.type === 'resultNode' || targetNode.type === 'carica' || targetNode.type === 'caricaArcgis')) continue;

    const isSpatial = edge.data?.is_spatial;

    if (isSpatial) {
      // J2 - Spatial join senza geometry columns
      if (!edge.data?.source_geom_column || !edge.data?.target_geom_column) {
        const sourceNode = nodeMap.get(edge.source);
        issues.push({
          severity: 'error',
          message: `Join spaziale non configurato: mancano le colonne geometry.`,
          nodeId: edge.target,
          nodeName: getNodeName(targetNode)
        });
      }
    } else {
      // J1 - Join standard non configurato
      if (!edge.data?.source_column || !edge.data?.target_column) {
        issues.push({
          severity: 'error',
          message: `Join non configurato: mancano le colonne di collegamento. Clicca sulla freccia per configurarlo.`,
          nodeId: edge.target,
          nodeName: getNodeName(targetNode)
        });
      } else {
        // J3 - Colonne join esistono nei nodi
        const sourceNode = nodeMap.get(edge.source);
        if (sourceNode) {
          const sourceCols = (sourceNode.data?.columns || []).map(c => c.alias || c.name);
          if (!sourceCols.includes(edge.data.source_column)) {
            issues.push({
              severity: 'warning',
              message: `Join: colonna "${edge.data.source_column}" non trovata in "${getNodeName(sourceNode)}".`,
              nodeId: edge.source, nodeName: getNodeName(sourceNode)
            });
          }
        }
        if (targetNode) {
          const targetCols = (targetNode.data?.columns || []).map(c => c.alias || c.name);
          if (targetNode.type !== 'resultNode' && !targetCols.includes(edge.data.target_column)) {
            issues.push({
              severity: 'warning',
              message: `Join: colonna "${edge.data.target_column}" non trovata in "${getNodeName(targetNode)}".`,
              nodeId: edge.target, nodeName: getNodeName(targetNode)
            });
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

  // P1 - Nessuna colonna visibile
  for (const node of sourceNodes) {
    const cols = node.data?.columns || [];
    if (cols.length > 0) {
      const visible = cols.filter(c => c.visible !== false);
      if (visible.length === 0) {
        issues.push({
          severity: 'error',
          message: 'Tutte le colonne sono state deselezionate. Seleziona almeno una colonna.',
          nodeId: node.id, nodeName: getNodeName(node)
        });
      }
    }
  }

  // P2 - Conflitti nomi colonne
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
      issues.push({
        severity: 'warning',
        message: `Conflitto nome colonna: "${colName}" appare in ${tables.join(', ')}. Aggiungi un alias per disambiguare.`
      });
    }
  });

  return issues;
}

function validateCarica(caricaNode, edges, nodes) {
  const issues = [];
  const d = caricaNode.data || {};
  const nodeName = getNodeName(caricaNode);

  // C1 - Connesso al resultNode
  const resultNode = nodes.find(n => n.type === 'resultNode');
  if (resultNode) {
    const incoming = edges.filter(e => e.target === caricaNode.id);
    if (!incoming.some(e => e.source === resultNode.id)) {
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
  if (['upsert', 'update'].includes(d.write_mode || 'create') && !(d.match_columns || []).length) {
    issues.push({
      severity: 'error',
      message: `Carica DB: modalità "${d.write_mode}" richiede almeno una colonna chiave in "Colonne di corrispondenza".`,
      nodeId: caricaNode.id, nodeName
    });
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

// ============================================================
// FUNZIONE PRINCIPALE
// ============================================================

/**
 * Valida il canvas ESTRAI — livello 3.
 *
 * @param {Array} nodes
 * @param {Array} edges
 * @returns {{ isValid: boolean, issues: Array }}
 */
export function validateEstraiCanvas(nodes, edges) {
  const issues = [];

  // S1 - Canvas vuoto
  if (!nodes || nodes.length === 0) {
    return { isValid: false, issues: [{ severity: 'error', message: 'Canvas vuoto. Aggiungi almeno una tabella e un nodo Output.' }] };
  }

  issues.push(...validateStructure(nodes, edges));

  // resultNode obbligatorio
  const resultNode = nodes.find(n => n.type === 'resultNode');
  if (!resultNode) {
    issues.push({ severity: 'error', message: 'Nodo Output mancante. Aggiungi un nodo "Output".' });
    return { isValid: false, issues };
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

  return {
    isValid: !issues.some(i => i.severity === 'error'),
    issues
  };
}
