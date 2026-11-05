import React from 'react';
import './BindingWarnings.css';

/**
 * Avviso sui binding di un Eseguibile pubblicato che l'esecuzione non legge
 * piu' (`binding_warnings` di GET /eseguibili e /eseguibili/{id}, calcolati
 * dal backend con la stessa regola della pubblicazione). L'esecuzione resta
 * com'e': l'input parte e il suo valore e' ignorato. Mai bloccante.
 */
export default function BindingWarnings({ warnings }) {
  if (!warnings || warnings.length === 0) return null;
  return (
    <div className="binding-warnings" role="status">
      <strong>⚠ Input collegato a un campo che l&apos;esecuzione non legge: ripubblica dopo averlo corretto</strong>
      <ul className="binding-warnings__list">
        {warnings.map(w => (
          <li key={`${w.input_key}|${w.target_node_id}|${w.target_field || ''}`}>
            <code>{w.input_key}</code> → {w.target_node_id}{w.target_field ? `.${w.target_field}` : ''}: {w.motivo}
          </li>
        ))}
      </ul>
    </div>
  );
}
