/**
 * Costanti e configurazioni per ResultNode
 * 
 * Centralizza tutte le configurazioni statiche per mantenere
 * il componente principale più leggibile.
 */

// Configurazioni per variante di progetto
export const PROJECT_VARIANTS = {
  estrai: {
    icon: '📁',
    title: 'Output',
    executeLabel: 'Esegui Query',
    accentColor: '#2196F3',
    description: 'Esporta i risultati della query SQL'
  },
  manipola: {
    icon: '🔧',
    title: 'Output Manipola',
    executeLabel: 'Esegui Pipeline',
    accentColor: '#9C27B0',
    description: 'Esporta i dati trasformati'
  },
  arricchisci: {
    icon: '🔗',
    title: 'Output Arricchisci',
    executeLabel: 'Esegui Arricchimento',
    accentColor: '#FF6B35',
    description: 'Esporta i dati arricchiti'
  }
};

// Formati di output disponibili
export const OUTPUT_FORMATS = [
  { value: 'csv', label: 'CSV', icon: '📊', category: 'standard', description: 'Comma-separated values' },
  { value: 'json', label: 'JSON', icon: '📄', category: 'standard', description: 'JavaScript Object Notation' },
  { value: 'xlsx', label: 'XLSX', icon: '📗', category: 'standard', description: 'Microsoft Excel' },
  { value: 'geojson', label: 'GeoJSON', icon: '🌐', category: 'geo', description: 'GeoJSON per dati geografici' },
  { value: 'gpkg', label: 'GeoPackage', icon: '📦', category: 'geo', description: 'GeoPackage SQLite' },
  { value: 'shp', label: 'Shapefile', icon: '🗺️', category: 'geo', description: 'ESRI Shapefile' }
];

// Formati standard (non geografici)
export const STANDARD_FORMATS = OUTPUT_FORMATS.filter(f => f.category === 'standard');

// Formati geografici
export const GEO_FORMATS = OUTPUT_FORMATS.filter(f => f.category === 'geo');

// Funzioni di aggregazione SQL
export const AGGREGATE_FUNCTIONS = [
  { value: 'COUNT', label: 'COUNT', desc: 'Conta', icon: '#️⃣' },
  { value: 'SUM', label: 'SUM', desc: 'Somma', icon: '➕' },
  { value: 'AVG', label: 'AVG', desc: 'Media', icon: '📊' },
  { value: 'MIN', label: 'MIN', desc: 'Minimo', icon: '⬇️' },
  { value: 'MAX', label: 'MAX', desc: 'Massimo', icon: '⬆️' }
];

// Modalità di esecuzione
export const EXECUTION_MODES = {
  cache: {
    value: 'cache',
    title: '⚡ Usa cache esistente',
    description: 'Veloce - carica dati già elaborati (se disponibili)'
  },
  recalculate: {
    value: 'recalculate',
    title: '🔄 Ricalcola tutto',
    descriptions: {
      manipola: 'Aggiornato - riesegue tutte le query SQL',
      arricchisci: 'Aggiornato - riesegue tutte le chiamate API'
    }
  }
};

// Helper: determina la variante da data props
export const getProjectVariant = (data) => {
  if (data.isArricchisci) return 'arricchisci';
  if (data.isManipulate) return 'manipola';
  return 'estrai';
};

// Helper: verifica se il formato è geospaziale
export const isGeoFormat = (format) => {
  return ['geojson', 'gpkg', 'shp'].includes(format);
};

// Helper: ottieni icona formato
export const getFormatIcon = (format) => {
  const found = OUTPUT_FORMATS.find(f => f.value === format);
  return found?.icon || '📄';
};

// Helper: deduplica colonne per nome
export const deduplicateColumns = (columns) => {
  return [...new Map(columns.map(col => [col.name, col])).values()];
};

export default {
  PROJECT_VARIANTS,
  OUTPUT_FORMATS,
  STANDARD_FORMATS,
  GEO_FORMATS,
  AGGREGATE_FUNCTIONS,
  EXECUTION_MODES,
  getProjectVariant,
  isGeoFormat,
  getFormatIcon,
  deduplicateColumns
};
