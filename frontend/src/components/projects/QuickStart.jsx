/**
 * QuickStart.jsx — Onboarding empty-state della dashboard (Epic B).
 *
 * Sostituisce le 3 empty-box passive ("Nessun progetto…") quando l'utente non
 * ha ancora progetti. Guida il percorso happy-path login → primo run con una
 * checklist a step, e risolve il vicolo cieco "per un progetto Estrai serve una
 * connessione" rendendo esplicito (e cliccabile) il prerequisito.
 *
 * Lo step "connessione" è stateful: se l'utente ha già una sorgente dati
 * (connessione SQL/ArcGIS, API esterna o datasource) risulta già completato.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';

export default function QuickStart({ hasDataSource, canCreate, onNewProject }) {
  const navigate = useNavigate();

  // Viewer senza progetti: niente CTA di creazione, solo messaggio gentile.
  if (!canCreate) {
    return (
      <div className="pl__qs">
        <div className="pl__qs-icon">⬡</div>
        <h2 className="pl__qs-title">Nessun progetto disponibile</h2>
        <p className="pl__qs-sub">
          Non ci sono ancora progetti da visualizzare. Chiedi a un amministratore
          di crearne uno o di condividerlo con te.
        </p>
      </div>
    );
  }

  const steps = [
    {
      n: 1,
      done: hasDataSource,
      title: 'Collega una sorgente dati',
      desc: 'Una connessione SQL/ArcGIS, un’API esterna o un datasource. È il punto di partenza per estrarre i dati.',
      action: hasDataSource
        ? null
        : { label: 'Crea connessione', onClick: () => navigate('/connections') },
    },
    {
      n: 2,
      done: false,
      title: 'Crea il tuo primo progetto',
      desc: 'Parti da un progetto Estrai per portare dentro i dati dalla sorgente collegata.',
      action: { label: '＋ Nuovo progetto Estrai', primary: true, onClick: () => onNewProject('estrai') },
    },
    {
      n: 3,
      done: false,
      title: 'Costruisci la pipeline ed esegui',
      desc: 'Nell’editor trascini i nodi, colleghi l’output e premi Esegui: il risultato appare in anteprima nel nodo.',
      action: null,
    },
  ];

  return (
    <div className="pl__qs">
      <div className="pl__qs-icon">⬡</div>
      <h2 className="pl__qs-title">Benvenuto in Plenora</h2>
      <p className="pl__qs-sub">
        Tre passi per arrivare al tuo primo risultato. Inizia da qui.
      </p>

      <ol className="pl__qs-steps">
        {steps.map((s) => (
          <li key={s.n} className={`pl__qs-step${s.done ? ' pl__qs-step--done' : ''}`}>
            <div className="pl__qs-step-badge">{s.done ? '✓' : s.n}</div>
            <div className="pl__qs-step-body">
              <div className="pl__qs-step-title">{s.title}</div>
              <div className="pl__qs-step-desc">{s.desc}</div>
              {s.action && (
                <button
                  type="button"
                  className={`pl__btn ${s.action.primary ? 'pl__btn--primary' : 'pl__btn--ghost'} pl__btn--sm pl__qs-step-btn`}
                  onClick={s.action.onClick}
                >
                  {s.action.label}
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
