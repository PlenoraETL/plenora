/**
 * api/templates.js — API per template API predefiniti
 *
 * Espone gli endpoint del backend per il catalogo template API pronti
 * all'uso (geocoding, catasto, ML, ...). I template pre-compilano la
 * configurazione di un connettore API.
 *
 * Usato da:
 *   - components/arricchisci/TemplateLibraryModal.jsx
 */

import http from './http';

/**
 * Restituisce la lista dei template API disponibili nel catalogo.
 * @returns {Promise<Array<TemplateDef>>}
 */
export const getTemplates = async () => {
  const response = await http.get('/templates');
  return response.data;
};
