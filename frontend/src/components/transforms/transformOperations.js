/**
 * transformOperations.js
 * Lista operazioni disponibili e utility functions
 */

export const operations = [
  // Base
  { value: 'join', label: 'Join (Merge)', color: '#2196F3', category: 'base' },
  { value: 'formula', label: 'Formula (Calcolo)', color: '#4CAF50', category: 'base' },
  { value: 'aggregate', label: 'Aggregazione (GroupBy)', color: '#FF9800', category: 'base' },
  { value: 'filter', label: 'Filter (Filtra)', color: '#9C27B0', category: 'base' },
  { value: 'concat', label: 'Concat (Unisci)', color: '#F44336', category: 'base' },
  { value: 'sort', label: 'Sort (Ordina)', color: '#607D8B', category: 'base' },
  { value: 'distinct', label: 'Distinct (Univoci)', color: '#795548', category: 'base' },
  { value: 'rename', label: 'Rinomina Colonne', color: '#00BCD4', category: 'base' },
  { value: 'drop_columns', label: 'Elimina Colonne', color: '#FF5722', category: 'base' },
  { value: 'fill_na', label: 'Riempi Nulli', color: '#8BC34A', category: 'base' },
  { value: 'replace', label: 'Sostituisci Valori', color: '#673AB7', category: 'base' },
  { value: 'split_column', label: 'Dividi Colonna', color: '#009688', category: 'base' },
  // Spaziali
  { value: 'sjoin', label: 'Spatial Join', color: '#E91E63', category: 'spatial' },
  { value: 'geo_buffer', label: 'Buffer', color: '#E91E63', category: 'spatial' },
  { value: 'geo_centroid', label: 'Centroide', color: '#E91E63', category: 'spatial' },
  { value: 'geo_area', label: 'Area', color: '#E91E63', category: 'spatial' },
  { value: 'geo_clip', label: 'Clip', color: '#E91E63', category: 'spatial' },
  { value: 'geo_dissolve', label: 'Dissolve', color: '#E91E63', category: 'spatial' },
  { value: 'geo_intersection', label: 'Intersezione', color: '#E91E63', category: 'spatial' },
  { value: 'geo_union', label: 'Unione Geom.', color: '#E91E63', category: 'spatial' },
  { value: 'geo_difference', label: 'Differenza', color: '#E91E63', category: 'spatial' },
  { value: 'geo_simplify', label: 'Semplifica', color: '#E91E63', category: 'spatial' },
  { value: 'geo_convex_hull', label: 'Convex Hull', color: '#E91E63', category: 'spatial' },
  { value: 'geo_reproject', label: 'Riproietta', color: '#E91E63', category: 'spatial' }
];

export const getOperationsByCategory = (category) => 
  operations.filter(op => op.category === category);

export const getOperationByValue = (value) => 
  operations.find(op => op.value === value);

export const getCategoryForOperation = (opValue) => {
  const op = operations.find(o => o.value === opValue);
  return op?.category || 'base';
};

// Operazioni che richiedono 2 input
export const twoInputOperations = [
  'join', 'sjoin', 'geo_clip', 'geo_intersection', 'geo_union', 'geo_difference'
];

// Operazioni che richiedono 1 input con geometria
export const singleInputSpatialOps = [
  'geo_buffer', 'geo_centroid', 'geo_area', 'geo_dissolve', 
  'geo_simplify', 'geo_convex_hull', 'geo_reproject'
];

// Operazioni che richiedono 2 input con geometria
export const twoInputSpatialOps = [
  'sjoin', 'geo_clip', 'geo_intersection', 'geo_union', 'geo_difference'
];

/**
 * Inferisce il tipo di una colonna calcolata dalla formula
 */
export const inferFormulaType = (formula) => {
  if (!formula) return 'str';
  
  const formulaLower = formula.toLowerCase();
  
  // Operazioni stringa
  if (formulaLower.includes('.str.') || 
      formulaLower.includes('+ "') || 
      formulaLower.includes("+ '")) {
    return 'str';
  }
  
  // Operazioni booleane
  if (formulaLower.includes(' == ') || 
      formulaLower.includes(' != ') ||
      formulaLower.includes(' > ') ||
      formulaLower.includes(' < ') ||
      formulaLower.includes(' and ') ||
      formulaLower.includes(' or ')) {
    return 'bool';
  }
  
  // Operazioni matematiche -> default real
  if (formula.includes('+') || 
      formula.includes('-') || 
      formula.includes('*') || 
      formula.includes('/')) {
    return 'real';
  }
  
  return 'str';
};

/**
 * Helper per ottenere il tipo di una colonna
 */
export const getColType = (col) => col.type || col.dtype || 'str';

/**
 * Filtra colonne geometriche
 */
export const filterGeometryColumns = (columns) => {
  return (columns || []).filter(c => {
    const colType = (c.type || c.dtype || '').toLowerCase();
    return colType.startsWith('geometry') && c.visible !== false;
  });
};

/**
 * Filtra colonne visibili
 */
export const filterVisibleColumns = (columns) => {
  return (columns || []).filter(c => c.visible !== false);
};
