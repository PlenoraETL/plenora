/**
 * dashboard.js — conteggi della dashboard (UI-1, 06/11/2026, audit C4).
 *
 * GET /dashboard/counts: connessioni API, data source e schedule (attivi e
 * per progetto) come numeri, al posto di tre liste intere che la dashboard
 * usava solo per contarle.
 */
import http from './http';

/**
 * @returns {Promise<{api_connections: number, datasources: number, schedules_active: number, schedules_by_project: Object<string, number>}>}
 */
export const getDashboardCounts = async () => {
  const response = await http.get('/dashboard/counts');
  return response.data;
};

export default { getDashboardCounts };
