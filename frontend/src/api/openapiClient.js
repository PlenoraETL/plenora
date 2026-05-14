/**
 * openapiClient.js — Loader lazy + cache dello schema OpenAPI BE.
 *
 * Espone l'accesso allo schema `/openapi.json` per contract test FE↔BE
 * cross-stack, validator runtime opzionali, e tooling di drift detect.
 * Niente integrazione automatica in http.js: il loader e' opt-in,
 * solo i caller che lo importano pagano la latenza del fetch (singolo,
 * cache in memoria).
 *
 * Pattern d'uso (contract test):
 *   import { loadOpenApiSchema, getEndpointSchema } from '../../src/api/openapiClient';
 *   beforeAll(async () => { schema = await loadOpenApiSchema(); });
 *   it('endpoint X esiste BE-side', () => {
 *     expect(getEndpointSchema('GET', '/projects')).toBeTruthy();
 *   });
 *
 * Bump policy: lo schema viene cached al primo successful fetch. Il
 * caller deve invocare `clearOpenApiCache()` in `beforeEach` se il
 * test scenario richiede uno schema diverso (es. mock fetch).
 */

import { API_BASE_URL } from './config';

let _cachedSchema = null;
let _inFlight = null;

/**
 * Fetcha /openapi.json (una sola volta, cache in memoria).
 *
 * Concurrent call durante l'in-flight ricevono la stessa promise
 * (single-flight). Errori rete propagano al caller — niente fallback
 * silent, perche` chi importa il loader si aspetta lo schema o un
 * fail esplicito.
 *
 * @returns {Promise<object>} OpenAPI 3.x schema
 */
export async function loadOpenApiSchema() {
  if (_cachedSchema) return _cachedSchema;
  if (_inFlight) return _inFlight;

  _inFlight = (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/openapi.json`);
      if (!response.ok) {
        throw new Error(`OpenAPI fetch failed: HTTP ${response.status}`);
      }
      const schema = await response.json();
      if (!schema || typeof schema !== 'object' || !schema.paths) {
        throw new Error('OpenAPI schema invalid: missing paths');
      }
      _cachedSchema = schema;
      return schema;
    } finally {
      _inFlight = null;
    }
  })();

  return _inFlight;
}

/**
 * Lookup dello schema di un endpoint.
 *
 * @param {string} method - 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
 * @param {string} path - es. '/projects/{project_id}'
 * @returns {object|null} operation object OpenAPI, o null se non esiste
 */
export function getEndpointSchema(method, path) {
  if (!_cachedSchema || !_cachedSchema.paths) return null;
  const pathItem = _cachedSchema.paths[path];
  if (!pathItem) return null;
  return pathItem[method.toLowerCase()] || null;
}

/**
 * Lookup dello schema di un componente named (es. UserCreate).
 *
 * @param {string} name - nome del componente
 * @returns {object|null} schema JSON o null
 */
export function getModelSchema(name) {
  if (!_cachedSchema || !_cachedSchema.components) return null;
  return _cachedSchema.components.schemas?.[name] || null;
}

/**
 * Lista dei path dichiarati lato BE (utile per drift detection).
 *
 * @returns {string[]} array di path (es. ['/projects', '/auth/login', ...])
 */
export function listEndpointPaths() {
  if (!_cachedSchema || !_cachedSchema.paths) return [];
  return Object.keys(_cachedSchema.paths);
}

/**
 * Reset cache. Usalo in `beforeEach` se vuoi che il prossimo
 * `loadOpenApiSchema()` rifaccia il fetch (es. mock diversi per test).
 */
export function clearOpenApiCache() {
  _cachedSchema = null;
  _inFlight = null;
}

/**
 * Read-only accessor. Ritorna null se non caricato. Per uso debug /
 * test snapshot.
 */
export function getCachedSchema() {
  return _cachedSchema;
}
