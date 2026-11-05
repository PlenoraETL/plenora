/**
 * TicketsList.jsx — Lista ticket (tech_debt + user_support).
 * Route: /tickets
 *
 * RBAC visibilità (enforcement lato backend):
 *   esecutore  → accesso negato (redirect dalla sidebar, 403 dall'API)
 *   viewer/user→ solo i propri ticket (qualunque kind)
 *   admin      → ticket del proprio gruppo
 *   superadmin → cross-group
 *
 * Filtri: kind, status (multi), severity (multi), "solo i miei".
 * Ordine: updated_at DESC (backend).
 *
 * Fase 5 T2 (R4.5, R4.8): lista da useResource con i filtri nella chiave
 * (vince l'ultima richiesta), errore come errore con «Riprova» (ListState).
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import AppSidebar from '../components/AppSidebar';
import TicketCreateModal from '../components/TicketCreateModal';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../hooks/useToast';
import { useResource } from '../hooks/useResource';
import ListState from '../components/common/ListState';
import { can } from '../utils/permissions';
import { getTicketsMeta, listTickets } from '../api/tickets';
import '../styles/TicketsList.css';


function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('it-IT', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return iso; }
}

function SeverityBadge({ value }) {
  const map = {
    low:      { label: 'Bassa',    cls: 'tk__sev--low' },
    medium:   { label: 'Media',    cls: 'tk__sev--medium' },
    high:     { label: 'Alta',     cls: 'tk__sev--high' },
    critical: { label: 'Critica',  cls: 'tk__sev--critical' },
  };
  const v = map[value] || { label: value, cls: '' };
  return <span className={`tk__sev ${v.cls}`}>{v.label}</span>;
}

function StatusBadge({ value }) {
  const map = {
    open:          'Aperto',
    in_progress:   'In lavorazione',
    waiting_user:  'Attesa utente',
    resolved:      'Risolto',
    closed:        'Chiuso',
    reopened:      'Riaperto',
  };
  return <span className={`tk__status tk__status--${value}`}>{map[value] || value}</span>;
}

function KindBadge({ value }) {
  if (value === 'tech_debt') {
    return <span className="tk__kind tk__kind--td">Debito tecnico</span>;
  }
  return <span className="tk__kind tk__kind--us">Supporto utente</span>;
}


export default function TicketsList() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();

  const metaResource = useResource(getTicketsMeta, { key: 'tickets-meta' });
  const meta = metaResource.data || null;
  const [showCreate, setShowCreate] = useState(false);

  // Filtri
  const [kindFilter, setKindFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState(['open', 'in_progress', 'waiting_user', 'reopened']);
  const [severityFilter, setSeverityFilter] = useState([]);
  const [mineOnly, setMineOnly] = useState(false);

  const isAdmin = can.manageTickets(user);

  const filters = {
    kind: kindFilter || undefined,
    status: statusFilter.length ? statusFilter : undefined,
    severity: severityFilter.length ? severityFilter : undefined,
    mine: mineOnly || undefined,
  };
  const resource = useResource(() => listTickets(filters), { key: filters, keepPreviousData: true });
  const tickets = resource.data || [];
  const loading = resource.loading;

  const toggleArr = (setter, arr, val) => {
    setter(arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val]);
  };

  const handleCreated = (newTicket) => {
    setShowCreate(false);
    toast.success(`Ticket "${newTicket.title}" creato`);
    navigate(`/tickets/${newTicket.id}`);
  };

  return (
    <div className="tk-page">
      <AppSidebar />
      <main className="tk-page__main">
        <header className="tk-page__header">
          <div>
            <h1 className="tk-page__title">Ticket</h1>
            <div className="tk-page__subtitle">
              {loading ? 'Caricamento…' : `${tickets.length} ticket`}
            </div>
          </div>
          <button
            type="button"
            className="tk-page__btn tk-page__btn--primary"
            onClick={() => setShowCreate(true)}
          >
            + Apri ticket
          </button>
        </header>

        <section className="tk-filters">
          <label className="tk-filters__row">
            <span>Tipo:</span>
            <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
              <option value="">Tutti</option>
              {isAdmin && <option value="tech_debt">Debito tecnico</option>}
              <option value="user_support">Supporto utente</option>
            </select>
          </label>

          <div className="tk-filters__row">
            <span>Status:</span>
            {(meta?.statuses || []).map((s) => (
              <label key={s} className="tk-filters__chip">
                <input
                  type="checkbox"
                  checked={statusFilter.includes(s)}
                  onChange={() => toggleArr(setStatusFilter, statusFilter, s)}
                />
                {s}
              </label>
            ))}
          </div>

          <div className="tk-filters__row">
            <span>Severity:</span>
            {(meta?.severities || []).map((s) => (
              <label key={s} className="tk-filters__chip">
                <input
                  type="checkbox"
                  checked={severityFilter.includes(s)}
                  onChange={() => toggleArr(setSeverityFilter, severityFilter, s)}
                />
                {s}
              </label>
            ))}
          </div>

          <label className="tk-filters__row">
            <input
              type="checkbox"
              checked={mineOnly}
              onChange={(e) => setMineOnly(e.target.checked)}
            />
            <span>{isAdmin ? 'Assegnati a me' : 'Solo i miei'}</span>
          </label>
        </section>

        <section className="tk-list">
          <ListState
            resource={resource}
            errorTitle="Ticket non caricati"
            empty={<div className="tk-list__empty">Nessun ticket con questi filtri</div>}
          >
          <table className="tk-list__table">
            <thead>
              <tr>
                <th>Titolo</th>
                <th>Tipo</th>
                <th>Severity</th>
                <th>Status</th>
                <th>Categoria</th>
                <th>Aggiornato</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => (
                <tr
                  key={t.id}
                  className="tk-list__row"
                  onClick={() => navigate(`/tickets/${t.id}`)}
                >
                  <td className="tk-list__title">{t.title}</td>
                  <td><KindBadge value={t.kind} /></td>
                  <td><SeverityBadge value={t.severity} /></td>
                  <td><StatusBadge value={t.status} /></td>
                  <td className="tk-list__cat">{t.category}</td>
                  <td className="tk-list__date">{fmtDate(t.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </ListState>
        </section>

        {showCreate && (
          <TicketCreateModal
            meta={meta}
            user={user}
            onClose={() => setShowCreate(false)}
            onCreated={handleCreated}
          />
        )}
      </main>
    </div>
  );
}
