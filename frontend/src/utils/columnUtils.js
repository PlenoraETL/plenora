
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
 * Una colonna e' nella SELECT se non e' esplicitamente nascosta: stessa
 * regola del backend (canvas_column: visible di default True). Unica fonte
 * per conteggi, anteprima della query, colonne dell'Output e griglia.
 */
export const isColumnVisible = (col) => col?.visible !== false;

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
 * Deduplica array di colonne per nome
 * Mantiene l'ultima occorrenza (che potrebbe avere tipo più preciso)
 * 
 * @param {Array} columns - Array di colonne
 * @returns {Array} Array deduplicato
 */
export function deduplicateColumns(columns) {
  return [...new Map(columns.map(col => [col.name, col])).values()];
}
