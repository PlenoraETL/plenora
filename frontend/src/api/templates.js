/**
 * api/templates.js — API per template API predefiniti ed eseguibili
 *
 * Espone gli endpoint del backend per:
 *   - Catalogo template API pronti all'uso (geocoding, catasto, ML, ...)
 *   - Test di un eseguibile esterno (script, binario) prima del salvataggio
 *
 * Usato da:
 *   - components/arricchisci/TemplateLibraryModal.jsx (templates)
 *   - components/arricchisci/ExecutableConfigModal.jsx (executables)
 */

import http from './http';

/**
 * Restituisce la lista dei template API disponibili nel catalogo.
 * I template pre-compilano la configurazione di un connettore API.
 * @returns {Promise<Array<TemplateDef>>}
 */
export const getTemplates = async () => {
  const response = await http.get('/templates');
  return response.data;
};

/**
 * Testa un eseguibile esterno con un payload di esempio.
 * Verifica che l'eseguibile risponda nel formato atteso prima del salvataggio.
 * @param {Object} payload
 * @param {string}   payload.executable_path  - Path o comando dell'eseguibile
 * @param {string}   [payload.interpreter]    - 'python'|'node'|'bash'|null
 * @param {Object}   [payload.test_input]     - Input di esempio per il test
 * @returns {Promise<{success: boolean, output?: any, error?: string, duration_ms?: number}>}
 */
export const testExecutable = async (payload) => {
  const response = await http.post('/executables/test', payload);
  return response.data;
};
