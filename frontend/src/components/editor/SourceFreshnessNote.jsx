/**
 * SourceFreshnessNote — di quando sono i dati delle sorgenti che
 * un'esecuzione «con i risultati salvati» userebbe (UI-3, U18).
 *
 * Prima «Usa cache esistente» era la scelta predefinita senza dire quanto
 * fossero vecchi i dati. Il backend annota ogni progetto sorgente del
 * canvas aperto con `source_freshness` (core/projects/source_freshness:
 * ultimo risultato, progetto modificato dopo): qui una riga per sorgente,
 * con la data dell'ultimo risultato o il motivo per rieseguirla.
 */
import React from 'react';
import { useCanvasDerived } from '../../hooks/useCanvasDerived';
import { nodeTitle } from '../canvas/nodeDescriptors';
import { formatDateTime } from '../../utils/format';

/** Righe { id, name, text, stale } per i progetti sorgente del canvas. */
export function sourceFreshnessRows(nodes) {
  return (nodes || [])
    .filter(n => n.type === 'project_source' && n.data?.source_project_id)
    .map((n) => {
      const f = n.data.source_freshness || null;
      const name = nodeTitle(n.type, n.data);
      if (!f) return { id: n.id, name, text: 'data dell\'ultimo risultato non nota (si vede dopo il salvataggio)', stale: false };
      if (f.stale) return { id: n.id, name, text: f.message || 'da rieseguire', stale: true };
      return { id: n.id, name, text: `risultato del ${formatDateTime(f.latest_result_at)}`, stale: false };
    });
}

// Righe ricalcolate solo quando cambia il contenuto del canvas (UI-2):
// spostare un nodo non ridisegna la nota.
const freshnessOf = (st) => sourceFreshnessRows(st.nodes);

export default function SourceFreshnessNote() {
  const rows = useCanvasDerived(freshnessOf);
  if (!rows.length) return null;
  return (
    <ul className="source-freshness" aria-label="Aggiornamento delle sorgenti">
      {rows.map(r => (
        <li key={r.id} className={r.stale ? 'source-freshness__row source-freshness__row--stale' : 'source-freshness__row'}>
          <strong>{r.name}</strong>: {r.text}
        </li>
      ))}
    </ul>
  );
}
