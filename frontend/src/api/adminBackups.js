/**
 * api/adminBackups.js — API per la gestione dei backup DB (solo superadmin)
 *
 * Endpoint REST:
 *   GET  /api/admin/backups          lista oggetti S3
 *   GET  /api/admin/backups/status   stato ultimo run systemd
 *   GET  /api/admin/backups/schedule prossimo run timer
 *   POST /api/admin/backups/trigger  trigger manuale
 *
 * Tutti richiedono ruolo superadmin (enforcement backend, il frontend
 * filtra solo la UI via role.isSuperadmin).
 *
 * Usato da: pages/AdminBackupsPage.jsx
 */

import http from './http';

/**
 * Lista dei backup presenti nel bucket S3.
 * @returns {Promise<{bucket: string, region: string, count: number, items: Array}>}
 */
export const listBackups = async ({ limit = 100, cursor = null } = {}) => {
  const params = { limit };
  if (cursor) params.cursor = cursor;
  const response = await http.get('/admin/backups', { params });
  return response.data;
};

/**
 * Stato dell'ultimo run del service urby-backup.
 * @returns {Promise<{result: string, exit_code: number|null, finished_at: string|null, duration_sec: number|null, log_tail: string[]}>}
 */
export const getBackupStatus = async () => {
  const response = await http.get('/admin/backups/status');
  return response.data;
};

/**
 * Info sul prossimo run schedulato del timer urby-backup.
 * @returns {Promise<{timer_active: boolean, next_run: string|null, last_run: string|null}>}
 */
export const getBackupSchedule = async () => {
  const response = await http.get('/admin/backups/schedule');
  return response.data;
};

/**
 * Lancia un backup manuale on-demand.
 * @returns {Promise<{started: boolean, pid: number|null, message: string}>}
 */
export const triggerBackup = async () => {
  const response = await http.post('/admin/backups/trigger');
  return response.data;
};
