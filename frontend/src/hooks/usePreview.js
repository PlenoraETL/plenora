/**
 * usePreview.js — Anteprima completa dell'Output (PreviewModal).
 *
 * Sopra il ciclo unico di hooks/useTask (Fase 5 T2, audit R4.12, R1-N2):
 * ogni anteprima ha un id di richiesta (un "Aggiorna" con limiti diversi o
 * una seconda anteprima fanno vincere l'ultima, mai i dati della prima),
 * annullata/permessi revocati/task perso hanno un esito, chiudere la
 * finestra ferma il polling, il cambio di progetto la azzera.
 *
 * Ritorna: { showModal, data, loading, error, permissionRevoked, progress,
 *            progressMessage, handlePreview, closeModal, reset }
 */
import { useState, useCallback } from 'react';
import { previewProject } from '../api';
import { requestErrorMessage } from '../utils/errorMessages';
import { useTask } from './useTask';
import { PREVIEW_ROW_LIMIT } from '../utils/previewFormat';

const POLL_INTERVAL = 1000;

/** Testo dell'esito di un'anteprima non riuscita (null se riuscita o in corso). */
export function previewErrorOf(state) {
  if (state.phase === 'revoked') return 'Anteprima annullata';
  if (state.phase === 'failure' || state.phase === 'lost' || state.phase === 'permission_revoked') {
    return state.error || "Errore durante l'anteprima";
  }
  return null;
}

export function usePreview(projectId, onBeforePreview = null) {
  const [showModal, setShowModal] = useState(false);
  // Errore prima dell'avvio (salvataggio richiesto non riuscito).
  const [localError, setLocalError] = useState(null);
  const task = useTask({ scope: projectId, interval: POLL_INTERVAL, outcome: { failureFallback: "Errore durante l'anteprima" } });
  const { state, run, reset: resetTask } = task;

  const handlePreview = useCallback(async (limit = PREVIEW_ROW_LIMIT) => {
    // Fase 4, M7: l'anteprima legge la versione salvata; onBeforePreview
    // (serverActions.beforePreview) chiede se salvare. false = annullato.
    if (onBeforePreview) {
      try {
        if ((await onBeforePreview()) === false) return;
      } catch (err) {
        resetTask();
        setShowModal(true);
        setLocalError(`Salvataggio non riuscito: ${requestErrorMessage(err)}`);
        return;
      }
    }
    setShowModal(true);
    setLocalError(null);
    await run(() => previewProject(projectId, limit), { startMessage: 'Avvio...' });
  }, [projectId, onBeforePreview, run, resetTask]);

  // Chiudere la finestra ferma il polling: nessun esito arriva dopo.
  const closeModal = useCallback(() => {
    setShowModal(false);
    resetTask();
  }, [resetTask]);

  const reset = useCallback(() => {
    resetTask();
    setShowModal(false);
    setLocalError(null);
  }, [resetTask]);

  return {
    showModal,
    data: state.phase === 'success' ? (state.result || {}) : null,
    loading: task.isActive,
    error: localError || previewErrorOf(state),
    permissionRevoked: state.phase === 'permission_revoked',
    progress: state.progress,
    progressMessage: state.message,
    handlePreview, closeModal, reset,
  };
}

export default usePreview;
