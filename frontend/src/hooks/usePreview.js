/**
 * usePreview.js — Hook per preview asincrona dei dati
 *
 * Avvia una preview (esecuzione parziale con LIMIT) e fa polling
 * finché il task non è completato. Il risultato viene mostrato in PreviewModal.
 *
 * Ritorna: { previewing, previewData, previewError, startPreview, clearPreview }
 */
import { useState, useCallback, useRef } from 'react';
import { previewProject } from '../api';
import { humanizeError } from '../utils/errorMessages';
import { useTaskPolling } from './useTaskPolling';
import { PREVIEW_ROW_LIMIT } from '../utils/previewFormat';

const POLL_INTERVAL = 1000;

/**
 * Hook per gestire la preview dei progetti.
 * La preview gira sul worker — polling fino al completamento.
 */
export function usePreview(projectId, onBeforePreview = null) {
  const [showModal, setShowModal] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // PR-cleanup UX Tier 1: il task preview (`pipeline_task.py:392-398`)
  // puo' propagare result.status='permission_revoked' quando il ruolo
  // utente viene declassato/disabilitato mid-task. Espone flag dedicato
  // cosi' il consumer (PreviewModal) puo' renderizzare
  // <PermissionRevokedBanner> inline invece dell'errore generico.
  const [permissionRevoked, setPermissionRevoked] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState('');

  const cancelledRef = useRef(false);
  const { mountedRef, stopPolling, startPolling } = useTaskPolling({ interval: POLL_INTERVAL });

  const handlePreview = useCallback(async (limit = PREVIEW_ROW_LIMIT) => {
    setShowModal(true);
    setLoading(true);
    setError(null);
    setData(null);
    setPermissionRevoked(false);
    setProgress(0);
    setProgressMessage('Avvio...');
    cancelledRef.current = false;

    try {
      if (onBeforePreview) await onBeforePreview();

      const response = await previewProject(projectId, limit);
      if (!mountedRef.current) return;

      if (response.async && response.task_id) {
        setProgressMessage(response.message || 'Preview in coda...');
        // Il callback ritorna true quando il task è completato → stopPolling automatico
        startPolling(response.task_id, (task) => {
          if (cancelledRef.current) return true;

          setProgress(task.progress || 0);
          setProgressMessage(task.message || '');

          if (task.status === 'success') {
            setLoading(false);
            const result = task.result || {};
            // PR-cleanup UX Tier 1: distingui permission_revoked dal
            // generic failure -> banner inline dedicato lato consumer.
            if (result.status === 'permission_revoked') {
              setPermissionRevoked(true);
              setError(result.message || 'Permessi revocati durante la preview');
            } else if (result.success === false) {
              setError(result.message || 'Errore preview');
            } else {
              setData(result);
            }
            return true;
          } else if (task.status === 'failure') {
            setLoading(false);
            const innerStatus = task.result?.status;
            if (innerStatus === 'permission_revoked') {
              setPermissionRevoked(true);
              setError(task.result?.message || 'Permessi revocati durante la preview');
            } else {
              setError(task.error || 'Errore durante la preview');
            }
            return true;
          }
          return false;
        });
      } else {
        // fallback sincrono (non dovrebbe accadere)
        setLoading(false);
        setProgress(100);
        if (response.success) {
          setData(response);
        } else {
          setError(response.message || 'Errore preview');
        }
      }
    } catch (err) {
      console.error('[usePreview] Errore:', err);
      setLoading(false);
      setError(humanizeError(err).message);
    }
  }, [projectId, onBeforePreview, startPolling, mountedRef]);

  const closeModal = useCallback(() => {
    setShowModal(false);
  }, []);

  const reset = useCallback(() => {
    stopPolling();
    setShowModal(false);
    setData(null);
    setLoading(false);
    setError(null);
    setPermissionRevoked(false);
    setProgress(0);
    setProgressMessage('');
  }, [stopPolling]);

  return {
    showModal, data, loading, error, permissionRevoked,
    progress, progressMessage,
    handlePreview, closeModal, reset,
  };
}

export default usePreview;
