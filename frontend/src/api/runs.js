/**
 * runs.js — API per feature inspection e confronto run
 *
 * Endpoints intermediate.db:
 *   GET /runs/{canvas_id}                        — lista run per canvas
 *   GET /runs/{run_id}/nodes/{node_id}/data      — righe di un nodo
 *   GET /runs/compare/l1?run_id_a=&run_id_b=     — confronto hash/contatori
 *   GET /runs/compare/l2?run_id_a=&run_id_b=&node_id= — righe aggiunte/rimosse
 *   POST /runs/{run_id}/pin                      — pinna/spinna una run
 */

import http from './http';

/**
 * Lista run per un canvas (project_id), ordinate dalla più recente.
 * @param {string} canvasId
 * @param {number} [limit=50]
 * @returns {Promise<Array>}
 */
export const listRuns = async (canvasId, limit = 50) => {
  const response = await http.get(`/runs/${canvasId}`, { params: { limit } });
  return response.data;
};

/**
 * Righe del nodo da intermediate.db.
 * @param {string} runId
 * @param {string} nodeId
 * @param {number} [limit=100]
 * @returns {Promise<Object>} { run_id, node_id, row_count, col_schema, rows, truncated }
 */
export const getNodeData = async (runId, nodeId, limit = 100) => {
  const response = await http.get(`/runs/${runId}/nodes/${nodeId}/data`, {
    params: { limit },
  });
  return response.data;
};

/**
 * Confronto L1 tra due run — hash aggregati e contatori.
 * @param {string} runIdA
 * @param {string} runIdB
 * @returns {Promise<Object>} { run_a, run_b, same_input, same_output, delta_rows_in, delta_rows_out, delta_exec_time }
 */
export const compareRunsL1 = async (runIdA, runIdB) => {
  const response = await http.get('/runs/compare/l1', {
    params: { run_id_a: runIdA, run_id_b: runIdB },
  });
  return response.data;
};

/**
 * Confronto L2 — righe aggiunte/rimosse tra due run sullo stesso nodo.
 * @param {string} runIdA
 * @param {string} runIdB
 * @param {string} nodeId
 * @returns {Promise<Object>} { count_a, count_b, removed, added, removed_count, added_count, truncated }
 */
export const compareRunsL2 = async (runIdA, runIdB, nodeId) => {
  const response = await http.get('/runs/compare/l2', {
    params: { run_id_a: runIdA, run_id_b: runIdB, node_id: nodeId },
  });
  return response.data;
};

/**
 * Pinna o spinna una run (esclusa/inclusa dalla retention FIFO).
 * @param {string} runId
 * @param {boolean} [pinned=true]
 * @returns {Promise<Object>}
 */
export const pinRun = async (runId, pinned = true) => {
  const response = await http.post(`/runs/${runId}/pin`, { pinned });
  return response.data;
};
