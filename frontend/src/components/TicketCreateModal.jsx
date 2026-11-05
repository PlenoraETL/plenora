/**
 * TicketCreateModal.jsx — Form modal per aprire un nuovo ticket.
 * Usato da TicketsList e (opzionalmente) dalla sidebar.
 *
 * Campi:
 *   - title (required, min 3)
 *   - description (opzionale)
 *   - kind: user_support / tech_debt (tech_debt solo admin+)
 *   - severity: low/medium/high/critical (default medium)
 *   - category: enum da meta
 */
import React, { useState } from 'react';

import { createTicket } from '../api/tickets';
import { useToast } from '../hooks/useToast';
import useDialog from '../hooks/useDialog';
import { toastApiError } from '../utils/toastApiError';
import { can } from '../utils/permissions';


export default function TicketCreateModal({ meta, user, onClose, onCreated }) {
  const toast = useToast();
  // Il tipo 'debito tecnico' e' degli admin (stessa regola della gestione).
  const isAdmin = can.manageTickets(user);

  const [form, setForm] = useState({
    kind: 'user_support',
    title: '',
    description: '',
    severity: 'medium',
    category: 'other',
  });
  const [submitting, setSubmitting] = useState(false);
  // Montato solo quando aperto. Durante l'invio niente ×, Esc o Annulla
  // (N6: la risposta non deve arrivare a una finestra chiusa).
  const dialog = useDialog({ isOpen: true, onClose, busy: submitting, closeOnOverlay: false });

  const handle = (k, v) => setForm((s) => ({ ...s, [k]: v }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (form.title.trim().length < 3) {
      toast.error('Il titolo deve avere almeno 3 caratteri');
      return;
    }
    setSubmitting(true);
    try {
      const created = await createTicket({
        kind: form.kind,
        title: form.title.trim(),
        description: form.description,
        severity: form.severity,
        category: form.category,
      });
      onCreated?.(created);
    } catch (err) {
      toastApiError(toast, err, 'Errore creazione ticket');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="tk-modal__overlay" {...dialog.overlayProps}>
      <div
        ref={dialog.ref}
        className="tk-modal"
        {...dialog.dialogProps}
      >
        <header className="tk-modal__header">
          <h2 id={dialog.titleId}>Nuovo ticket</h2>
          <button type="button" className="tk-modal__close" {...dialog.closeButtonProps}>×</button>
        </header>

        <form onSubmit={handleSubmit} className="tk-modal__form">
          {isAdmin && (
            <label>
              Tipo
              <select
                value={form.kind}
                onChange={(e) => handle('kind', e.target.value)}
              >
                <option value="user_support">Supporto utente</option>
                <option value="tech_debt">Debito tecnico</option>
              </select>
            </label>
          )}

          <label>
            Titolo *
            <input
              type="text"
              maxLength={200}
              required
              value={form.title}
              onChange={(e) => handle('title', e.target.value)}
              placeholder="Riassunto breve del problema o del debito"
            />
          </label>

          <label>
            Descrizione
            <textarea
              rows={6}
              value={form.description}
              onChange={(e) => handle('description', e.target.value)}
              placeholder="Dettagli, passi per riprodurre, cosa ti aspettavi vs cosa vedi"
            />
          </label>

          <div className="tk-modal__row">
            <label>
              Severity
              <select
                value={form.severity}
                onChange={(e) => handle('severity', e.target.value)}
              >
                {(meta?.severities || ['low', 'medium', 'high', 'critical']).map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>

            <label>
              Categoria
              <select
                value={form.category}
                onChange={(e) => handle('category', e.target.value)}
              >
                {(meta?.categories || [{ value: 'other', label: 'Altro' }]).map((c) => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="tk-modal__actions">
            <button type="button" className="tk-page__btn" onClick={onClose} disabled={submitting}>
              Annulla
            </button>
            <button
              type="submit"
              className="tk-page__btn tk-page__btn--primary"
              disabled={submitting}
            >
              {submitting ? 'Creazione…' : 'Apri ticket'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
