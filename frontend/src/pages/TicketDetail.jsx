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
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import AppSidebar from '../components/AppSidebar';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useAuth } from '../context/AuthContext';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { useToast } from '../hooks/useToast';
import {
  addComment, deleteAttachment, deleteTicket, getTicket, getTicketsMeta,
  openAttachment, updateTicket, uploadAttachment,
} from '../api/tickets';
import '../styles/TicketsList.css';


function fmtDateTime(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('it-IT', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return iso; }
}

function fmtBytes(n) {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}


export default function TicketDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();
  const confirmModal = useConfirmModal();
  const fileInputRef = useRef(null);

  const [meta, setMeta] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [commentBody, setCommentBody] = useState('');
  const [saving, setSaving] = useState(false);

  const ticket = data?.ticket;
  const isAdmin = user?.role === 'admin' || user?.role === 'superadmin';
  const isCreator = ticket && ticket.created_by === user?.user_id;
  // L'utente puo' riaprire un proprio ticket chiuso/risolto
  const canReopen =
    isCreator && !isAdmin && (ticket?.status === 'resolved' || ticket?.status === 'closed');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getTicket(id);
      setData(result);
    } catch (e) {
      toast.error(e.message || 'Errore caricamento ticket');
      if (e.response?.status === 404) navigate('/tickets');
    } finally {
      setLoading(false);
    }
  }, [id, toast, navigate]);

  useEffect(() => {
    getTicketsMeta().then(setMeta).catch(() => {});
    load();
  }, [load]);

  const handleStatusChange = async (newStatus) => {
    try {
      setSaving(true);
      await updateTicket(id, { status: newStatus });
      toast.success(`Status aggiornato a "${newStatus}"`);
      load();
    } catch (e) {
      toast.error(e.message || 'Errore aggiornamento');
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
      toast.error(e.message || `Errore aggiornamento ${field}`);
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
    try {
      await addComment(id, commentBody);
      setCommentBody('');
      load();
    } catch (err) {
      toast.error(err.message || 'Errore invio commento');
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
        toast.error(
          err.message || `Errore upload ${file.name}`,
        );
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
      toast.error(e.message || 'Errore eliminazione ticket');
    }
  };

  const handleOpenAttachment = async (attachmentId, filename) => {
    try {
      await openAttachment(id, attachmentId, filename);
    } catch (e) {
      toast.error(e.message || 'Errore apertura allegato');
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
      toast.error(e.message || 'Errore rimozione allegato');
    }
  };

  if (loading) {
    return (
      <div className="tk-page">
        <AppSidebar />
        <main className="tk-page__main"><div>Caricamento…</div></main>
      </div>
    );
  }

  if (!ticket) return null;

  return (
    <div className="tk-page">
      <AppSidebar />
      <main className="tk-page__main">
        <header className="tk-page__header">
          <div>
            <button
              type="button"
              className="tk-detail__back"
              onClick={() => navigate('/tickets')}
            >
              ← Ticket
            </button>
            <h1 className="tk-page__title">{ticket.title}</h1>
            <div className="tk-page__subtitle">
              #{ticket.id.slice(0, 8)} · {ticket.kind === 'tech_debt' ? 'Debito tecnico' : 'Supporto utente'}
              {' · aggiornato '}{fmtDateTime(ticket.updated_at)}
            </div>
          </div>
          {isAdmin && (
            <button
              type="button"
              className="tk-page__btn tk-page__btn--danger"
              onClick={handleDeleteTicket}
              disabled={saving}
            >
              Elimina ticket
            </button>
          )}
        </header>

        <section className="tk-detail">
          {/* Metadata */}
          <div className="tk-detail__meta">
            <div className="tk-detail__field">
              <span className="tk-detail__label">Status</span>
              {isAdmin ? (
                <select
                  value={ticket.status}
                  onChange={(e) => handleStatusChange(e.target.value)}
                  disabled={saving}
                >
                  {(meta?.statuses || []).map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              ) : (
                <>
                  <span className={`tk__status tk__status--${ticket.status}`}>
                    {ticket.status}
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
              {isAdmin ? (
                <select
                  value={ticket.severity}
                  onChange={(e) => handleFieldChange('severity', e.target.value)}
                  disabled={saving}
                >
                  {(meta?.severities || []).map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              ) : (
                <span className={`tk__sev tk__sev--${ticket.severity}`}>
                  {ticket.severity}
                </span>
              )}
            </div>

            <div className="tk-detail__field">
              <span className="tk-detail__label">Categoria</span>
              {isAdmin ? (
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
                <span>{ticket.category}</span>
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
                  <button
                    type="button"
                    className="tk-detail__btn tk-detail__btn--danger"
                    onClick={() => handleDeleteAttachment(a.id)}
                  >
                    Rimuovi
                  </button>
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
      </main>
      <ConfirmDialog {...confirmModal} />
    </div>
  );
}
