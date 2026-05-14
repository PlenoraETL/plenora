/**
 * api/carica.js — API per il modulo CARICA (scrittura su database)
 *
 * Espone gli endpoint del backend per:
 *   - Elenco schemi disponibili su una connessione di destinazione
 *   - Elenco tabelle di uno schema
 *   - Elenco colonne di una tabella
 *   - Auto-mapping tra colonne sorgente e colonne destinazione
 *
 * Usato da: components/nodes/CaricanNode.jsx
 */

import http from './http';

/**
 * Restituisce gli schemi disponibili per una connessione.
 * @param {string|number} connId
 * @returns {Promise<string[]>}
 */
export const getCaricaSchemas = async (connId) => {
  const response = await http.get(`/carica/schemas/${connId}`);
  return response.data;
};

/**
 * Restituisce le tabelle di uno schema.
 * @param {string|number} connId
 * @param {string} schema
 * @returns {Promise<string[]>}
 */
export const getCaricaTables = async (connId, schema) => {
  const response = await http.get(`/carica/tables/${connId}/${schema}`);
  return response.data;
};

/**
 * Calcola un auto-mapping tra colonne sorgente e colonne di destinazione.
 * @param {Object} payload
 * @param {string[]} payload.source_columns   - Colonne output della pipeline
 * @param {string}   payload.target_conn_id   - ID connessione destinazione
 * @param {string}   payload.target_schema    - Schema destinazione
 * @param {string}   payload.target_table     - Tabella destinazione
 * @returns {Promise<Object>} { mappings: Array<{source_column, dest_column, dest_type, is_geometry, srid, is_primary_key, skip}>, suggested_match_columns, existing_columns }
 */
export const postCaricaAutoMapping = async (payload) => {
  const response = await http.post('/carica/auto-mapping', payload);
  return response.data;
};

/**
 * POST /api/carica/check-conflicts
 * Verifica conflitti pre-scrittura (per modalità append/upsert/update).
 * Il backend carica i dati dal result store del progetto e li confronta
 * con la tabella destinazione sulle colonne match.
 *
 * @param {Object} params
 * @param {string} params.connection_id
 * @param {string} params.schema_name
 * @param {string} params.table_name
 * @param {string[]} params.match_columns
 * @param {string} [params.project_id]       - Carica dati dal result store
 * @param {Object[]} [params.sample_values]  - Alternativa a project_id
 * @returns {Promise<{has_conflicts, total_conflicts, internal_duplicates, db_conflicts, details}>}
 */
export const postCaricaCheckConflicts = async (params) => {
  const response = await http.post('/carica/check-conflicts', params);
  return response.data;
};
