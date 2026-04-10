/**
 * client.js — Shim di backward compatibility
 *
 * Questo file esiste per non rompere gli import esistenti che referenziano
 * direttamente 'api/client'. Tutti i nuovi import devono usare 'api/index'
 * o i moduli specifici (api/projects, api/connections, etc.).
 *
 * @deprecated Importa da '../api' o dal modulo specifico.
 *
 * Esempio migrazione:
 *   // Prima
 *   import { getProject, listConnections } from '../api/client';
 *   // Dopo
 *   import { getProject } from '../api/projects';
 *   import { listConnections } from '../api/connections';
 *   // Oppure tutto da index:
 *   import { getProject, listConnections } from '../api';
 */

export { default }           from './http';
export { default as api }    from './http';

export * from './connections';
export * from './projects';
export * from './tasks';
export * from './enrichment';
export * from './apiConnections';
