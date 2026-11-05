/**
 * EmptyState — l'unico stato vuoto delle pagine (Fase 5 T7, linea guida G2,
 * voci S2, S3, S4, DB6, P16, P18).
 *
 * Centrato, con icona, titolo, testo e l'azione che lo risolve. `compact`:
 * una riga sola (sezione vuota della dashboard, DB6: niente riquadro enorme).
 * Con un filtro attivo il chiamante passa il testo del filtro (mai «crea il
 * primo» quando la lista è vuota solo per il filtro, P18).
 *
 * KPI e filtri a zero elementi: li nasconde il chiamante (G2); il test
 * tests/shared/pageLayout.contract.test.jsx vieta stati vuoti fatti a mano
 * nelle pagine.
 */
import React from 'react';
import './EmptyState.css';

export default function EmptyState({ icon = null, title, text = null, action = null, compact = false, className = '' }) {
  return (
    <div className={`plen-empty${compact ? ' plen-empty--compact' : ''}${className ? ` ${className}` : ''}`} role="status">
      {icon && <div className="plen-empty__icon" aria-hidden="true">{icon}</div>}
      <div className="plen-empty__body">
        <div className="plen-empty__title">{title}</div>
        {text && <div className="plen-empty__text">{text}</div>}
      </div>
      {action && <div className="plen-empty__action">{action}</div>}
    </div>
  );
}
