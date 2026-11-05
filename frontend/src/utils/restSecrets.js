/**
 * restSecrets.js — i segreti della destinazione REST non stanno nel canvas
 * (Fase 5 T1, audit R10.8).
 *
 * Regola unica: le credenziali di una scrittura REST si danno con un
 * RIFERIMENTO a un connettore API (`rest_auth_api_id`), che il server
 * conserva cifrato e risolve al momento della scrittura. Un'intestazione
 * marcata segreta (canvas vecchi) o con il nome di una credenziale
 * (Authorization, X-API-Key, Cookie, *token*, *secret*, ...) non si salva:
 * la toglie persistedNodeData (utils/nodeTypeRegistry), l'unico punto da cui
 * passa il node.data salvato, versionato e copiato in locale.
 */
export const REST_AUTH_FIELD = 'rest_auth_api_id';

const SECRET_HEADER_NAME = /^(authorization|proxy-authorization|cookie|set-cookie)$|api[-_]?key|token|secret|password|passwd|credential|session/i;

/** True se l'intestazione porta (o puo' portare) una credenziale. */
export function isSecretHeader(header) {
  if (!header || typeof header !== 'object') return false;
  return header.secret === true || SECRET_HEADER_NAME.test(String(header.name || '').trim());
}

/** Le intestazioni che si possono salvare nel canvas. */
export function persistableRestHeaders(headers) {
  return Array.isArray(headers) ? headers.filter((header) => !isSecretHeader(header)) : headers;
}
