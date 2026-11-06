/**
 * Client API per il modulo Scheduler.
 * 
 * Endpoint: /api/schedules/*
 * Gestisce CRUD schedule, log, toggle, esecuzione manuale e healthcheck.
 */

import http from './http';
import { API_ENDPOINT } from './config';
import { downloadBlob } from '../utils/downloadBlob';
import { downloadProgress } from '../utils/transferProgress';
import { fetchAllOffsetPages } from './pagination';

export const listSchedules = async (projectId = null) => {
  const params = projectId ? { project_id: projectId } : {};
  return fetchAllOffsetPages('/schedules', {
    params,
    extractItems: data => data.schedules,
    mergeItems: items => ({ schedules: items, total: items.length }),
  });
};

export const getSchedule = async (scheduleId) => {
  const res = await http.get(`/schedules/${scheduleId}`);
  return res.data;
};

export const createSchedule = async (data) => {
  const res = await http.post('/schedules', data);
  return res.data;
};

export const updateSchedule = async (scheduleId, data) => {
  const res = await http.put(`/schedules/${scheduleId}`, data);
  return res.data;
};

export const deleteSchedule = async (scheduleId) => {
  const res = await http.delete(`/schedules/${scheduleId}`);
  return res.data;
};

export const toggleSchedule = async (scheduleId, enabled) => {
  const res = await http.patch(`/schedules/${scheduleId}/toggle`, { enabled });
  return res.data;
};

export const runScheduleNow = async (scheduleId) => {
  const res = await http.post(`/schedules/${scheduleId}/run`);
  return res.data;
};

export const getScheduleLogs = async (scheduleId, page = 1, pageSize = 20, status = null) => {
  const params = { page, page_size: pageSize };
  if (status) params.status = status;
  const res = await http.get(`/schedules/${scheduleId}/logs`, { params });
  return res.data;
};

export const getBeatHealthcheck = async () => {
  const res = await http.get('/schedules/healthcheck');
  return res.data;
};


export const listScheduleRuns = async (scheduleId) => {
  return fetchAllOffsetPages(`/schedules/${scheduleId}/runs`, {
    extractItems: data => data.runs,
    mergeItems: (items, lastPage) => ({
      ...lastPage,
      runs: items,
      runs_count: items.length,
    }),
  });
};

/**
 * Scarica l'output di uno schedule con autenticazione Bearer.
 *
 * @param {string} scheduleId
 * @param {string|null} run - timestamp run opzionale
 * @param {string} [filename]
 */
export const downloadScheduleOutput = async (scheduleId, run = null, filename = null, { onProgress = null } = {}) => {
  const params = run ? { run } : {};
  const response = await http.get(`/schedules/${scheduleId}/download`, {
    params,
    responseType: 'blob',
    ...downloadProgress(onProgress),
  });

  downloadBlob(response, filename, `schedule_${scheduleId}_output`);
};
