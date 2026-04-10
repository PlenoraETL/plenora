/**
 * users.js — API client per gestione utenti e gruppi
 */
import http from './http';

// ── Utenti ────────────────────────────────────────────────────────────────
export const listUsers       = ()              => http.get('/auth/users').then(r => r.data);
export const createUser      = (data)          => http.post('/auth/users', data).then(r => r.data);
export const updateUser      = (id, data)      => http.put(`/auth/users/${id}`, data).then(r => r.data);
export const deleteUser      = (id)            => http.delete(`/auth/users/${id}`).then(r => r.data);
export const resetPassword   = (id, data)      => http.post(`/auth/users/${id}/reset-password`, data).then(r => r.data);

// ── Gruppi ────────────────────────────────────────────────────────────────
export const listGroups      = ()              => http.get('/auth/groups').then(r => r.data);
export const createGroup     = (data)          => http.post('/auth/groups', data).then(r => r.data);
export const deleteGroup     = (id)            => http.delete(`/auth/groups/${id}`).then(r => r.data);
export const getGroupSummary = (id)            => http.get(`/auth/groups/${id}/summary`).then(r => r.data);
