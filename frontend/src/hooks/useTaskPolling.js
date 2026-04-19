/**
 * useTaskPolling.js — Hook base per polling di task Celery
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

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // Cleanup su unmount: cancella interval direttamente dalla ref
  useEffect(() => () => {
    if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  /**
   * Avvia il polling di un task Celery.
   *
   * @param {string} taskId - ID del task da monitorare
   * @param {Function} onStatus - Callback chiamata ad ogni poll con i dati del task.
   *   Riceve (data) dove data = risposta di getTaskStatus().
   *   Deve restituire true se il task è completato (per fermare il polling).
   */
  const startPolling = useCallback((taskId, onStatus) => {
    stopPolling();

    const poll = async () => {
      try {
        const data = await getTaskStatus(taskId);
        if (!mountedRef.current) return;
        if (!data) return;

        const done = onStatus(data);
        if (done) stopPolling();
      } catch (e) {
        // 404 = task non ancora registrato, aspetta il prossimo poll
        if (e.message && e.message.includes('non trovato')) return;
        console.error('[useTaskPolling] Errore polling:', e);
      }
    };

    pollingRef.current = setInterval(poll, interval);
  }, [interval, stopPolling]);

  return { mountedRef, pollingRef, stopPolling, startPolling };
}

export default useTaskPolling;
