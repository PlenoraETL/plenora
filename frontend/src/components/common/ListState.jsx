/**
 * ListState — gli stati di una risorsa (hooks/useResource) per liste e
 * dettagli (Fase 5 T2, audit R4.7, R4.8, G2).
 *
 *   <ListState resource={res} empty="Nessun ticket" errorTitle="Ticket non caricati">
 *     {(items) => <table>...</table>}
 *   </ListState>
 *
 * Regole:
 *  - primo caricamento: indicatore di caricamento (mai al reload);
 *  - errore senza dati: messaggio d'errore con «Riprova», MAI lo stato vuoto;
 *  - errore con dati gia' caricati: avviso con «Riprova» sopra i dati;
 *  - vuoto: il messaggio `empty`;
 *  - pronto: i figli (funzione dei dati o nodo).
 */
import React from 'react';
import './ListState.css';

export function ResourceError({ title = 'Caricamento non riuscito', message, onRetry, compact = false }) {
  return (
    <div className={`list-state__error${compact ? ' list-state__error--compact' : ''}`} role="alert">
      <div>
        <strong>{title}</strong>
        {message && <span className="list-state__error-msg">{message}</span>}
      </div>
      {onRetry && (
        <button type="button" className="list-state__retry" onClick={() => onRetry()}>Riprova</button>
      )}
    </div>
  );
}

export default function ListState({
  resource,
  children,
  empty = 'Nessun elemento.',
  loading = 'Caricamento...',
  errorTitle = 'Caricamento non riuscito',
  className = '',
}) {
  const { status, data, error, reload, stale } = resource;
  const render = () => (typeof children === 'function' ? children(data) : children);

  if (status === 'idle') return null;
  if (status === 'loading') {
    return (
      <div className={`list-state list-state--loading ${className}`} role="status" aria-live="polite">
        {typeof loading === 'string' ? <span className="list-state__loading">{loading}</span> : loading}
      </div>
    );
  }
  if (status === 'error' && !stale) {
    return (
      <div className={`list-state ${className}`}>
        <ResourceError title={errorTitle} message={error} onRetry={reload} />
      </div>
    );
  }
  if (status === 'error') {
    return (
      <>
        <ResourceError title={errorTitle} message={error} onRetry={reload} compact />
        {render()}
      </>
    );
  }
  if (status === 'empty') {
    return (
      <div className={`list-state list-state--empty ${className}`}>
        {typeof empty === 'string' ? <p className="list-state__empty">{empty}</p> : empty}
      </div>
    );
  }
  return render();
}
