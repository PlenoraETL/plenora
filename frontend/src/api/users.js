/**
 * users.js — API client per gestione utenti, gruppi e profilo personale
 */
import http from './http';
import { fetchAllOffsetPages } from './pagination';

export const getMe           = ()              => http.get('/auth/me').then(r => r.data);
export const changeMyPassword = (oldPwd, newPwd) =>
  http.post('/auth/change-password', { old_password: oldPwd, new_password: newPwd }).then(r => r.data);

export const listUsers       = ()              => fetchAllOffsetPages('/auth/users');
export const createUser      = (data)          => http.post('/auth/users', data).then(r => r.data);
export const updateUser      = (id, data)      => http.put(`/auth/users/${id}`, data).then(r => r.data);
// DELETE risponde 204 No Content (vuoto): `r.data` e' `''` o `undefined`.
// Pre-fix (P1 contracts-flows): `.then(r => r.data)` ritornava stringa
// vuota; consumer che faceva `result.deleted_user_id` riceveva
// `undefined` invece di sollevare un'error. Post-fix: ritorna oggetto
// canonico {success: true} per semantica esplicita.
export const deleteUser      = (id)            =>
  http.delete(`/auth/users/${id}`).then(() => ({ success: true, id }));
// Elimina mantenendo lo storico (10/11/2026, solo superadmin): 204 vuoto.
export const deleteUserKeepingHistory = (id)   =>
  http.post(`/auth/users/${id}/delete-keeping-history`).then(() => ({ success: true, id }));
export const resetPassword   = (id, data)      => http.post(`/auth/users/${id}/reset-password`, data).then(r => r.data);

export const listGroups      = ()              => fetchAllOffsetPages('/auth/groups');
export const createGroup     = (data)          => http.post('/auth/groups', data).then(r => r.data);
// DELETE /auth/groups/{id} risponde 200 con { deleted, group_id, report }:
// il report (cosa e' stato eliminato) arriva al chiamante (audit R10.3).
// Su un corpo vuoto resta la forma canonica { success: true, id }.
export const deleteGroup     = (id)            =>
  http.delete(`/auth/groups/${id}`).then(r => ({
    ...(r?.data && typeof r.data === 'object' ? r.data : {}),
    success: true,
    id,
  }));
export const getGroupSummary = (id)            => http.get(`/auth/groups/${id}/summary`).then(r => r.data);
