/**
 * api/transforms.js — API per le trasformazioni e le funzioni SQL
 *
 * Espone gli endpoint del backend per:
 *   - Registry delle operazioni di trasformazione disponibili (MANIPOLA)
 *   - Catalogo delle funzioni SQL custom
 *   - Traduzione funzione in SQL
 *
 * Usato da:
 *   - components/transforms/TransformNode.jsx (registry)
 *   - components/modals/ColumnBuilderModal.jsx (functions)
 */

import http from './http';

/**
 * Restituisce il registry completo delle operazioni di trasformazione disponibili.
 * Il risultato viene cachato localmente nel componente consumer.
 * @returns {Promise<Object>} Registry: { [category]: { [op_name]: OperationDef } }
 */
export const getTransformRegistry = async () => {
  const response = await http.get('/transforms/registry');
  return response.data;
};

/**
 * Restituisce il catalogo delle funzioni SQL disponibili nel ColumnBuilder.
 * @returns {Promise<Array<FunctionDef>>}
 */
export const getFunctionsCatalog = async () => {
  const response = await http.get('/functions/catalog');
  return response.data;
};

/**
 * Traduce una configurazione di funzione in un'espressione SQL.
 * @param {Object} payload
 * @param {string}   payload.function_name
 * @param {Object[]} payload.arguments
 * @param {string}   payload.dialect   - 'postgresql'|'oracle'|'mssql'|'mysql'
 * @returns {Promise<{sql: string}>}
 */
export const translateFunction = async (payload) => {
  const response = await http.post('/functions/translate', payload);
  return response.data;
};
