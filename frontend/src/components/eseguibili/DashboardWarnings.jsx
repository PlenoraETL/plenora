import React from 'react';
import './BindingWarnings.css';

/**
 * Avvisi sulla dashboard Grafana di un Eseguibile (`dashboard_warnings` del
 * dettaglio, solo a chi puo' modificarlo: utils/eseguibileDashboard
 * .dashboardWarningsFor): colonne usate dalla dashboard che il risultato
 * non ha piu'. Mai bloccante: la dashboard si apre, quei pannelli restano
 * vuoti finche' non si corregge in Grafana.
 */
export default function DashboardWarnings({ warnings }) {
  if (!warnings || warnings.length === 0) return null;
  return (
    <div className="binding-warnings" role="status">
      <strong>⚠ Dashboard da controllare in Grafana</strong>
      <ul className="binding-warnings__list">
        {warnings.map((w, i) => (
          <li key={`${w.column || ''}|${i}`}>
            {w.column ? <><code>{w.column}</code>: </> : null}{w.motivo}
          </li>
        ))}
      </ul>
    </div>
  );
}
