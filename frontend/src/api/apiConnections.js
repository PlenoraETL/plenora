/**
 * apiConnections.js — API per la gestione dei connettori esterni
 *
 * Connettori esterni = API REST, eseguibili Python/Shell, Data Sources (SharePoint, FTP, S3...).
 * Usati dal modulo Arricchisci per arricchire i dataset con dati esterni.
 *
 * Precedentemente queste funzioni erano definite direttamente in ApiConnectionManager.jsx.
 */

import http from './http';

/**
 * Restituisce tutti i connettori esterni configurati.
 *
 * @param {Object} filters
 * @param {string} filters.category        - Filtra per categoria (geocoding, routing, ml_model, ...)
 * @param {string} filters.connection_type - Filtra per tipo (manual, template, openapi, executable, datasource)
 * @returns {Promise<Object>} { connections: [...] }
 */
export const listApiConnections = async (filters = {}) => {
  const params = new URLSearchParams();
  if (filters.category)        params.append('category',        filters.category);
  if (filters.connection_type) params.append('connection_type', filters.connection_type);

  const query = params.toString();
  const url   = query ? `/api-connections?${query}` : '/api-connections';

  const response = await http.get(url);
  return response.data;
};

