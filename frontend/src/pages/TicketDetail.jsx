/**
 * TicketDetail.jsx — Dettaglio ticket + commenti + allegati.
 * Route: /tickets/:id
 *
 * Operazioni:
 *   - Vedi metadati + thread commenti + allegati
 *   - Aggiungi commento (qualsiasi viewer autorizzato)
 *   - Upload allegati (immagini + log, size cap backend)
 *   - Admin/superadmin: cambia status, assignee, severity, category
 *   - Creatore: riapre un proprio ticket risolto/chiuso
 *
 * Visibilita' actions: derivata da user.role + ticket.created_by,
 * ma il backend e' comunque la fonte di verita' (403 se bypass UI).
 *
 * Fase 5 T2 (R4.1, R4.7, R4.8): il ticket viene da useResource con chiave
 * l'id (mai i dati di A sotto l'indirizzo di B), gli aggiornamenti dopo
 * un'azione non sostituiscono la pagina con il caricamento, un errore e' un
 * errore con «Riprova» (mai pagina bianca). La bozza del commento e' del
 * ticket: dopo l'invio si svuota solo se e' ancora il testo inviato.
 */
import React, { useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import PageLayout from '../components/common/PageLayout';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useAuth } from '../context/AuthContext';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { useToast } from '../hooks/useToast';
import { toastApiError } from '../utils/toastApiError';
import { useResource } from '../hooks/useResource';
import { ResourceError } from '../components/common/ListState';
import { can } from '../utils/permissions';
import {
  addComment, deleteAttachment, deleteTicket, getTicket, getTicketsMeta,
  openAttachment, updateTicket, uploadAttachment,
} from '../api/tickets';
import '../styles/TicketsList.css';
import { formatDateTime as fmtDateTime, formatBytes as fmtBytes, enumLabel, optionLabel } from '../utils/format';





export default function TicketDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();
  const confirmModal = useConfirmModal();
  const fileInputRef = useRef(null);

  const metaResource = useResource(getTicketsMeta, { key: 'tickets-meta' });
  const meta = metaResource.data || null;
  const resource = useResource(() => getTicket(id), { key: id });
  const data = resource.data || null;
  const load = resource.reload;
  // Bozza del commento PER TICKET.
  const [drafts, setDrafts] = useState({});
  const commentBody = drafts[id] || '';
  const setCommentBody = (text) => setDrafts(d => ({ ...d, [id]: text }));
  const [saving, setSaving] = useState(false);

  const ticket = data?.ticket;
  // Permessi dalla tabella unica (utils/permissions, audit R10.1/R10.9).
  const canManage = can.manageTickets(user);
  const canReopen = can.reopenTicket(user, ticket);

  const handleStatusChange = async (newStatus) => {
    try {
      setSaving(true);
      await updateTicket(id, { status: newStatus });
      toast.success(`Status aggiornato a "${newStatus}"`);
      load();
    } catch (e) {
      toastApiError(toast, e, 'Errore aggiornamento');
    } finally {
      setSaving(false);
    }
  };

  const handleFieldChange = async (field, value) => {
    try {
      setSaving(true);
      await updateTicket(id, { [field]: value });
      load();
    } catch (e) {
      toastApiError(toast, e, `Errore aggiornamento ${field}`);
    } finally {
      setSaving(false);
    }
  };

  const handleAddComment = async (e) => {
    e.preventDefault();
    if (!commentBody.trim() || saving) return;
    // Guard contro doppio submit: pre-fix il pulsante "Invia" restava
    // attivo durante la richiesta, un secondo click creava un commento
    // duplicato. Riusiamo il flag `saving` gia' definito per gli altri
    // form action della pagina (status change, field change).
    setSaving(true);
    const ticketId = id;
    const sent = commentBody;
    try {
      await addComment(ticketId, sent);
      // Il testo scritto durante l'invio resta.
      setDrafts(d => (d[ticketId] === sent ? { ...d, [ticketId]: '' } : d));
      load();
    } catch (err) {
      toastApiError(toast, err, 'Errore invio commento');
    } finally {
      setSaving(false);
    }
  };

  const handleFileUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    for (const file of files) {
      try {
        await uploadAttachment(id, file);
        toast.success(`Allegato "${file.name}" caricato`);
      } catch (err) {
        toastApiError(toast, err, `Caricamento non riuscito: ${file.name}`);
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
    load();
  };

  const handleDeleteTicket = async () => {
    const ok = await confirmModal.confirm({
      title: 'Elimina ticket',
      message: (
        <>
          Eliminare definitivamente il ticket "{ticket.title}"?
          <br /><br />
          Verranno cancellati anche commenti e allegati. L'azione è
          irreversibile.
        </>
      ),
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await deleteTicket(id);
      toast.success('Ticket eliminato');
      navigate('/tickets');
    } catch (e) {
      toastApiError(toast, e, 'Errore eliminazione ticket');
    }
  };

  const handleOpenAttachment = async (attachmentId, filename) => {
    try {
      await openAttachment(id, attachmentId, filename);
    } catch (e) {
      toastApiError(toast, e, 'Errore apertura allegato');
    }
  };

  const handleDeleteAttachment = async (attachmentId) => {
    const ok = await confirmModal.confirm({
      title: 'Rimuovi allegato',
      message: 'Rimuovere questo allegato?',
      confirmText: 'Rimuovi',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await deleteAttachment(id, attachmentId);
      load();
    } catch (e) {
      toastApiError(toast, e, 'Errore rimozione allegato');
    }
  };

  const parent = { to: '/tickets', label: '← Ticket' };

  if (resource.initialLoading) {
    return (
      <PageLayout className="tk-page" title="Ticket" parent={parent}>
        <div role="status">Caricamento…</div>
      </PageLayout>
    );
  }

  if (!ticket) {
    return (
      <PageLayout className="tk-page" title="Ticket" parent={parent}>
        {resource.status === 'error' ? (
          <ResourceError
            resource={resource}
            title={resource.rawError?.response?.status === 404 ? 'Ticket non trovato' : 'Ticket non caricato'}
          />
        ) : (
          <ResourceError title="Ticket non caricato" message="Il ticket non è disponibile." onRetry={resource.reload} />
        )}
      </PageLayout>
    );
  }

  return (
    <PageLayout
      className="tk-page"
      parent={parent}
      title={ticket.title}
      subtitle={<>#{ticket.id.slice(0, 8)} · {enumLabel('ticketKind', ticket.kind)}{' · aggiornato '}{fmtDateTime(ticket.updated_at)}</>}
      actions={canManage && (
        // Unica azione della pagina (nessuna primaria accanto, G3), con conferma.
        <button
          type="button"
          className="tk-page__btn tk-page__btn--danger"
          onClick={handleDeleteTicket}
          disabled={saving}
        >
          Elimina ticket
        </button>
      )}
    >
        {/* Ricarica non riuscita (dopo un'azione): il ticket mostrato e' quello di prima. */}
        <ResourceError resource={resource} title="Ticket non aggiornato" />
        <ResourceError compact resource={metaResource} title="Opzioni dei ticket non caricate" />

        <section className="tk-detail">
          {/* Metadata */}
          <div className="tk-detail__meta">
            <div className="tk-detail__field">
              <span className="tk-detail__label">Status</span>
              {canManage ? (
                <select
                  value={ticket.status}
                  onChange={(e) => handleStatusChange(e.target.value)}
                  disabled={saving}
                >
                  {(meta?.statuses || []).map((s) => (
                    <option key={s} value={s}>{enumLabel('ticketStatus', s)}</option>
                  ))}
                </select>
              ) : (
                <>
                  <span className={`tk__status tk__status--${ticket.status}`}>
                    {enumLabel('ticketStatus', ticket.status)}
                  </span>
                  {canReopen && (
                    <button
                      type="button"
                      className="tk-detail__btn"
                      onClick={() => handleStatusChange('reopened')}
                      disabled={saving}
                    >
                      Riapri
                    </button>
                  )}
                </>
              )}
            </div>

            <div className="tk-detail__field">
              <span className="tk-detail__label">Severity</span>
              {canManage ? (
                <select
                  value={ticket.severity}
                  onChange={(e) => handleFieldChange('severity', e.target.value)}
                  disabled={saving}
                >
                  {(meta?.severities || []).map((s) => (
                    <option key={s} value={s}>{enumLabel('ticketSeverity', s)}</option>
                  ))}
                </select>
              ) : (
                <span className={`tk__sev tk__sev--${ticket.severity}`}>
                  {enumLabel('ticketSeverity', ticket.severity)}
                </span>
              )}
            </div>

            <div className="tk-detail__field">
              <span className="tk-detail__label">Categoria</span>
              {canManage ? (
                <select
                  value={ticket.category}
                  onChange={(e) => handleFieldChange('category', e.target.value)}
                  disabled={saving}
                >
                  {(meta?.categories || []).map((c) => (
                    <option key={c.value} value={c.value}>{c.label}</option>
                  ))}
                </select>
              ) : (
                <span>{optionLabel(meta?.categories, ticket.category)}</span>
              )}
            </div>

            <div className="tk-detail__field">
              <span className="tk-detail__label">Creato</span>
              <span>{fmtDateTime(ticket.created_at)}</span>
            </div>

            {ticket.resolved_at && (
              <div className="tk-detail__field">
                <span className="tk-detail__label">Risolto il</span>
                <span>{fmtDateTime(ticket.resolved_at)}</span>
              </div>
            )}
          </div>

          {/* Description */}
          <div className="tk-detail__description">
            <h3>Descrizione</h3>
            <pre className="tk-detail__descr-body">{ticket.description || '(nessuna descrizione)'}</pre>
          </div>

          {/* Attachments */}
          <div className="tk-detail__attachments">
            <h3>
              Allegati ({data.attachments.length})
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={meta ? meta.allowed_attachment_extensions.map(e => `.${e}`).join(',') : ''}
                onChange={handleFileUpload}
                style={{ marginLeft: 12 }}
              />
            </h3>
            {data.attachments.length === 0 && (
              <div className="tk-detail__empty">Nessun allegato</div>
            )}
            <ul className="tk-detail__att-list">
              {data.attachments.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    className="tk-detail__att-link"
                    onClick={() => handleOpenAttachment(a.id, a.original_filename)}
                  >
                    {a.original_filename}
                  </button>
                  <span className="tk-detail__att-meta">
                    {fmtBytes(a.size_bytes)} · {fmtDateTime(a.uploaded_at)} · {a.uploaded_by_username || 'unknown'}
                  </span>
                  {can.removeTicketAttachment(user, a) && (
                    <button
                      type="button"
                      className="tk-detail__btn tk-detail__btn--danger"
                      onClick={() => handleDeleteAttachment(a.id)}
                    >
                      Rimuovi
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>

          {/* Comments thread */}
          <div className="tk-detail__comments">
            <h3>Commenti ({data.comments.length})</h3>
            {data.comments.map((c) => (
              <div key={c.id} className="tk-comment">
                <div className="tk-comment__head">
                  <strong>{c.author_username || 'unknown'}</strong>
                  <span>{fmtDateTime(c.created_at)}</span>
                </div>
                <pre className="tk-comment__body">{c.body}</pre>
              </div>
            ))}

            <form onSubmit={handleAddComment} className="tk-comment-form">
              <textarea
                rows={3}
                placeholder="Scrivi un commento…"
                value={commentBody}
                onChange={(e) => setCommentBody(e.target.value)}
                required
              />
              <button
                type="submit"
                className="tk-page__btn tk-page__btn--primary"
                disabled={saving || !commentBody.trim()}
              >
                {saving ? 'Invio…' : 'Invia'}
              </button>
            </form>
          </div>
        </section>
      <ConfirmDialog {...confirmModal} />
    </PageLayout>
  );
}
