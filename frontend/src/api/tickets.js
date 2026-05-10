/**
 * api/tickets.js — Client API per il sistema ticket (tech_debt + user_support).
 *
 * Endpoint coperti:
 *   GET    /api/tickets/meta                      enum + config lato client
 *   GET    /api/tickets/counts                    badge sidebar + card dashboard
 *   GET    /api/tickets                           lista con filtri
 *   POST   /api/tickets                           crea
 *   GET    /api/tickets/:id                       dettaglio + commenti + allegati
 *   PATCH  /api/tickets/:id                       update (RBAC nel backend)
 *   POST   /api/tickets/:id/comments              aggiungi commento
 *   POST   /api/tickets/:id/attachments           upload allegato (multipart)
 *   DELETE /api/tickets/:id/attachments/:aid      rimuovi allegato
 *
 * RBAC e' enforced nel backend — qui il client serve solo da wrapper HTTP.
 */

import http from './http';
import {
  assertFileSize,
  throwIfUploadTooLarge,
  TICKET_ATTACHMENT_MAX_SIZE_BYTES,
} from './uploadGuards';

export const getTicketsMeta = async () => {
  const res = await http.get('/tickets/meta');
  return res.data;
};

export const getTicketsCounts = async () => {
  const res = await http.get('/tickets/counts');
  return res.data;
};

/**
 * Lista ticket con filtri opzionali.
 * @param {{kind?: string, status?: string|string[], severity?: string|string[],
 *           assignee?: string, mine?: boolean, limit?: number}} opts
 */
export const listTickets = async (opts = {}) => {
  const params = {};
  if (opts.kind) params.kind = opts.kind;
  if (opts.status) {
    params.status = Array.isArray(opts.status) ? opts.status.join(',') : opts.status;
  }
  if (opts.severity) {
    params.severity = Array.isArray(opts.severity)
      ? opts.severity.join(',') : opts.severity;
  }
  if (opts.assignee) params.assignee = opts.assignee;
  if (opts.mine) params.mine = true;
  if (opts.limit) params.limit = opts.limit;
  const res = await http.get('/tickets', { params });
  return res.data.tickets;
};

/**
 * @param {{kind: 'tech_debt'|'user_support', title: string, description?: string,
 *           severity?: string, category?: string, target_group_id?: string,
 *           assignee?: string}} payload
 */
export const createTicket = async (payload) => {
  const res = await http.post('/tickets', payload);
  return res.data;
};

export const getTicket = async (ticketId) => {
  const res = await http.get(`/tickets/${ticketId}`);
  return res.data;  // { ticket, comments, attachments }
};

/**
 * Update parziale. `assignee: null` per unassign; omettere la key per non toccare.
 */
export const updateTicket = async (ticketId, patch) => {
  // Converti `assignee: undefined` o chiave mancante in sentinel "__UNSET__"
  // cosi' il backend non la tocca. Se esplicitamente null → unassign.
  const payload = { ...patch };
  if (!('assignee' in patch)) payload.assignee = '__UNSET__';
  const res = await http.patch(`/tickets/${ticketId}`, payload);
  return res.data;
};

export const addComment = async (ticketId, body) => {
  const res = await http.post(`/tickets/${ticketId}/comments`, { body });
  return res.data;
};

export const uploadAttachment = async (ticketId, file) => {
  // Pre-check size lato client allineato al cap backend specifico per i
  // ticket (10MB). Il default generale di
  // uploadGuards e' 100MB e accetterebbe file che `tickets/router.py`
  // (TICKET_ATTACHMENT_MAX_BYTES = 10MB) rifiuta.
  assertFileSize(file, { maxBytes: TICKET_ATTACHMENT_MAX_SIZE_BYTES });

  const form = new FormData();
  form.append('file', file);
  // Non forzare Content-Type: senza boundary blocca il parsing
  // Starlette/python-multipart. Axios lo costruisce da FormData.
  try {
    const res = await http.post(`/tickets/${ticketId}/attachments`, form);
    return res.data;
  } catch (err) {
    throwIfUploadTooLarge(err);
    throw err;
  }
};

export const deleteAttachment = async (ticketId, attachmentId) => {
  const res = await http.delete(
    `/tickets/${ticketId}/attachments/${attachmentId}`,
  );
  return res.data;
};

/**
 * Hard delete del ticket. Solo admin/superadmin (RBAC nel backend).
 * Cascade automatico su commenti e allegati.
 */
export const deleteTicket = async (ticketId) => {
  const res = await http.delete(`/tickets/${ticketId}`);
  return res.data;
};

/**
 * Scarica il blob dell'allegato con auth header e apre/scarica nel browser.
 * Le immagini (Content-Disposition: inline) vengono aperte in nuova tab;
 * log/txt (attachment) forzano il download via <a download>.
 */
export const openAttachment = async (ticketId, attachmentId, filename) => {
  const res = await http.get(
    `/tickets/${ticketId}/attachments/${attachmentId}/download`,
    { responseType: 'blob' },
  );
  const disposition = res.headers['content-disposition'] || '';
  const isInline = disposition.toLowerCase().startsWith('inline');
  const url = URL.createObjectURL(res.data);
  if (isInline) {
    window.open(url, '_blank', 'noopener,noreferrer');
    // Revoke dopo un delay: alcuni browser serializzano la open async.
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } else {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || 'attachment';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }
};
