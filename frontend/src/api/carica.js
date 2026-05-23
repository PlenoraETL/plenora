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
 *
 * Shape contract: il BE ritorna `{schemas: string[], db_type: string}`.
 * Pre-fix l'API wrapper passava `response.data` grezzo: se in futuro il
 * BE cambiasse a `string[]` (o lo facesse un fake-rest in test), il
 * consumer `useCaricaSchemaSelect` faceva `data.schemas || []` e
 * ricadeva silenziosamente a `[]` mascherando il drift. Post-fix
 * normalizziamo SEMPRE alla shape `{schemas, db_type}` qui per garantire
 * un contratto stabile downstream.
 *
 * @param {string|number} connId
 * @returns {Promise<{schemas: string[], db_type: string|null}>}
 */
export const getCaricaSchemas = async (connId) => {
  const response = await http.get(`/carica/schemas/${connId}`);
  const data = response.data;
  if (Array.isArray(data)) {
    return { schemas: data, db_type: null };
  }
  return { schemas: data?.schemas || [], db_type: data?.db_type ?? null };
};

/**
 * Restituisce le tabelle di uno schema.
 *
 * Stessa normalizzazione di `getCaricaSchemas`: shape canonica
 * `{tables: string[]}`.
 *
 * @param {string|number} connId
 * @param {string} schema
 * @returns {Promise<{tables: string[]}>}
 */
export const getCaricaTables = async (connId, schema) => {
  const response = await http.get(`/carica/tables/${connId}/${schema}`);
  const data = response.data;
  if (Array.isArray(data)) {
    return { tables: data };
  }
  return { tables: data?.tables || [] };
};

/**
 * Calcola un auto-mapping tra colonne sorgente e colonne di destinazione.
 *
 * Pre-fix (P1 contracts-schemas): il JSDoc documentava `target_conn_id`/
 * `target_schema`/`target_table` ma il BE `AutoMappingRequest` (vedi
 * `core/carica/router.py:58`) accetta `connection_id`/`schema_name`/
 * `table_name`. Qualunque nuovo caller basato sul JSDoc avrebbe inviato
 * field ignorati silenziosamente dal BE → mapping calcolato su
 * connection vuota. Allineato ai nomi reali del modello BE.
 *
 * @param {Object} payload
 * @param {Array<{name, type}>} payload.source_columns - Colonne output pipeline
 * @param {string}   payload.connection_id  - ID connessione destinazione
 * @param {string}   payload.schema_name    - Schema destinazione (default 'public')
 * @param {string}   payload.table_name     - Tabella destinazione
 * @param {string}   [payload.project_id]   - Project context (opzionale)
 * @param {number}   [payload.srid]         - SRID geometrico (default 4326)
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
