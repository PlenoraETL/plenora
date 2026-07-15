/**
 * useTaskPolling.js — Hook base per polling di task asincroni
 *
 * Fornisce il meccanismo comune di polling usato da useExecution e usePreview:
 * - Guard mountedRef contro setState post-unmount
 * - Gestione interval con cleanup automatico su unmount
 * - getTaskStatus() con gestione 404 (task non ancora registrato)
 *
 * @param {Object} options
 * @param {number} options.interval - Intervallo polling in ms (default 1000)
 * @returns {{ mountedRef, pollingRef, stopPolling, startPolling }}
 */
import { useCallback, useRef, useEffect } from 'react';
import { getTaskStatus } from '../api';

export function useTaskPolling({ interval = 1000 } = {}) {
  const mountedRef = useRef(true);
  const pollingRef = useRef(null);
  const generationRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
    };
  }, []);

  // Cleanup su unmount: cancella interval direttamente dalla ref
  useEffect(() => () => {
    if (pollingRef.current) { clearTimeout(pollingRef.current); pollingRef.current = null; }
  }, []);

  const stopPolling = useCallback(() => {
    generationRef.current += 1;
    if (pollingRef.current) {
      clearTimeout(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  /**
   * Avvia il polling di un task asincrono.
   *
   * Garanzia: stopPolling() viene chiamato prima di pianificare il nuovo
   * interval, quindi una seconda startPolling() sullo stesso hook
   * non lascia mai due interval attivi (vedi RESOURCE_LEAKS.md).
   *
   * @param {string} taskId - ID del task da monitorare
   * @param {Function} onStatus - Callback chiamata ad ogni poll con i dati del task.
   *   Riceve (data) dove data = risposta di getTaskStatus().
   *   Deve restituire true se il task è completato (per fermare il polling).
   * @param {Object} [opts]
   * @param {boolean} [opts.immediate=false] - Se true esegue una prima poll
   *   subito (fire-and-forget) prima di pianificare l'interval. Utile quando
   *   l'UI deve mostrare il primo stato senza aspettare `interval` ms.
   * @param {Function} [opts.onError] - Callback opzionale per errori non-404
   *   propagati da getTaskStatus o da onStatus. Se restituisce true il
   *   polling viene fermato; altrimenti continua. In assenza di onError
   *   l'errore viene loggato (console.error) e il polling continua.
   */
  const startPolling = useCallback((taskId, onStatus, opts = {}) => {
    stopPolling();
    const { immediate = false, onError = null } = opts;
    const generation = generationRef.current;

    const scheduleNext = () => {
      if (!mountedRef.current || generation !== generationRef.current) return;
      pollingRef.current = setTimeout(poll, interval);
    };

    const poll = async () => {
      if (!mountedRef.current || generation !== generationRef.current) return;
      let completed = false;
      try {
        const data = await getTaskStatus(taskId);
        if (!mountedRef.current || generation !== generationRef.current) return;
        if (!data) {
          scheduleNext();
          return;
        }

        const done = onStatus(data);
        if (done) {
          completed = true;
          stopPolling();
        }
      } catch (e) {
        if (!mountedRef.current || generation !== generationRef.current) return;
        // 404 = task non ancora registrato, aspetta il prossimo poll
        if (e.response?.status !== 404 && onError) {
          const stop = onError(e);
          if (stop) {
            completed = true;
            stopPolling();
          }
        } else if (e.response?.status !== 404) {
          console.error('[useTaskPolling] Errore polling:', e);
        }
      }
      if (!completed) scheduleNext();
    };

    if (immediate) poll();
    else scheduleNext();
  }, [interval, stopPolling]);

  return { mountedRef, pollingRef, stopPolling, startPolling };
}

export default useTaskPolling;
