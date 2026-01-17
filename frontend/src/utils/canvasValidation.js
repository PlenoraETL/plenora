/**
 * Utility per validazione canvas
 */

/**
 * Valida il canvas e trova errori
 * 
 * @param {Array} nodes - Array di nodi React Flow
 * @param {Array} edges - Array di edges React Flow
 * @returns {Object} {valid, errors, disconnectedNodeIds}
 */
export function validateCanvas(nodes, edges) {
  const errors = [];
  let disconnectedNodeIds = [];

  // Validazione 1: Canvas vuoto
  if (nodes.length === 0) {
    return {
      valid: false,
      errors: ['Canvas vuoto. Aggiungi almeno una tabella.'],
      disconnectedNodeIds: []
    };
  }

  // Validazione 2: Grafi disconnessi
  const graphs = findDisconnectedGraphs(nodes, edges);
  
  if (graphs.length > 1) {
    // Tutti i nodi tranne quelli del grafo principale sono "disconnessi"
    const mainGraph = graphs[0]; // Grafo più grande
    const otherGraphs = graphs.slice(1);
    
    disconnectedNodeIds = otherGraphs.flatMap(g => 
      g.nodes.map(n => n.id)
    );
    
    errors.push(
      `Canvas contiene ${graphs.length} grafi separati. ` +
      `Collega tutte le tabelle in un unico grafo o rimuovi quelle non utilizzate.`
    );
  }

  // Validazione 3: Join incompleti (senza colonne configurate, escludi edge verso output)
  const incompleteJoins = edges.filter(edge => {
    const targetNode = nodes.find(n => n.id === edge.target);
    // Skip edge verso resultNode (non ha configurazione join)
    if (targetNode && targetNode.type === 'resultNode') return false;
    
    // Per join spaziali, controlla le colonne geometriche
    if (edge.data?.is_spatial) {
      return !edge.data?.source_geom_column || !edge.data?.target_geom_column;
    }
    
    // Per join standard, controlla le colonne normali
    return !edge.data?.source_column || !edge.data?.target_column;
  });
  
  if (incompleteJoins.length > 0) {
    errors.push(
      `${incompleteJoins.length} join non configurati. ` +
      `Clicca sulle frecce per configurare le colonne.`
    );
  }

  // ===== VALIDAZIONE 1: Colonne join esistono (non più required visibili) =====
  edges.forEach(edge => {
    const sourceNode = nodes.find(n => n.id === edge.source);
    const targetNode = nodes.find(n => n.id === edge.target);
    
    if (!sourceNode || !targetNode) return;
    
    // Skip edge verso resultNode
    if (targetNode.type === 'resultNode') return;
    
    // Per join spaziali, valida le colonne geometriche
    if (edge.data?.is_spatial) {
      const sourceGeomCol = edge.data?.source_geom_column;
      const targetGeomCol = edge.data?.target_geom_column;
      
      if (sourceGeomCol && targetGeomCol) {
        // Verifica colonna geometria sorgente esiste (normale o calcolata)
        const sourceColExists = sourceNode.data?.columns?.find(col => col.name === sourceGeomCol) ||
          sourceNode.data?.calculated_columns?.find(col => col.name === sourceGeomCol);
        
        if (!sourceColExists) {
          const sourceTableName = sourceNode.data?.alias || sourceNode.data?.table_name;
          errors.push(
            `Join spaziale invalido: geometria "${sourceGeomCol}" non trovata in "${sourceTableName}"`
          );
        }
        
        // Verifica colonna geometria destinazione esiste (normale o calcolata)
        const targetColExists = targetNode.data?.columns?.find(col => col.name === targetGeomCol) ||
          targetNode.data?.calculated_columns?.find(col => col.name === targetGeomCol);
        
        if (!targetColExists) {
          const targetTableName = targetNode.data?.alias || targetNode.data?.table_name;
          errors.push(
            `Join spaziale invalido: geometria "${targetGeomCol}" non trovata in "${targetTableName}"`
          );
        }
      }
    } else {
      // Per join standard, valida le colonne normali
      const sourceCol = edge.data?.source_column;
      const targetCol = edge.data?.target_column;
      
      if (sourceCol && targetCol) {
        // Verifica colonna sorgente esiste (non serve visibile)
        const sourceColExists = sourceNode.data?.columns?.find(
          col => col.name === sourceCol
        );
        
        if (!sourceColExists) {
          const sourceTableName = sourceNode.data?.alias || sourceNode.data?.table_name;
          errors.push(
            `Join invalido: colonna "${sourceCol}" non trovata in "${sourceTableName}"`
          );
        }
        
        // Verifica colonna destinazione esiste (non serve visibile)
        const targetColExists = targetNode.data?.columns?.find(
          col => col.name === targetCol
        );
        
        if (!targetColExists) {
          const targetTableName = targetNode.data?.alias || targetNode.data?.table_name;
          errors.push(
            `Join invalido: colonna "${targetCol}" non trovata in "${targetTableName}"`
          );
        }
      }
    }
  });

  // ===== VALIDAZIONE 2: Cicli nel grafo (circular dependencies) =====
  const hasCycle = detectCycle(nodes, edges);
  if (hasCycle) {
    errors.push(
      'Rilevato ciclo nel grafo. I join non possono formare un ciclo (es: A→B→C→A). ' +
      'Rimuovi una connessione per risolvere.'
    );
  }

  // ===== VALIDAZIONE 3: Conflitti di nomi colonne (duplicati senza alias) =====
  const columnConflicts = findColumnConflicts(nodes);
  if (columnConflicts.length > 0) {
    columnConflicts.forEach(conflict => {
      errors.push(
        `Conflitto di nome colonna: "${conflict.columnName}" appare in ${conflict.tables.join(', ')}. ` +
        `Aggiungi alias per disambiguare.`
      );
    });
  }

  // Validazione 4: Nessuna colonna visibile (escludi resultNode)
  const tableNodes = nodes.filter(n => n.type === 'tableNode' || n.type === 'source'); // ← Supporta entrambi i tipi
  const nodesWithoutColumns = tableNodes.filter(node => {
    const visibleColumns = node.data?.columns?.filter(col => col.visible) || [];
    return visibleColumns.length === 0;
  });

  if (nodesWithoutColumns.length > 0) {
    errors.push(
      `${nodesWithoutColumns.length} tabelle senza colonne visibili. ` +
      `Seleziona almeno una colonna per ogni tabella.`
    );
  }

  // ===== VALIDAZIONE 5: Nodo risultato =====
  const resultNodes = nodes.filter(node => node.type === 'resultNode' || node.type === 'result'); // ← Supporta entrambi i tipi
  
  if (resultNodes.length === 0) {
    errors.push(
      'Manca il nodo risultato. Aggiungi un nodo output cliccando "📁 Output" nella toolbar.'
    );
  } else if (resultNodes.length > 1) {
    errors.push(
      'Troppi nodi risultato. Puoi avere solo un nodo output per progetto.'
    );
  } else {
    // Verifica che il nodo risultato abbia almeno una connessione in entrata
    const resultNode = resultNodes[0];
    const incomingEdges = edges.filter(e => e.target === resultNode.id);
    
    if (incomingEdges.length === 0) {
      errors.push(
        'Nodo risultato non connesso. Collega almeno una tabella al nodo output.'
      );
    }
  }

  return {
    valid: errors.length === 0,
    errors,
    disconnectedNodeIds
  };
}

/**
 * Trova tutti i grafi disconnessi nel canvas
 */
function findDisconnectedGraphs(nodes, edges) {
  if (nodes.length === 0) {
    return [];
  }

  // Se nessun edge, ogni nodo è un grafo separato
  if (edges.length === 0) {
    return nodes.map(node => ({
      nodes: [node],
      edges: [],
      nodeIds: new Set([node.id])
    }));
  }

  // Costruisci mappa di adiacenza (grafo non diretto)
  const adjacency = new Map();
  nodes.forEach(node => adjacency.set(node.id, new Set()));
  
  edges.forEach(edge => {
    if (adjacency.has(edge.source) && adjacency.has(edge.target)) {
      adjacency.get(edge.source).add(edge.target);
      adjacency.get(edge.target).add(edge.source); // Bidirezionale
    }
  });

  // Trova grafi connessi con DFS
  const visited = new Set();
  const graphs = [];

  nodes.forEach(node => {
    if (!visited.has(node.id)) {
      const graphNodeIds = exploreGraph(node.id, adjacency, visited);
      
      const graphNodes = nodes.filter(n => graphNodeIds.has(n.id));
      const graphEdges = edges.filter(e => 
        graphNodeIds.has(e.source) && graphNodeIds.has(e.target)
      );
      
      graphs.push({
        nodes: graphNodes,
        edges: graphEdges,
        nodeIds: graphNodeIds
      });
    }
  });

  // Ordina per dimensione (più grande prima)
  graphs.sort((a, b) => b.nodes.length - a.nodes.length);

  return graphs;
}

/**
 * Esplora grafo connesso usando DFS
 */
function exploreGraph(startNodeId, adjacency, visited) {
  const nodeIds = new Set();
  const stack = [startNodeId];

  while (stack.length > 0) {
    const nodeId = stack.pop();
    
    if (visited.has(nodeId)) continue;
    
    visited.add(nodeId);
    nodeIds.add(nodeId);
    
    const neighbors = adjacency.get(nodeId) || new Set();
    neighbors.forEach(neighborId => {
      if (!visited.has(neighborId)) {
        stack.push(neighborId);
      }
    });
  }

  return nodeIds;
}

/**
 * Trova il nodo root di un grafo (nodo senza edges in entrata)
 */
export function findRootNode(nodes, edges) {
  if (nodes.length === 0) return null;
  
  // Nodi con edges in entrata
  const nodesWithIncoming = new Set(edges.map(e => e.target));
  
  // Root = nodi senza edges in entrata
  const rootNodes = nodes.filter(node => !nodesWithIncoming.has(node.id));
  
  // Se tutti hanno incoming (ciclo), prendi il primo
  return rootNodes.length > 0 ? rootNodes[0] : nodes[0];
}

/**
 * Rileva cicli nel grafo usando DFS con colori
 * @returns {boolean} true se c'è un ciclo
 */
function detectCycle(nodes, edges) {
  if (nodes.length === 0 || edges.length === 0) return false;

  // Costruisci grafo diretto (source → target)
  const adjacency = new Map();
  nodes.forEach(node => adjacency.set(node.id, []));
  edges.forEach(edge => {
    if (adjacency.has(edge.source)) {
      adjacency.get(edge.source).push(edge.target);
    }
  });

  // DFS con colori: white (0), gray (1), black (2)
  const colors = new Map();
  nodes.forEach(node => colors.set(node.id, 0));

  // Funzione DFS ricorsiva
  function dfsHasCycle(nodeId) {
    colors.set(nodeId, 1); // Gray = in visita

    const neighbors = adjacency.get(nodeId) || [];
    for (const neighborId of neighbors) {
      const color = colors.get(neighborId);
      
      if (color === 1) {
        // Trovato nodo gray = back edge = ciclo!
        return true;
      }
      
      if (color === 0) {
        // Nodo white = non visitato
        if (dfsHasCycle(neighborId)) {
          return true;
        }
      }
    }

    colors.set(nodeId, 2); // Black = visitato completamente
    return false;
  }

  // Controlla da ogni nodo non visitato
  for (const node of nodes) {
    if (colors.get(node.id) === 0) {
      if (dfsHasCycle(node.id)) {
        return true;
      }
    }
  }

  return false;
}

/**
 * Trova conflitti di nomi colonne (stesso nome in tabelle diverse senza alias)
 * @returns {Array} Array di conflitti {columnName, tables}
 */
function findColumnConflicts(nodes) {
  const conflicts = [];
  
  // Mappa: nome_colonna → [tabelle che la usano]
  const columnUsage = new Map();

  // ← FIX: Filtra solo i nodi tabella, escludi resultNode
  const tableNodes = nodes.filter(n => n.type === 'tableNode' || n.type === 'source');

  tableNodes.forEach(node => {
    const tableName = node.data?.alias || node.data?.table_name;
    const visibleColumns = node.data?.columns?.filter(col => col.visible) || [];

    visibleColumns.forEach(col => {
      // Nome finale della colonna (alias o nome originale)
      const finalName = col.alias || col.name;

      if (!columnUsage.has(finalName)) {
        columnUsage.set(finalName, []);
      }
      columnUsage.get(finalName).push(tableName);
    });
  });

  // Trova colonne usate da più tabelle (conflitto)
  columnUsage.forEach((tables, columnName) => {
    if (tables.length > 1) {
      conflicts.push({
        columnName,
        tables
      });
    }
  });

  return conflicts;
}