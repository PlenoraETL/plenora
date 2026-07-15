/**
 * useColumnPropagation.js — Hook per propagazione colonne attraverso il canvas
 *
 * Unica fonte di verità per il calcolo delle colonne di ogni nodo.
 * Traversa il grafo dai nodi foglia al nodo risultato e calcola le colonne
 * disponibili per ogni trasformazione, aggiornando data.inputColumns su ogni nodo.
 *
 * Usato da: ManipolaEditor, ArricchisciEditor
 */
import { useCallback } from 'react';

/**
 * useColumnPropagation - UNICA FONTE DI VERITÀ per le colonne
 * 
 * Calcola per ogni nodo:
 * - inputColumns: colonne disponibili in ingresso (dal nodo precedente)
 * - columns: colonne in uscita (dopo la trasformazione)
 * 
 * I componenti TransformNode e MultiJoinNode NON devono fare calcoli,
 * leggono solo data.inputColumns e data.columns.
 */
export const useColumnPropagation = (setNodes) => {
  
  const propagateColumns = useCallback((nodes, edges) => {
    if (!nodes || nodes.length === 0) return;
    
    
    // 1. Costruisci il grafo
    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const inDegree = new Map();
    const outEdges = new Map();
    
    nodes.forEach(n => {
      inDegree.set(n.id, 0);
      outEdges.set(n.id, []);
    });
    
    edges.forEach(e => {
      if (nodeMap.has(e.target) && nodeMap.has(e.source)) {
        inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
        const outs = outEdges.get(e.source) || [];
        outs.push(e.target);
        outEdges.set(e.source, outs);
      }
    });
    
    // 2. Ordinamento topologico (Kahn's algorithm)
    const queue = [];
    inDegree.forEach((deg, id) => {
      if (deg === 0) queue.push(id);
    });
    
    const sortedIds = [];
    while (queue.length > 0) {
      const id = queue.shift();
      sortedIds.push(id);
      
      (outEdges.get(id) || []).forEach(targetId => {
        inDegree.set(targetId, inDegree.get(targetId) - 1);
        if (inDegree.get(targetId) === 0) {
          queue.push(targetId);
        }
      });
    }

    // Rilevamento cicli: se sortedIds non contiene tutti i nodi,
    // c'è almeno un ciclo nel grafo. Non propagare per non corrompere lo stato.
    if (sortedIds.length < nodeMap.size) {
      return;
    }
    
    // 3. Calcola colonne in ordine topologico
    // outputColumnsMap: nodeId -> colonne OUTPUT del nodo
    const outputColumnsMap = new Map();
    // inputColumnsMap: nodeId -> colonne INPUT del nodo
    const inputColumnsMap = new Map();
    
    sortedIds.forEach(nodeId => {
      const node = nodeMap.get(nodeId);
      if (!node) return;
      
      // Trova edge in ingresso
      const incomingEdges = edges.filter(e => e.target === nodeId);
      
      // Calcola inputColumns (colonne dai predecessori)
      let inputColumns = [];
      let outputColumns = [];
      
      if (incomingEdges.length === 0) {
        // Nodo sorgente - non ha input, le sue colonne sono l'output
        inputColumns = [];
        outputColumns = computeOutputColumns(node, inputColumns);
      } else if (node.type === 'multi_join') {
        // CASO SPECIALE: Multi-join - gestisci colonne separate per ogni sorgente
        const sourcesColumns = incomingEdges.map(edge => 
          outputColumnsMap.get(edge.source) || []
        );
        
        // inputColumns = tutte le colonne (per mostrare nel nodo)
        sourcesColumns.forEach(cols => {
          inputColumns = [...inputColumns, ...cols];
        });
        
        // outputColumns = simula il merge con rinomina come il backend
        outputColumns = computeMultiJoinOutput(node, sourcesColumns);
      } else if (node.type === 'transform' && node.data?.transform_op === 'concat') {
        // CASO SPECIALE: Concat/Union - output = colonne del primo input (impila righe, non colonne)
        const sourcesColumns = incomingEdges.map(edge => 
          outputColumnsMap.get(edge.source) || []
        );
        
        // inputColumns = tutte le colonne (per mostrare nel nodo)
        sourcesColumns.forEach(cols => {
          inputColumns = [...inputColumns, ...cols];
        });
        
        // outputColumns = colonne del primo input (concat mantiene schema del primo)
        outputColumns = sourcesColumns.length > 0 ? [...sourcesColumns[0]] : [];
      } else if (node.type === 'transform' && node.data?.transform_op === 'join') {
        // CASO SPECIALE: Join - applica suffissi _L e _R come il backend
        const config = node.data?.config || {};
        
        // Ordina edges per targetHandle per determinare left/right
        const sortedEdges = [...incomingEdges].sort((a, b) => {
          // Estrai handle dall'ID se non presente come attributo
          const extractHandle = (edge) => {
            if (edge.targetHandle) return edge.targetHandle;
            const match = edge.id?.match(/(input-\d+)$/);
            return match ? match[1] : 'input-9'; // fallback alto per ordinare dopo
          };
          return extractHandle(a).localeCompare(extractHandle(b));
        });
        
        const leftCols = sortedEdges.length > 0 ? (outputColumnsMap.get(sortedEdges[0].source) || []) : [];
        const rightCols = sortedEdges.length > 1 ? (outputColumnsMap.get(sortedEdges[1].source) || []) : [];
        const leftKeys = resolveColumnNameList(
          leftCols,
          config.left_keys_column_ids,
          config.left_keys || (config.left_on ? [config.left_on] : [])
        );
        const rightKeys = resolveColumnNameList(
          rightCols,
          config.right_keys_column_ids,
          config.right_keys || (config.right_on ? [config.right_on] : [])
        );
        
        // inputColumns separati per mostrare nel nodo (con marker left/right)
        inputColumns = [
          ...leftCols.map(c => ({ ...c, source: 'left' })),
          ...rightCols.map(c => ({ ...c, source: 'right' }))
        ];
        
        // outputColumns con suffissi _L e _R
        outputColumns = computeJoinOutput(leftCols, rightCols, leftKeys, rightKeys);
      } else {
        // Nodo normale - unisci tutte le colonne input
        incomingEdges.forEach(edge => {
          const sourceOutput = outputColumnsMap.get(edge.source) || [];
          inputColumns = [...inputColumns, ...sourceOutput];
        });
        outputColumns = computeOutputColumns(node, inputColumns);
      }
      
      inputColumnsMap.set(nodeId, inputColumns);
      outputColumnsMap.set(nodeId, outputColumns);
      
    });
    
    // 4. Aggiorna i nodi con ENTRAMBI inputColumns e columns
    setNodes(nds => nds.map(node => {
      const newInputColumns = inputColumnsMap.get(node.id) || [];
      const newOutputColumns = outputColumnsMap.get(node.id) || [];
      
      // Confronta per evitare update inutili
      const colSignature = (col) => {
        if (typeof col === 'string') return col;
        return `${col.id || col.column_id || ''}:${col.name}`;
      };
      const currentInputNames = (node.data?.inputColumns || []).map(colSignature).join(',');
      const currentOutputNames = (node.data?.columns || []).map(colSignature).join(',');
      const newInputNames = newInputColumns.map(colSignature).join(',');
      const newOutputNames = newOutputColumns.map(colSignature).join(',');
      const syncedConfig = syncConfigColumnReferences(
        node.data?.config,
        newInputColumns
      );
      const configChanged = syncedConfig !== node.data?.config;
      
      if (isSourceNode(node)) {
        if (currentInputNames === newInputNames) {
          return node;
        }
        return {
          ...node,
          data: {
            ...node.data,
            inputColumns: newInputColumns
          }
        };
      }

      if (currentInputNames === newInputNames && currentOutputNames === newOutputNames && !configChanged) {
        return node; // Nessun cambiamento
      }
      
      return {
        ...node,
        data: {
          ...node.data,
          inputColumns: newInputColumns,
          columns: newOutputColumns,
          ...(configChanged ? { config: syncedConfig } : {})
        }
      };
    }));
    
    
  }, [setNodes]);
  
  return { propagateColumns };
};

/**
 * Estrae colonne da un nodo sorgente
 */
function extractSourceColumns(node) {
  const cols = node.data?.columns || [];
  return cols.map(c => {
    if (typeof c === 'string') {
      return { name: c, type: 'str' };
    }
    return {
      id: c.id || c.column_id,
      column_id: c.column_id || c.id,
      source_name: c.source_name,
      display_name: c.display_name,
      name: c.alias || c.name,
      type: c.type || 'str'
    };
  });
}

function isSourceNode(node) {
  return node.type === 'project_source' || node.type === 'file_source'
    || node.type === 'staticValues'
    || node.type === 'datasourceNode' || node.type === 'datasource_node';
}

/**
 * Calcola le colonne OUTPUT di un nodo
 */
function computeOutputColumns(node, inputColumns) {
  const nodeType = node.type;
  
  // Nodi sorgente: usano le loro colonne salvate.
  if (isSourceNode(node)) {
    return extractSourceColumns(node);
  }
  
  // Nodi passthrough (result, carica): passano le colonne così come sono
  if (nodeType === 'result' || nodeType === 'carica') {
    return [...inputColumns];
  }
  
  // Multi-join: gestito separatamente nel loop principale
  if (nodeType === 'multi_join') {
    // Non dovrebbe mai arrivare qui, ma fallback
    return [...inputColumns];
  }
  
  // Transform node: applica la trasformazione
  if (nodeType === 'transform') {
    return applyTransformation(node, inputColumns);
  }
  
  // Default: passa colonne così come sono
  return [...inputColumns];
}

/**
 * Applica una trasformazione e restituisce le colonne output
 */
function applyTransformation(node, inputColumns) {
  const transform_op = node.data?.transform_op;
  const config = node.data?.config || {};
  
  let result = [...inputColumns];
  
  switch (transform_op) {
    case 'geo_from_coords':
    case 'geometry_from_coords':
    case 'geometry_from_wkt': {
      const geomCol = config.output_column || config.geometry_column || 'geometry';
      const srid = config.srid || 4326;
      result = result.filter(c => c.name !== geomCol);
      result.push({ name: geomCol, type: `geometry:point:${srid}` });
      break;
    }
    
    case 'drop':
    case 'drop_columns': {
      const dropCols = resolveColumnNameList(inputColumns, config.columns_column_ids, config.columns || []);
      result = result.filter(c => !dropCols.includes(c.name));
      break;
    }
    
    case 'select':
    case 'select_columns': {
      const selectCols = resolveColumnNameList(inputColumns, config.columns_column_ids, config.columns || []);
      if (selectCols.length > 0) {
        result = result.filter(c => selectCols.includes(c.name));
      }
      break;
    }
    
    case 'rename': {
      // Supporto lista rinominazioni
      if (config.renames && Array.isArray(config.renames)) {
        config.renames.forEach(r => {
          const oldName = resolveColumnName(inputColumns, r.old_name_id, r.old_name);
          const newName = r.new_name;
          if (oldName && newName) {
            result = result.map(c => 
              c.name === oldName ? { ...c, name: newName, display_name: newName } : c
            );
          }
        });
      }
      // Retrocompatibilità singola rinominazione
      else if (config.old_name && config.new_name) {
        const oldName = resolveColumnName(inputColumns, config.old_name_column_id, config.old_name);
        result = result.map(c => 
          c.name === oldName ? { ...c, name: config.new_name, display_name: config.new_name } : c
        );
      }
      break;
    }
    
    case 'add_column':
    case 'formula': {
      const newCol = config.column || config.output_column || config.new_column;
      if (newCol && !result.find(c => c.name === newCol)) {
        result.push({ name: newCol, type: 'str' });
      }
      break;
    }
    
    case 'concat_columns': {
      const newCol = config.output_column;
      if (newCol && !result.find(c => c.name === newCol)) {
        result.push({ name: newCol, type: 'str' });
      }
      break;
    }
    
    case 'pivot': {
      // Pivot cambia completamente lo schema:
      // Output = index_col + colonne generate dal mapping
      const indexCol = config.index_col;
      const mapping = config.mapping || {};
      
      // Inizia con colonna index
      result = [];
      if (indexCol) {
        const indexColObj = inputColumns.find(c => c.name === indexCol);
        result.push({ name: indexCol, type: indexColObj?.type || 'str' });
      }
      
      // Aggiungi colonne dal mapping (i valori del mapping sono i nomi output)
      Object.values(mapping).forEach(colName => {
        if (colName && !result.find(c => c.name === colName)) {
          result.push({ name: colName, type: 'real' }); // pivot values sono tipicamente numerici
        }
      });
      
      break;
    }
    
    case 'aggregate': {
      // Aggregate cambia lo schema:
      // Output = group_by columns + aggregated columns
      const groupBy = resolveColumnNameList(inputColumns, config.group_by_column_ids, config.group_by || []);
      const aggregations = config.aggregations || [];
      
      result = [];
      
      // Aggiungi colonne group_by
      groupBy.forEach(colName => {
        const col = inputColumns.find(c => c.name === colName);
        if (col) {
          result.push({ ...col });
        } else {
          result.push({ name: colName, type: 'str' });
        }
      });
      
      // Aggiungi colonne aggregate
      aggregations.forEach(agg => {
        const colName = resolveColumnName(inputColumns, agg.column_id, agg.column);
        const func = agg.function || 'count';
        // L'alias e' il nome effettivo prodotto dal backend e deve essere
        // propagato immediatamente ai nodi downstream. Prima veniva ignorato:
        // il canvas continuava quindi a esporre `colonna`/`colonna_funzione`
        // fino al successivo save + refresh dell'inferenza server-side.
        const alias = typeof agg.alias === 'string' ? agg.alias.trim() : '';
        const outputName = alias || (
          aggregations.filter(a => (
            resolveColumnName(inputColumns, a.column_id, a.column) === colName
          )).length > 1
            ? `${colName}_${func}`
            : colName
        );
        
        if (outputName && !result.find(c => c.name === outputName)) {
          const colType = func === 'count' ? 'int' : (func === 'concat' ? 'str' : 'real');
          result.push({ name: outputName, type: colType });
        }
      });
      
      // Se nessuna aggregazione, aggiungi count di default
      if (aggregations.length === 0 && !result.find(c => c.name === 'count')) {
        result.push({ name: 'count', type: 'int' });
      }
      
      break;
    }
    
    case 'conditional':
    case 'case_when': {
      const outCol = config.output_column;
      if (outCol && !result.find(c => c.name === outCol)) {
        result.push({ name: outCol, type: 'str' });
      }
      break;
    }
    
    case 'buffer':
    case 'simplify':
    case 'reproject':
    case 'geo_simplify':
    case 'geo_reproject':
    case 'geo_make_valid':
      // Mantengono geometry esistente
      break;

    // Spatial ops che AGGIUNGONO una colonna geometrica derivata
    // (mirror dei `_schema_geo_*` lato backend in
    // `app/core/projects/schema_geo_handlers.py`). Senza questi case
    // l'`OutputSettingsPanel` del result node downstream non vede la
    // nuova colonna finche' il backend non ricomputa lo schema al
    // save → dropdown "Colonna geometria" non offre la scelta.
    case 'geo_buffer': {
      const outCol = config.output_column || 'buffer_geom';
      if (outCol !== (config.geom_column || 'geometry')) {
        if (!result.find(c => c.name === outCol)) {
          result.push({ name: outCol, type: 'geometry:polygon' });
        }
      } else {
        result = result.map(c => c.name === outCol ? { ...c, type: 'geometry:polygon' } : c);
      }
      break;
    }
    case 'centroid':
    case 'geo_centroid': {
      const outCol = config.output_column || 'centroid';
      if (outCol !== (config.geom_column || 'geometry')) {
        if (!result.find(c => c.name === outCol)) {
          result.push({ name: outCol, type: 'geometry:point' });
        }
      } else {
        result = result.map(c => c.name === outCol ? { ...c, type: 'geometry:point' } : c);
      }
      break;
    }
    case 'convex_hull':
    case 'geo_convex_hull':
    case 'geo_envelope':
    case 'geo_point_on_surface':
    case 'geo_boundary': {
      const defaults = {
        convex_hull: 'convex_hull',
        geo_convex_hull: 'convex_hull',
        geo_envelope: 'envelope',
        geo_point_on_surface: 'point_on_surface',
        geo_boundary: 'boundary',
      };
      const outCol = config.output_column || defaults[transform_op];
      if (outCol !== (config.geom_column || 'geometry') && !result.find(c => c.name === outCol)) {
        result.push({ name: outCol, type: 'geometry' });
      }
      break;
    }
    case 'geo_area': {
      const outCol = config.output_column || 'area_m2';
      if (!result.find(c => c.name === outCol)) {
        result.push({ name: outCol, type: 'real' });
      }
      break;
    }
    case 'geo_distance': {
      const outCol = config.output_column || 'distance_m';
      if (!result.find(c => c.name === outCol)) {
        result.push({ name: outCol, type: 'real' });
      }
      break;
    }
    case 'geo_length': {
      const outCol = config.output_column || 'length_m';
      if (!result.find(c => c.name === outCol)) {
        result.push({ name: outCol, type: 'real' });
      }
      break;
    }
    case 'geo_perimeter': {
      const outCol = config.output_column || 'perimeter_m';
      if (!result.find(c => c.name === outCol)) {
        result.push({ name: outCol, type: 'real' });
      }
      break;
    }
    case 'geo_vertex_count': {
      const outCol = config.output_column || 'vertex_count';
      if (!result.find(c => c.name === outCol)) {
        result.push({ name: outCol, type: 'int' });
      }
      break;
    }
    
    case 'extract_coordinates': {
      const latCol = config.lat_column || 'lat';
      const lonCol = config.lon_column || 'lon';
      if (!result.find(c => c.name === latCol)) {
        result.push({ name: latCol, type: 'real' });
      }
      if (!result.find(c => c.name === lonCol)) {
        result.push({ name: lonCol, type: 'real' });
      }
      break;
    }
    
    case 'filter':
    case 'sort':
    case 'distinct':
    case 'limit':
    case 'text_normalize':
    case 'hash':
    case 'mask':
    case 'generalize':
    default:
      // Nessuna modifica alle colonne
      break;
  }
  
  return result;
}

/**
 * Calcola le colonne OUTPUT di un multi_join
 * Simula il merge del backend: rinomina colonne duplicate con _2, _3, etc.
 * 
 * @param {Object} node - Nodo multi_join
 * @param {Array<Array>} sourcesColumns - Array di colonne per ogni sorgente
 * @returns {Array} Colonne output dopo il merge
 */
function computeMultiJoinOutput(node, sourcesColumns) {
  if (!sourcesColumns || sourcesColumns.length === 0) {
    return [];
  }
  
  if (sourcesColumns.length === 1) {
    return [...sourcesColumns[0]];
  }
  
  const config = node.data?.config || {};
  const joinPairs = Array.isArray(config.join_pairs)
    ? config.join_pairs.filter(pair => pair && pair.left && pair.right)
    : [];
  const pairMode = sourcesColumns.length === 2 && joinPairs.length > 0;
  const leftKeys = pairMode
    ? joinPairs.map(pair => pair.left)
    : resolveColumnNameList(
        sourcesColumns.flat(),
        config.join_key_ids,
        config.join_keys || []
      );
  const rightKeys = pairMode
    ? joinPairs.map(pair => pair.right)
    : leftKeys;
  const joinKeys = leftKeys;
  const leftKeySet = new Set(leftKeys);
  const rightKeySet = new Set(rightKeys);
  
  // Risultato finale
  const result = [];
  const addedNames = new Set();
  
  // 1. Prima aggiungi le chiavi di join (una sola volta)
  joinKeys.forEach(key => {
    // Cerca la colonna in qualsiasi sorgente
    for (const cols of sourcesColumns) {
      const col = cols.find(c => c.name === key);
      if (col && !addedNames.has(key)) {
        result.push({ ...col });
        addedNames.add(key);
        break;
      }
    }
  });
  
  // 2. Poi aggiungi le altre colonne, rinominando i duplicati
  sourcesColumns.forEach((cols, sourceIdx) => {
    const suffix = sourceIdx >= 1 ? `_${sourceIdx + 1}` : '';
    
    cols.forEach(col => {
      // Skip chiavi di join (già aggiunte)
      const keySet = pairMode && sourceIdx === 1 ? rightKeySet : leftKeySet;
      if (keySet.has(col.name)) {
        return;
      }
      
      let finalName = col.name;
      
      // Se è un duplicato (già esiste una colonna con questo nome)
      if (addedNames.has(col.name)) {
        // Aggiungi suffisso
        finalName = `${col.name}${suffix || '_2'}`;
        
        // Se anche il nome con suffisso esiste, incrementa
        let counter = sourceIdx + 1;
        while (addedNames.has(finalName)) {
          counter++;
          finalName = `${col.name}_${counter}`;
        }
      }
      
      result.push({
        ...col,
        name: finalName,
        type: col.type || 'str'
      });
      addedNames.add(finalName);
    });
  });
  
  
  return result;
}

/**
 * Calcola le colonne OUTPUT di un join a 2 tabelle
 * Simula il merge del backend: aggiunge suffissi _L e _R a TUTTE le colonne (tranne la chiave)
 * 
 * @param {Array} leftCols - Colonne della tabella sinistra
 * @param {Array} rightCols - Colonne della tabella destra
 * @param {string|string[]} leftOn - Colonne chiave sinistre
 * @param {string|string[]} rightOn - Colonne chiave destre
 * @returns {Array} Colonne output dopo il join
 */
function computeJoinOutput(leftCols, rightCols, leftOn, rightOn) {
  const leftKeys = Array.isArray(leftOn) ? leftOn.filter(Boolean) : (leftOn ? [leftOn] : []);
  const rightKeys = Array.isArray(rightOn) ? rightOn.filter(Boolean) : (rightOn ? [rightOn] : []);
  const leftKeySet = new Set(leftKeys);
  const rightKeySet = new Set(rightKeys);
  const result = [];
  
  // Aggiungi colonne LEFT con suffisso _L (tranne la chiave)
  leftCols.forEach(col => {
    if (leftKeySet.has(col.name)) {
      // La chiave di join mantiene il nome originale
      result.push({ ...col, name: col.name, type: col.type || 'str', source: 'left' });
    } else {
      // Tutte le altre colonne ricevono suffisso _L
      result.push({ ...col, name: `${col.name}_L`, type: col.type || 'str', source: 'left' });
    }
  });
  
  // Aggiungi colonne RIGHT con suffisso _R (tranne la chiave)
  rightCols.forEach(col => {
    if (rightKeySet.has(col.name)) {
      // La chiave di join destra viene ignorata se diversa da leftOn (backend la droppa)
      // Se uguale a leftOn, è già stata aggiunta
      return;
    }
    // Tutte le altre colonne ricevono suffisso _R
    result.push({ ...col, name: `${col.name}_R`, type: col.type || 'str', source: 'right' });
  });
  
  return result;
}

function columnKey(col) {
  return col?.id || col?.column_id || null;
}

function syncConfigColumnReferences(config, columns) {
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    return config;
  }

  let changed = false;
  const next = { ...config };
  const byId = new Map(
    (columns || [])
      .map(col => [columnKey(col), col])
      .filter(([key]) => Boolean(key))
  );

  Object.entries(config).forEach(([key, refs]) => {
    if (!key.endsWith('_column_refs') || !Array.isArray(refs)) return;
    const expressionKey = key.slice(0, -12);
    const expression = next[expressionKey];
    if (typeof expression !== 'string') return;

    let nextExpression = expression;
    const nextRefs = [];
    refs.forEach(ref => {
      if (!ref?.id) return;
      const current = byId.get(ref.id);
      if (!current?.name) return;
      nextExpression = rewriteExpressionToken(
        nextExpression,
        ref.name,
        current.name
      );
      nextRefs.push({ id: ref.id, name: current.name });
    });

    if (nextExpression !== expression) {
      next[expressionKey] = nextExpression;
      changed = true;
    }
    if (JSON.stringify(nextRefs) !== JSON.stringify(refs)) {
      next[key] = nextRefs;
      changed = true;
    }
  });

  ['formula'].forEach(expressionKey => {
    const expression = next[expressionKey];
    if (typeof expression !== 'string' || !expression) return;
    const refsKey = `${expressionKey}_column_refs`;
    if (Array.isArray(next[refsKey]) && next[refsKey].length > 0) return;

    let nextExpression = expression;
    legacyNameMap(columns).forEach((currentName, legacyName) => {
      nextExpression = rewriteExpressionToken(
        nextExpression,
        legacyName,
        currentName
      );
    });

    const refs = collectExpressionRefs(nextExpression, columns);
    if (nextExpression !== expression) {
      next[expressionKey] = nextExpression;
      changed = true;
    }
    if (refs.length > 0) {
      next[refsKey] = refs;
      changed = true;
    }
  });

  return changed ? next : config;
}

function legacyNameMap(columns) {
  const candidates = new Map();
  (columns || []).forEach(col => {
    if (!col?.name) return;
    [col.source_name, col.display_name].forEach(oldName => {
      if (!oldName || oldName === col.name) return;
      const key = String(oldName);
      const currentValue = candidates.get(key);
      if (currentValue && currentValue !== col.name) {
        candidates.set(key, null);
      } else if (!candidates.has(key)) {
        candidates.set(key, String(col.name));
      }
    });
  });
  for (const [key, value] of [...candidates.entries()]) {
    if (!value) candidates.delete(key);
  }
  return candidates;
}

function collectExpressionRefs(expression, columns) {
  if (!expression || typeof expression !== 'string') return [];
  return (columns || [])
    .filter(col => {
      if (!columnKey(col)) return false;
      const names = [col.name, col.source_name, col.display_name].filter(Boolean);
      return names.some(name => expressionHasToken(expression, String(name)));
    })
    .map(col => ({ id: columnKey(col), name: col.name }));
}

function expressionHasToken(expression, token) {
  if (!expression || !token || typeof expression !== 'string') return false;
  if (expression.includes(`\`${token}\``)) return true;
  if (expression.includes(`['${token}']`)) return true;
  if (expression.includes(`["${token}"]`)) return true;
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(token)) {
    return new RegExp(`\\b${escapeRegExp(token)}\\b`).test(expression);
  }
  return expression.includes(token);
}

function rewriteExpressionToken(expression, oldName, newName) {
  if (!expression || !oldName || !newName || oldName === newName) return expression;
  let rewritten = expression
    .replaceAll(`\`${oldName}\``, `\`${newName}\``)
    .replaceAll(`['${oldName}']`, `['${newName}']`)
    .replaceAll(`["${oldName}"]`, `["${newName}"]`);
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(oldName)) {
    rewritten = rewritten.replace(
      new RegExp(`\\b${escapeRegExp(oldName)}\\b`, 'g'),
      newName
    );
  }
  return rewritten;
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function resolveColumnName(columns, columnId, fallbackName) {
  if (columnId) {
    const found = columns.find(col => columnKey(col) === columnId);
    if (found?.name) return found.name;
  }
  return fallbackName;
}

function resolveColumnNameList(columns, columnIds, fallbackNames) {
  const names = Array.isArray(fallbackNames) ? fallbackNames : [];
  if (!Array.isArray(columnIds) || columnIds.length === 0) {
    return names;
  }
  return columnIds
    .map((columnId, idx) => resolveColumnName(columns, columnId, names[idx]))
    .filter(Boolean);
}

export default useColumnPropagation;
