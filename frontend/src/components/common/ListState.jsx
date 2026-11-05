/**
 * ListState — gli stati di una risorsa (hooks/useResource) per liste e
 * dettagli (Fase 5 T2, audit R4.7, R4.8, G2).
 *
 *   <ListState resource={res} empty="Nessun ticket" errorTitle="Ticket non caricati">
 *     {(items) => <table>...</table>}
 *   </ListState>
 *
 * Regole (Fase 5, correzione dopo la conferma di T2+T4, punti 3+4): con
 * status 'error' l'errore e' SEMPRE visibile.
 *  - primo caricamento: indicatore di caricamento (mai al reload);
 *  - errore senza dati: messaggio d'errore con «Riprova», MAI lo stato vuoto;
 *  - errore con dati gia' caricati (`stale`, solo dati della STESSA chiave:
 *    hooks/useResource): avviso «Dati non aggiornati» con «Riprova» sopra i
 *    dati;
 *  - vuoto: il messaggio `empty`;
 *  - pronto: i figli (funzione dei dati o nodo).
 *
 * Chi non usa ListState mostra l'errore con <ResourceError resource={res} />
 * (stesse due forme, nulla senza errore). Il test
 * tests/shared/resourceErrorVisible.contract.test.jsx enumera i consumatori.
 */
import React from 'react';
import './ListState.css';
import { READ_ONLY_SAFE } from './readOnlySafe';

export const STALE_TITLE = 'Dati non aggiornati';
const STALE_NOTE = "Sono mostrati i dati dell'ultimo caricamento riuscito.";

/**
 * Errore di una risorsa. Due usi:
 *  - <ResourceError resource={res} title="..." />: decide da solo (nulla
 *    senza errore; avviso «Dati non aggiornati» sopra dati gia' caricati;
 *    errore pieno senza dati);
 *  - <ResourceError message onRetry />: un errore che non viene da useResource.
 */
export function ResourceError({ resource = null, title = 'Caricamento non riuscito', message, onRetry, compact = false }) {
  let stale = false;
  if (resource) {
    if (resource.status !== 'error') return null;
    stale = Boolean(resource.stale);
    message = message ?? resource.error;
    onRetry = onRetry ?? resource.reload;
  }
  return (
    <div className={`list-state__error${compact || stale ? ' list-state__error--compact' : ''}${stale ? ' list-state__error--stale' : ''}`} role="alert">
      <div>
        <strong>{stale ? STALE_TITLE : title}</strong>
        {message && <span className="list-state__error-msg">{stale ? `${title}: ${message}` : message}</span>}
        {stale && <span className="list-state__error-note">{STALE_NOTE}</span>}
      </div>
      {onRetry && (
        <button type="button" {...READ_ONLY_SAFE} className="list-state__retry" onClick={() => onRetry()}>Riprova</button>
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
  const { status, data, stale } = resource;
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
        <ResourceError resource={resource} title={errorTitle} />
      </div>
    );
  }
  if (status === 'error') {
    return (
      <>
        <ResourceError resource={resource} title={errorTitle} />
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
