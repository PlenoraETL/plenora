/**
 * shares.js — API client per allowed_groups (cross-group sharing)
 *
 * Endpoint backend: /api/shares/{resource_type}/{resource_id}
 *
 * resource_type validi:
 *   connections | datasources | eseguibili | api_connections | arcgis_connections
 *
 * Il valore magic "*" indica condivisione con tutti i gruppi.
 */
import http from './http';

export const VALID_RESOURCE_TYPES = [
  'connections',
  'datasources',
  'eseguibili',
  'api_connections',
  'arcgis_connections',
];

/**
 * Lista i group_id con cui la risorsa è condivisa.
 * @returns {Promise<{resource_type, resource_id, group_ids: string[]}>}
 */
export const getShares = (resourceType, resourceId) =>
  http.get(`/shares/${resourceType}/${resourceId}`).then(r => r.data);

/**
 * Sostituisce TUTTE le righe di sharing per la risorsa.
 * Pass `['*']` per condivisione globale, `[]` per rimuovere tutte.
 */
export const setShares = (resourceType, resourceId, groupIds) =>
  http.put(`/shares/${resourceType}/${resourceId}`, { group_ids: groupIds }).then(r => r.data);

/**
 * Aggiunge un singolo gruppo. Idempotente.
 */
export const addShare = (resourceType, resourceId, groupId) =>
  http.post(`/shares/${resourceType}/${resourceId}/${groupId}`).then(r => r.data);

/**
 * Rimuove una singola riga di sharing.
 */
export const removeShare = (resourceType, resourceId, groupId) =>
  http.delete(`/shares/${resourceType}/${resourceId}/${groupId}`).then(r => r.data);
