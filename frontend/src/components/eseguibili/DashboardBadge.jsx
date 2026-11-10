import React from 'react';
import './DashboardBadge.css';

/**
 * Etichetta DASHBOARD sulla scheda di un Eseguibile che ha la dashboard
 * Grafana (`has_dashboard` di GET /eseguibili, vero solo con la funzione
 * attiva). UNA per tutte le schede: pagina Eseguibili, home, area
 * dell'esecutore (11/11/2026).
 */
export default function DashboardBadge({ item }) {
  if (item?.has_dashboard !== true) return null;
  return (
    <span className="dashboard-badge" title="Dopo l'esecuzione il risultato si apre anche come dashboard">
      DASHBOARD
    </span>
  );
}
