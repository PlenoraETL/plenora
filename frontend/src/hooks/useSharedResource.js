/**
 * useSharedResource.js — una risorsa condivisa (utils/sharedResources) in un
 * componente, sopra hooks/useResource (UI-1, 06/11/2026).
 *
 *   const files = useSharedResource('files', { select: (d) => d.files || [] });
 *   const tables = useSharedResource('connectionTables', { params: [id], enabled: Boolean(id) });
 *
 * Stessi stati, Riprova e regole di useResource, piu':
 *  - dati gia' in cache: subito, senza «Caricamento...» (anche scaduti: si
 *    mostrano e si chiedono al server in background);
 *  - una sola richiesta per tutti i componenti montati con la stessa risorsa;
 *  - la risorsa invalidata (scrittura) o arrivata nuova: si ricarica;
 *  - reload()/Riprova/poll: dal server.
 */
import { useCallback } from 'react';
import { useResource } from './useResource';
import { readShared, peekShared } from '../utils/sharedResources';

const identity = (d) => d;

/**
 * @param {string} name                 nome in SHARED_RESOURCES
 * @param {Object} [opts]
 * @param {Array} [opts.params]         parametri della risorsa (es. [id])
 * @param {(raw) => any} [opts.select]  dati per il componente (funzione stabile o pura)
 * @param {number} [opts.maxAgeMs]      eta' accettata al posto di freshMs della tabella
 * @returns la risorsa di useResource
 */
export function useSharedResource(name, { params = [], select = identity, maxAgeMs, enabled = true, ...opts } = {}) {
  const resourceKey = [name, ...params];
  const keyText = JSON.stringify(resourceKey);
  const loader = useCallback(
    ({ force } = {}) => readShared(name, params, { force, maxAgeMs }).then(select),
    // La chiave descrive nome e parametri; select e maxAgeMs sono della chiamata.
    [keyText, maxAgeMs], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const cached = enabled ? peekShared(name, params) : null;
  return useResource(loader, {
    ...opts,
    enabled,
    key: opts.key ?? keyText,
    shared: [resourceKey],
    initialData: cached ? () => select(cached.data) : undefined,
    revalidate: Boolean(cached && !cached.fresh),
  });
}

export default useSharedResource;
