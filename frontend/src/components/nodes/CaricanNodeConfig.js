/**
 * Configurazione e export per CaricanNode
 * 
 * Questo file viene importato in index.js della cartella nodes
 * per registrare il nodo nel sistema ReactFlow.
 */

// Configurazione default per un nuovo nodo CARICA
export const caricanNodeDefaults = {
  // Tipo destinazione
  dest_type: 'db', // 'db' | 'arcgis'
  
  // --- DB ---
  connection_id: '',
  schema_name: '',
  table_name: '',
  write_mode: 'create',
  column_mappings: [],
  match_columns: [],
  batch_size: 1000,
  transaction_mode: 'all_or_nothing',
  create_spatial_index: true,
  conflict_policy: 'abort',
  
  // --- ArcGIS ---
  arcgis_connection_id: '',
  arcgis_service_url: '',
  arcgis_layer_url: '',
  arcgis_operation: 'add',      // add | update | delete | create_service
  arcgis_new_service_name: '',
  arcgis_new_layer_name: 'Layer 0',
};

// Tipo nodo per ReactFlow
export const CARICA_NODE_TYPE = 'carica';

// Factory per creare un nuovo nodo CARICA
export const createCaricanNode = (position = { x: 0, y: 0 }, id = null) => ({
  id: id || `carica_${Date.now()}`,
  type: CARICA_NODE_TYPE,
  position,
  data: {
    ...caricanNodeDefaults,
    label: 'Carica'
  }
});

// Validazione configurazione nodo CARICA
export const validateCaricanConfig = (data) => {
  const errors = [];
  
  if (data.dest_type === 'arcgis') {
    // Validazione ArcGIS
    if (!data.arcgis_connection_id) {
      errors.push('Connessione ArcGIS non selezionata');
    }
    
    if (data.arcgis_operation === 'create_service') {
      if (!data.arcgis_new_service_name) {
        errors.push('Nome nuovo servizio non specificato');
      }
    } else {
      if (!data.arcgis_layer_url) {
        errors.push('Layer ArcGIS non selezionato');
      }
      // Per update/delete serve OBJECTID nelle colonne sorgente
      if (['update', 'delete'].includes(data.arcgis_operation)) {
        const cols = data.availableColumns || [];
        const hasOid = cols.some(c => (c.name || c).toUpperCase() === 'OBJECTID');
        if (!hasOid) {
          errors.push(`OBJECTID richiesto per operazione ${data.arcgis_operation}`);
        }
      }
    }
  } else {
    // Validazione DB
    if (!data.connection_id) {
      errors.push('Connessione database non selezionata');
    }
    if (!data.schema_name) {
      errors.push('Schema non selezionato');
    }
    if (!data.table_name) {
      errors.push('Nome tabella non specificato');
    }
    if (['update', 'upsert'].includes(data.write_mode)) {
      if (!data.match_columns || data.match_columns.length === 0) {
        errors.push('Colonne match richieste per UPDATE/UPSERT');
      }
    }
  }
  
  return {
    isValid: errors.length === 0,
    errors
  };
};

export { default } from './CaricanNode';