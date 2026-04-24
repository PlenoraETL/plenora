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

export { default } from './CaricanNode';