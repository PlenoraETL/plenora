/**
 * useResource.js — IL caricamento dei dati di pagine e componenti (Fase 5
 * T2, audit R4.1-R4.9, R4.13-R4.18, N3, N10).
 *
 *   const res = useResource(() => getTicket(id), { key: id });
 *   res.status   // 'idle' | 'loading' | 'error' | 'empty' | 'ready'
 *   res.data, res.error, res.initialLoading, res.refreshing
 *   res.reload() res.retry() res.setData(updater)
 *
 * Regole (una per caso):
 *  - ogni richiesta ha un id: una risposta superata (nuova richiesta,
 *    chiave cambiata, smontaggio) non scrive mai;
 *  - `key` cambiata = nuova richiesta e i dati della chiave di prima non si
 *    mostrano piu' (mai il ticket A sotto l'indirizzo del ticket B);
 *  - errore = stato `error` con il testo unico di utils/errorMessages, mai
 *    una lista vuota, e SEMPRE visibile: i dati gia' caricati PER QUESTA
 *    chiave restano (`stale`: avviso "dati non aggiornati" + Riprova sopra i
 *    dati, components/common/ListState o <ResourceError resource>); i dati
 *    di un'altra chiave (keepPreviousData) non sopravvivono a un errore:
 *    il risultato del filtro di prima non si mostra mai come risultato del
 *    filtro nuovo. Riprova = reload();
 *  - `initialLoading` solo al primo caricamento di una chiave: un reload dopo
 *    un'azione non sostituisce la pagina con lo spinner (`refreshing`);
 *  - `refreshInterval`: aggiornamento periodico con un profilo di
 *    utils/poller (nome di POLL_PROFILES): un poll alla volta, fermo con la
 *    scheda nascosta (riparte subito quando torna visibile), backoff quando
 *    i dati non cambiano, durata massima (UI-1, C19-C23);
 *  - `shared`: le risorse condivise (utils/sharedResources) che il
 *    caricatore legge: quando una viene invalidata (scrittura) o arriva
 *    nuova da un'altra richiesta, i dati si ricaricano in background;
 *  - il caricatore riceve `{ signal, force }`: `force` e' true per reload(),
 *    Riprova e i poll (la cache condivisa non basta, serve il server);
 *  - `revalidate` con `initialData`: si parte dai dati iniziali (es. cache
 *    scaduta) e si chiede comunque al server, in background;
 *  - `enabled: false` = nessuna richiesta (stato `idle`);
 *  - `keepPreviousData`: la chiave cambia ma la risorsa e' la stessa (es.
 *    filtri): i dati di prima restano visibili durante la nuova richiesta;
 *  - `initialData` (es. una cache gia' piena): si parte da li', senza
 *    richiesta al montaggio; reload() la chiede comunque.
 *
 * Il componente <ListState> (components/common/ListState) mostra questi
 * stati per le liste. Il test tests/shared/asyncArchitecture.contract.test.js
 * vieta useEffect + chiamate API a mano nelle pagine.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { requestErrorMessage } from '../utils/errorMessages';
import { pollProfile, startPoller } from '../utils/poller';
import { subscribeShared } from '../utils/sharedCache';

const defaultIsEmpty = (data) => data == null || (Array.isArray(data) && data.length === 0);

const keyOf = (key) => (typeof key === 'string' ? key : JSON.stringify(key ?? null));

/**
 * @param {(ctx: {signal: AbortSignal}) => Promise<any>} loader
 * @param {Object} [opts]
 * @param {any} [opts.key]                chiave della risorsa (cambia = nuova richiesta)
 * @param {boolean} [opts.enabled=true]
 * @param {string|0} [opts.refreshInterval=0] profilo di utils/poller (POLL_PROFILES)
 * @param {boolean} [opts.pauseWhenHidden=true]
 * @param {Array} [opts.shared]           risorse condivise lette dal caricatore
 * @param {boolean} [opts.revalidate]     con initialData: chiede comunque al server
 * @param {(data) => boolean} [opts.isEmpty]
 * @param {(err) => string} [opts.errorMessage]
 * @param {any|(() => any)} [opts.initialData]
 */
export function useResource(loader, {
  key = null,
  enabled = true,
  refreshInterval = 0,
  pauseWhenHidden = true,
  isEmpty = defaultIsEmpty,
  errorMessage = requestErrorMessage,
  initialData = undefined,
  keepPreviousData = false,
  shared = null,
  revalidate = false,
} = {}) {
  const keepRef = useRef(keepPreviousData);
  keepRef.current = keepPreviousData;
  const k = keyOf(key);
  const [state, setState] = useState(() => {
    const data = typeof initialData === 'function' ? initialData() : initialData;
    // dataKey: la chiave a cui appartengono i dati (con keepPreviousData puo'
    // restare quella di prima mentre la nuova richiesta e' in corso).
    return { key: k, dataKey: data === undefined ? null : k, data, error: null, rawError: null, loading: enabled && data === undefined };
  });
  const skipFirstRef = useRef(state.data !== undefined);
  const reqRef = useRef(0);
  const abortRef = useRef(null);
  const mountedRef = useRef(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const errorMessageRef = useRef(errorMessage);
  errorMessageRef.current = errorMessage;
  const keyRef = useRef(k);
  keyRef.current = k;
  const revalidateRef = useRef(revalidate);
  revalidateRef.current = revalidate;
  const dataRef = useRef(state.data);
  dataRef.current = state.data;

  // background: aggiornamento periodico (l'errore mostrato resta finche'
  // non arriva una risposta); altrimenti Riprova/reload (l'errore si toglie
  // e si vede il caricamento).
  const request = useCallback((background, force = !background) => {
    const id = ++reqRef.current;
    abortRef.current?.abort?.();
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    abortRef.current = controller;
    const forKey = keyRef.current;
    // Il caricatore della chiave di QUESTA richiesta (non quello del render dopo).
    const loader = loaderRef.current;
    setState(s => (s.key === forKey || keepRef.current
      ? { ...s, key: forKey, loading: true, ...(background ? {} : { error: null, rawError: null }) }
      : { key: forKey, dataKey: null, data: undefined, error: null, rawError: null, loading: true }));
    // La richiesta parte subito (nello stesso giro), un errore sincrono del
    // caricatore e' un errore della richiesta.
    let pending;
    try {
      pending = Promise.resolve(loader({ signal: controller?.signal, force }));
    } catch (err) {
      pending = Promise.reject(err);
    }
    return pending
      .then(
        (data) => {
          if (!mountedRef.current || id !== reqRef.current) return undefined;
          setState({ key: forKey, dataKey: forKey, data, error: null, rawError: null, loading: false });
          return data;
        },
        (err) => {
          if (!mountedRef.current || id !== reqRef.current) return undefined;
          if (err?.name === 'CanceledError' || err?.name === 'AbortError') return undefined;
          console.warn('[useResource] Caricamento non riuscito:', err);
          // Solo i dati di QUESTA chiave restano sotto l'errore (stale).
          setState(s => ({
            key: forKey,
            dataKey: s.dataKey === forKey ? forKey : null,
            data: s.dataKey === forKey ? s.data : undefined,
            error: errorMessageRef.current(err),
            rawError: err,
            loading: false,
          }));
          return undefined;
        },
      );
  }, []);
  // Primo caricamento di una chiave: la cache condivisa vale (force false).
  const firstLoad = useCallback(() => request(false, false), [request]);
  // reload/Riprova: dal server.
  const load = useCallback(() => request(false, true), [request]);
  const poll = useCallback(() => request(true, true), [request]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      reqRef.current += 1;
      abortRef.current?.abort?.();
    };
  }, []);

  // Chiave o abilitazione cambiate: nuova richiesta.
  useEffect(() => {
    if (!enabled) {
      reqRef.current += 1;
      setState({ key: k, dataKey: null, data: undefined, error: null, rawError: null, loading: false });
      return;
    }
    if (skipFirstRef.current) {
      skipFirstRef.current = false;
      if (revalidateRef.current) request(true, false);
      return;
    }
    firstLoad();
  }, [k, enabled, firstLoad, request]);

  // Aggiornamento periodico (utils/poller): parte dopo la prima risposta
  // della chiave e resta lo stesso poller tra un poll e l'altro.
  const settledOnce = state.key === k && (state.dataKey === k || state.error != null);
  const loadingRef = useRef(false);
  loadingRef.current = state.loading;
  const profile = useMemo(() => pollProfile(refreshInterval), [refreshInterval]);
  useEffect(() => {
    if (!enabled || !profile || !settledOnce) return undefined;
    return startPoller(() => {
      // Una richiesta gia' in corso (reload dell'utente) vale come giro.
      if (loadingRef.current) return { changed: true };
      const before = JSON.stringify(dataRef.current ?? null);
      // Nessun dato (errore, risposta superata) = nessuna novita': backoff.
      return poll().then((data) => ({ changed: data !== undefined && JSON.stringify(data) !== before }));
    }, { ...profile, pauseWhenHidden, resumeOnVisible: true });
  }, [enabled, profile, pauseWhenHidden, settledOnce, poll]);

  // Risorse condivise: invalidate (scrittura) o arrivate nuove da un'altra
  // richiesta -> ricarica in background (dalla cache se gia' fresca).
  const sharedKey = shared ? JSON.stringify(shared) : null;
  useEffect(() => {
    if (!enabled || !sharedKey) return undefined;
    return subscribeShared(JSON.parse(sharedKey), () => { if (mountedRef.current) request(true, false); });
  }, [enabled, sharedKey, request]);

  // Dati scritti da chi li conosce (azione riuscita, altra copia caricata):
  // sono i dati di questa chiave e l'errore precedente non vale piu'.
  const setData = useCallback((updater) => {
    setState(s => ({ ...s, key: keyRef.current, dataKey: keyRef.current, data: typeof updater === 'function' ? updater(s.key === keyRef.current ? s.data : undefined) : updater, error: null, rawError: null }));
  }, []);

  // keepPreviousData: la stessa risorsa con parametri diversi (non un'altra
  // risorsa): i dati di prima restano finche' arrivano i nuovi.
  const sameKey = state.key === k || keepPreviousData;
  const data = sameKey ? state.data : undefined;
  const error = sameKey ? state.error : null;
  const loading = enabled && (!sameKey || state.loading);
  const hasData = data !== undefined;

  let status;
  if (!enabled) status = 'idle';
  else if (error) status = 'error';
  else if (!hasData) status = 'loading';
  else status = isEmpty(data) ? 'empty' : 'ready';

  return useMemo(() => ({
    status,
    data,
    error,
    rawError: sameKey ? state.rawError : null,
    loading,
    initialLoading: loading && !hasData,
    refreshing: loading && hasData,
    stale: Boolean(error) && hasData,
    hasData,
    reload: load,
    retry: load,
    setData,
  }), [status, data, error, sameKey, state.rawError, loading, hasData, load, setData]);
}

export default useResource;
