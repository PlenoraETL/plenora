/**
 * API client per il modulo Data Source.
 * Segue lo stesso pattern di features.js e client.js.
 */

import http from './http';

// ── CRUD ──────────────────────────────────────────────────────────────────

export const listDataSources = async () => {
  const res = await http.get('/datasources');
  return res.data;
};

export const createDataSource = async (data) => {
  const res = await http.post('/datasources', data);
  return res.data;
};

export const updateDataSource = async (id, data) => {
  const res = await http.put(`/datasources/${id}`, data);
  return res.data;
};

export const deleteDataSource = async (id) => {
  await http.delete(`/datasources/${id}`);
};

export const testDataSource = async (id) => {
  const res = await http.post(`/datasources/${id}/test`);
  return res.data;
};

// ── Operazioni file ───────────────────────────────────────────────────────

export const listFiles = async (id, { path = '', pattern = null, include_hash = false } = {}) => {
  const params = { path, include_hash };
  if (pattern) params.pattern = pattern;
  const res = await http.get(`/datasources/${id}/files`, { params });
  return res.data;
};

