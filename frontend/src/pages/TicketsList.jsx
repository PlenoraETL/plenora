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

import PageLayout from '../components/common/PageLayout';
import EmptyState from '../components/common/EmptyState';
import TicketCreateModal from '../components/TicketCreateModal';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../hooks/useToast';
import { useResource } from '../hooks/useResource';
import ListState, { ResourceError } from '../components/common/ListState';
import { can } from '../utils/permissions';
import { getTicketsMeta, listTickets } from '../api/tickets';
import '../styles/TicketsList.css';
import { formatDateTime as fmtDate, formatNumber, enumLabel, optionLabel } from '../utils/format';
import { pressable } from '../utils/pressable';

// Massimo di GET /tickets (backend core/tickets/router.py: limit=500).
const TICKET_LIST_LIMIT = 500;



function SeverityBadge({ value }) {
  return <span className={`tk__sev tk__sev--${value}`}>{enumLabel('ticketSeverity', value)}</span>;
}

function StatusBadge({ value }) {
  return <span className={`tk__status tk__status--${value}`}>{enumLabel('ticketStatus', value)}</span>;
}

function KindBadge({ value }) {
  return <span className={`tk__kind tk__kind--${value === 'tech_debt' ? 'td' : 'us'}`}>{enumLabel('ticketKind', value)}</span>;
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

  // P22: nessuno stato selezionato = nessun ticket (non "tutti", chiusi
  // compresi); la richiesta non parte e la lista lo dice.
  const noStatus = statusFilter.length === 0;
  const filters = {
    kind: kindFilter || undefined,
    status: statusFilter,
    severity: severityFilter.length ? severityFilter : undefined,
    mine: mineOnly || undefined,
  };
  const resource = useResource(() => listTickets(filters), { key: filters, keepPreviousData: true, enabled: !noStatus });
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
    <PageLayout
      className="tk-page"
      title="Ticket"
      subtitle={noStatus ? 'Nessuno stato selezionato' : loading ? 'Caricamento…' : `${formatNumber(tickets.length)} ticket`}
      actions={(
        <button
          type="button"
          className="tk-page__btn tk-page__btn--primary"
          onClick={() => setShowCreate(true)}
        >
          + Apri ticket
        </button>
      )}
    >

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
            <span>Stato:</span>
            {(meta?.statuses || []).map((s) => (
              <label key={s} className="tk-filters__chip">
                <input
                  type="checkbox"
                  checked={statusFilter.includes(s)}
                  onChange={() => toggleArr(setStatusFilter, statusFilter, s)}
                />
                {enumLabel('ticketStatus', s)}
              </label>
            ))}
          </div>

          <div className="tk-filters__row">
            <span>Gravità:</span>
            {(meta?.severities || []).map((s) => (
              <label key={s} className="tk-filters__chip">
                <input
                  type="checkbox"
                  checked={severityFilter.includes(s)}
                  onChange={() => toggleArr(setSeverityFilter, severityFilter, s)}
                />
                {enumLabel('ticketSeverity', s)}
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

        {/* Stati e categorie dei filtri: senza, i filtri sono incompleti. */}
        <ResourceError compact resource={metaResource} title="Opzioni dei ticket non caricate" />
        <section className="tk-list">
          {noStatus && <EmptyState icon="🎫" title="Nessuno stato selezionato" text="Seleziona almeno uno stato per vedere i ticket." />}
          {tickets.length >= TICKET_LIST_LIMIT && (
            <div className="plen-banner-warn" role="status">
              Mostrati i primi {formatNumber(TICKET_LIST_LIMIT)} ticket (limite del server): restringi i filtri per vedere gli altri.
            </div>
          )}
          <ListState
            resource={resource}
            errorTitle="Ticket non caricati"
            empty={<EmptyState icon="🎫" title="Nessun ticket con questi filtri" text="Cambia i filtri o apri un nuovo ticket." />}
          >
          <table className="tk-list__table">
            <thead>
              <tr>
                <th>Titolo</th>
                <th>Tipo</th>
                <th>Gravità</th>
                <th>Stato</th>
                <th>Categoria</th>
                <th>Aggiornato</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => (
                <tr
                  key={t.id}
                  className="tk-list__row"
                  {...pressable(() => navigate(`/tickets/${t.id}`))}
                >
                  <td className="tk-list__title">{t.title}</td>
                  <td><KindBadge value={t.kind} /></td>
                  <td><SeverityBadge value={t.severity} /></td>
                  <td><StatusBadge value={t.status} /></td>
                  <td className="tk-list__cat">{optionLabel(meta?.categories, t.category)}</td>
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
    </PageLayout>
  );
}
