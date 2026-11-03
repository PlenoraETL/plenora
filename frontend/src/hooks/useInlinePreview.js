/**
 * useInlinePreview.js — Anteprima compatta NON-modale (#5).
 *
 * Variante leggera di `usePreview`: avvia una preview asincrona (esecuzione
 * parziale con LIMIT piccolo) e fa polling fino al completamento, ma invece
 * di aprire `PreviewModal` espone lo stato così il `ResultNode` può mostrare
 * le prime righe INLINE dentro al nodo subito dopo un'esecuzione riuscita.
 *
 * Ritorna: { status, data, error, run, clear }
 *   status: 'idle' | 'loading' | 'success' | 'empty' | 'error'
 *   data:   { columns:[{name,type}], rows:[{col:val}], total_count, preview_count, truncated }
 */
import { useState, useCallback, useRef } from 'react';
import { previewProject } from '../api';
import { humanizeError } from '../utils/errorMessages';
import { useTaskPolling } from './useTaskPolling';

const POLL_INTERVAL = 1000;

export function useInlinePreview(projectId) {
  const [status, setStatus] = useState('idle');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const cancelledRef = useRef(false);
  const { mountedRef, stopPolling, startPolling } = useTaskPolling({ interval: POLL_INTERVAL });

  const run = useCallback(async (limit = 10, nodeId = null) => {
    if (!projectId) return;
    setStatus('loading');
    setError(null);
    setData(null);
    cancelledRef.current = false;

    const settle = (result) => {
      // Allinea la semantica a usePreview: distingui revoca permessi / fallimento
      // applicativo dal successo con righe.
      if (result.status === 'permission_revoked' || result.success === false) {
        setError(result.message || 'Errore anteprima');
        setStatus('error');
        return;
      }
      setData(result);
      setStatus((result.rows || []).length === 0 ? 'empty' : 'success');
    };

    try {
      const response = await previewProject(projectId, limit, nodeId);
      if (!mountedRef.current) return;

      if (response.async && response.task_id) {
        // Il callback ritorna true quando il task è completato → stopPolling automatico
        startPolling(response.task_id, (task) => {
          if (cancelledRef.current) return true;
          if (task.status === 'success') {
            settle(task.result || {});
            return true;
          }
          if (task.status === 'failure') {
            const inner = task.result?.status;
            setError(inner === 'permission_revoked'
              ? (task.result?.message || 'Permessi revocati durante l\'anteprima')
              : (task.error || 'Errore durante l\'anteprima'));
            setStatus('error');
            return true;
          }
          return false;
        });
      } else if (response.success) {
        // fallback sincrono (non dovrebbe accadere)
        settle(response);
      } else {
        setError(response.message || 'Errore anteprima');
        setStatus('error');
      }
    } catch (err) {
      console.error('[useInlinePreview] Errore:', err);
      setError(humanizeError(err).message);
      setStatus('error');
    }
  }, [projectId, startPolling, mountedRef]);

  const clear = useCallback(() => {
    cancelledRef.current = true;
    stopPolling();
    setStatus('idle');
    setData(null);
    setError(null);
  }, [stopPolling]);

  return { status, data, error, run, clear };
}

export default useInlinePreview;
