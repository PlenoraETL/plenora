/**
 * api/adminSystemStatus.js — dashboard aggregato stato sistema (superadmin)
 *
 * Endpoint:
 *   GET /api/admin/system-status  →  { generated_at, backup, verify_restore, schedules, workers }
 *
 * Ogni sezione ha { status: 'ok'|'warn'|'error'|'unknown', message: string, ...fields }.
 * Errori in una sezione NON bloccano le altre (graceful degradation).
 *
 * Usato da: pages/AdminSystemStatusPage.jsx
 */

import http from './http';

export const getSystemStatus = async () => {
  const response = await http.get('/admin/system-status');
  return response.data;
};
