/**
 * columnUtils.js
 * Utility per calcolare e propagare le colonne attraverso il grafo
 * Include supporto per i tipi colonna (int, real, str, bool, date, geometry:*)
 */

/**
 * Normalizza il tipo colonna per consistenza tra i vari componenti
 * Supporta sia il vecchio formato (dtype: num/str/date/bool) 
 * che il nuovo (type: int/real/str/bool/date/geometry:*)
 * 
 * NOTA: Questa funzione era duplicata in ProjectSourceNode, FileSourceNode, ArricchisciEditor
 * 
 * @param {Object|string} col - Colonna con proprietà type/dtype o stringa del tipo
 * @returns {string} Tipo normalizzato
 * 
 * Uso:
 * ```jsx
 * const type = normalizeColumnType(col);
 * // oppure
 * const type = normalizeColumnType({ type: 'number' });
 * ```
 */
export function normalizeColumnType(col) {
  // Se è una stringa, usala direttamente
  const rawType = typeof col === 'string' 
    ? col 
    : (col.type || col.dtype || 'str');
  
  // Mappa vecchi valori ai nuovi
  const typeMap = {
    'num': 'real',        // vecchio 'num' -> nuovo 'real'
    'number': 'real',
    'integer': 'int',
    'float': 'real',
    'double': 'real',
    'string': 'str',
    'text': 'str',
    'boolean': 'bool',
    'datetime': 'date',
    'timestamp': 'date',
  };
  
  const normalized = typeMap[rawType.toLowerCase()] || rawType.toLowerCase();
  return normalized;
}

/**
 * Mappa il tipo dell'API ai tipi standard del sistema
 * Usato principalmente in ApiEnrichNode
 * 
 * @param {string} apiType - Tipo restituito dall'API
 * @returns {string} Tipo standard
 */
export function mapApiTypeToStandard(apiType) {
  const typeMap = {
    'string': 'str',
    'text': 'str',
    'number': 'real',
    'integer': 'int',
    'float': 'real',
    'double': 'real',
    'boolean': 'bool',
    'bool': 'bool',
    'date': 'date',
    'datetime': 'date',
    'geojson': 'geometry',
    'geometry': 'geometry',
    'point': 'geometry:point',
    'polygon': 'geometry:polygon',
    'multipolygon': 'geometry:multipolygon',
    'linestring': 'geometry:line',
  };
  return typeMap[(apiType || 'string').toLowerCase()] || 'str';
}

/**
 * Calcola le colonne finali che arrivano al nodo risultato
 * attraversando il grafo dei nodi e degli edges
 */
export function calculateOutputColumns(nodes, edges, resultNodeId) {
  // Trova il nodo risultato
  const resultNode = nodes.find(n => n.id === resultNodeId);
  if (!resultNode) return [];

  // Trova tutti gli edges che arrivano al nodo risultato
  const incomingEdges = edges.filter(e => e.target === resultNodeId);
  if (incomingEdges.length === 0) return [];

  // Raccoglie tutte le colonne dai nodi collegati
  const allColumns = [];

  // Per ogni edge in ingresso
  for (const edge of incomingEdges) {
    const sourceNode = nodes.find(n => n.id === edge.source);
    if (!sourceNode) continue;

    // Se è un nodo tabella, prende le colonne visibili
    if (sourceNode.type === 'tableNode') {
      const visibleColumns = (sourceNode.data.columns || [])
        .filter(col => col.visible)
        .map(col => ({
          name: col.name,
          alias: col.alias,
          type: col.type || 'str',
          source_table: sourceNode.data.alias || sourceNode.data.table_name,
          full_name: `${sourceNode.data.alias || sourceNode.data.table_name}.${col.alias || col.name}`
        }));
      
      allColumns.push(...visibleColumns);
    }
  }

  // Rimuovi duplicati in base a full_name
  const uniqueColumns = allColumns.reduce((acc, col) => {
    if (!acc.find(c => c.full_name === col.full_name)) {
      acc.push(col);
    }
    return acc;
  }, []);

  return uniqueColumns;
}

/**
 * Traversa il grafo ricorsivamente per trovare tutte le colonne
 * che contribuiscono al risultato finale
 */
export function calculateOutputColumnsRecursive(nodes, edges, resultNodeId) {
  const visitedNodes = new Set();
  const allColumns = [];

  function traverse(nodeId) {
    if (visitedNodes.has(nodeId)) return;
    visitedNodes.add(nodeId);

    const node = nodes.find(n => n.id === nodeId);
    if (!node) return;

    // Se è un nodo tabella, raccoglie le colonne visibili
    if (node.type === 'tableNode') {
      const tableName = node.data.alias || node.data.table_name;
      
      // Colonne normali visibili
      const visibleColumns = (node.data.columns || [])
        .filter(col => col.visible)
        .map(col => ({
          name: col.name,
          alias: col.alias,
          type: col.type || 'str',
          source_table: tableName,
          full_name: `${tableName}.${col.alias || col.name}`
        }));
      
      allColumns.push(...visibleColumns);
      
      // Colonne calcolate
      const calculatedColumns = (node.data.calculated_columns || [])
        .map(col => ({
          name: col.name,
          alias: null,
          type: col.output_type || 'str',
          source_table: tableName,
          full_name: `${tableName}.${col.name}`,
          isCalculated: true
        }));
      
      allColumns.push(...calculatedColumns);
    }

    // Trova tutti gli edges che arrivano a questo nodo
    const incomingEdges = edges.filter(e => e.target === nodeId);
    
    // Traversa ricorsivamente i nodi sorgente
    for (const edge of incomingEdges) {
      traverse(edge.source);
    }
  }

  // Inizia la traversata dal nodo risultato
  traverse(resultNodeId);

  // Rimuovi duplicati in base a full_name
  const uniqueColumns = allColumns.reduce((acc, col) => {
    if (!acc.find(c => c.full_name === col.full_name)) {
      acc.push(col);
    }
    return acc;
  }, []);

  return uniqueColumns;
}

/**
 * Helper: Mappa tipo colonna -> icona emoji
 * Può essere usato in qualsiasi componente
 * Gestisce formato con SRID: geometry:polygon:4326
 */
export function getColumnTypeIcon(type) {
  if (!type) return '📝'; // default string
  
  const typeStr = type.toLowerCase();
  
  // Geometry types (formato: geometry:subtype:srid)
  if (typeStr.startsWith('geometry')) {
    if (typeStr.includes('point')) return '📍';
    if (typeStr.includes('line')) return '〰️';
    if (typeStr.includes('polygon') || typeStr.includes('multi')) return '⬡';
    return '🌐'; // generic geometry
  }
  
  // Base types
  const iconMap = {
    'int': '🔢',
    'real': '🔣',
    'str': '📝',
    'bool': '☑️',
    'date': '📅',
  };
  
  return iconMap[typeStr] || '📝';
}

/**
 * Helper: Mappa tipo colonna -> label leggibile
 * Gestisce formato con SRID: geometry:polygon:4326
 */
export function getColumnTypeLabel(type) {
  if (!type) return 'Testo';
  
  const typeStr = type.toLowerCase();
  
  // Gestisci geometrie con SRID (es. geometry:polygon:4326)
  if (typeStr.startsWith('geometry')) {
    const parts = typeStr.split(':');
    const subtype = parts[1] || '';
    const srid = parts[2] || '';
    
    const subtypeLabels = {
      'point': 'Punto',
      'line': 'Linea',
      'polygon': 'Poligono',
      'multipolygon': 'Multi-Poligono',
      'multipoint': 'Multi-Punto',
      'multiline': 'Multi-Linea',
      'geometry': 'Geometria',
      'geography': 'Geografia',
    };
    
    const label = subtypeLabels[subtype] || 'Geometria';
    
    // Aggiungi SRID se presente
    if (srid) {
      return `${label} (EPSG:${srid})`;
    }
    return label;
  }
  
  const labelMap = {
    'int': 'Intero',
    'real': 'Decimale',
    'str': 'Testo',
    'bool': 'Booleano',
    'date': 'Data/Ora',
  };
  
  return labelMap[typeStr] || type;
}

/**
 * Helper: Estrae SRID dal tipo colonna
 * Es: geometry:polygon:4326 -> 4326
 * Ritorna null se non presente
 */
export function getColumnSRID(type) {
  if (!type) return null;
  
  const typeStr = type.toLowerCase();
  if (!typeStr.startsWith('geometry')) return null;
  
  const parts = typeStr.split(':');
  if (parts.length >= 3) {
    const srid = parseInt(parts[2], 10);
    return isNaN(srid) ? null : srid;
  }
  
  return null;
}

/**
 * Helper: Verifica se il tipo è una geometria
 */
export function isGeometryType(type) {
  if (!type) return false;
  return type.toLowerCase().startsWith('geometry');
}

/**
 * Estrae colonne visibili con formato standard
 * Utile per raccogliere colonne da nodi di tipo diverso
 * 
 * @param {Object} node - Nodo React Flow
 * @returns {Array} Array di colonne normalizzate
 */
export function extractVisibleColumns(node) {
  if (!node?.data?.columns) return [];
  
  return node.data.columns
    .filter(col => col.visible !== false)
    .map(col => {
      if (typeof col === 'string') {
        return { name: col, type: 'str', visible: true };
      }
      return {
        name: col.name || col.alias || 'unknown',
        type: normalizeColumnType(col),
        visible: col.visible !== false,
        alias: col.alias || null
      };
    });
}

/**
 * Deduplica array di colonne per nome
 * Mantiene l'ultima occorrenza (che potrebbe avere tipo più preciso)
 * 
 * @param {Array} columns - Array di colonne
 * @returns {Array} Array deduplicato
 */
export function deduplicateColumns(columns) {
  return [...new Map(columns.map(col => [col.name, col])).values()];
}
