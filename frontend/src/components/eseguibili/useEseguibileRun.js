/**
 * useEseguibileRun — avvio, esito, download e pulizia di un'esecuzione di
 * un Eseguibile (Fase 5 T2, audit R9.2, R9.3, N12, BA2). Lo usano le due
 * finestre degli Eseguibili (ExecutableDynamicRunForm, EseguibileExecuteWizard).
 *
 * Stesso percorso dell'editor: ciclo unico hooks/useTask (404, 5xx, revoked,
 * permission_revoked con un esito vero: mai "completata" con un download
 * inesistente), Annulla, download con errore mostrato (hooks/useDownload).
 *
 * Pulizia (/run, esecutore): `onCleanup(taskId)` parte UNA volta quando
 * l'esecuzione e' finita e la finestra si chiude, con qualunque esito
 * (scaricato o no, fallito, annullato), oppure allo smontaggio. Cosi' "I file
 * caricati ed elaborati non vengono conservati" vale sempre.
 */
import { useCallback, useEffect, useRef } from 'react';
import { downloadEseguibileOutput } from '../../api';
import { useTask } from '../../hooks/useTask';
import { useDownload } from '../../hooks/useDownload';

export function useEseguibileRun({ eseguibileId, onCleanup = null, onClose = null }) {
  const task = useTask({ scope: eseguibileId, interval: 1500 });
  const dl = useDownload();
  const { state } = task;
  const cleanedRef = useRef(null);
  const onCleanupRef = useRef(onCleanup);
  onCleanupRef.current = onCleanup;
  const finishedRef = useRef({ taskId: null, done: false });
  finishedRef.current = { taskId: state.taskId, done: task.isDone };

  const cleanup = useCallback(async () => {
    const { taskId, done } = finishedRef.current;
    if (!onCleanupRef.current || !taskId || !done || cleanedRef.current === taskId) return;
    cleanedRef.current = taskId;
    try {
      await onCleanupRef.current(taskId);
    } catch (err) {
      console.warn('[useEseguibileRun] Pulizia non riuscita:', err);
    }
  }, []);

  // Smontaggio con un'esecuzione finita e non ancora pulita.
  useEffect(() => () => { cleanup(); }, [cleanup]);

  /** Chiude la finestra; durante l'esecuzione non si chiude (c'e' Annulla). */
  const close = useCallback(async () => {
    if (task.isActive) return false;
    const { taskId, done } = finishedRef.current;
    const pending = onCleanupRef.current && taskId && done && cleanedRef.current !== taskId;
    if (pending) await cleanup();
    onClose?.();
    return true;
  }, [task.isActive, cleanup, onClose]);

  /** Scarica il risultato; riuscito -> pulizia e chiusura. */
  const download = useCallback(async (fallbackName) => {
    if (state.phase !== 'success' || !state.taskId) return;
    const ok = await dl.run('result', () => downloadEseguibileOutput(eseguibileId, state.taskId, fallbackName));
    if (ok) await close();
  }, [state.phase, state.taskId, dl, eseguibileId, close]);

  return { task, state, start: task.run, cancel: task.cancel, close, download, downloading: dl.busy, downloadError: dl.error };
}

export default useEseguibileRun;
