/**
 * ColumnStatusNote — stato delle colonne di un nodo, in modo sobrio
 * (Blocco A, regola 4). Lo montano TUTTI i lettori della fonte unica
 * (chi chiama useColumnsOf, test di enumerazione in
 * tests/components/ColumnReaders.contract.test.jsx), sempre, anche con zero
 * colonne. Riceve il risultato di columnsOf / useColumnsOf e mostra, in
 * quest'ordine:
 *  - "colonne in calcolo": il server non ha ancora uno schema per questo
 *    nodo (prima della prima risposta, o per un nodo appena aggiunto);
 *  - "nomi incerti" / "tipo incerto" / "ordine incerto" (es. a valle di un
 *    Pivot), dallo stato per aspetto del server;
 *  - "colonne non aggiornate (ultime note)" con "Riprova" se l'ultima
 *    richiesta e' fallita (le colonne restano le ultime note).
 *
 * ColumnSyncBadge e' lo stesso stato per l'intero editor, nella barra dei
 * tre editor (EditorToolbar).
 */
import React from 'react';
import { uncertainAspects } from '../../utils/columnSource';
import { refreshColumns } from '../../hooks/useColumns';
import useColumnStore from '../../store/useColumnStore';
import '../transforms/panels/panels.css';
import './ColumnStatusNote.css';

/** Stato del nodo, in parole: [{ kind: 'pending'|'uncertain'|'error', label, title }]. */
export function columnStatusItems(result) {
  if (!result) return [];
  const failed = result.status === 'error';
  const items = [];
  if (!result.known && !failed) items.push({ kind: 'pending', label: 'colonne in calcolo', title: result.state?.reason || '' });
  if (result.known) {
    for (const a of uncertainAspects(result.state)) items.push({ kind: 'uncertain', label: a, title: result.state?.reason || '' });
  }
  if (failed) items.push({ kind: 'error', label: 'colonne non aggiornate (ultime note)', title: result.error || '' });
  return items;
}

export default function ColumnStatusNote({ result }) {
  const items = columnStatusItems(result);
  if (!items.length) return null;
  return (
    <div className="ppanel__chips colstatus" data-testid="column-status">
      {items.map(item => (
        <span
          key={item.kind + item.label}
          className={`ppanel__tag colstatus__tag colstatus__tag--${item.kind}${item.kind === 'pending' ? '' : ' ppanel__tag--warn'}`}
          role={item.kind === 'error' ? 'status' : undefined}
          title={item.title}
        >
          {item.kind === 'pending' && <span className="colstatus__dot" aria-hidden="true" />}
          {item.label}
          {item.kind === 'error' && (
            <>
              {' '}
              <button type="button" className="ppanel__mini colstatus__retry" onClick={refreshColumns}>Riprova</button>
            </>
          )}
        </span>
      ))}
    </div>
  );
}

/** Stato delle colonne dell'editor, per la barra (tutti e tre gli editor). */
export function ColumnSyncBadge() {
  const status = useColumnStore(s => s.status);
  const error = useColumnStore(s => s.error);
  if (status === 'loading') {
    return (
      <span className="etb-colsync etb-colsync--loading" role="status" data-testid="column-sync-badge">
        <span className="colstatus__dot" aria-hidden="true" /> Colonne in calcolo…
      </span>
    );
  }
  if (status === 'error') {
    return (
      <button
        type="button"
        className="etb-colsync etb-colsync--error"
        data-testid="column-sync-badge"
        title={`Colonne non aggiornate: ${error || 'errore del server'}. Clicca per ritentare.`}
        onClick={refreshColumns}
      >
        Colonne non aggiornate · Riprova
      </button>
    );
  }
  return null;
}
