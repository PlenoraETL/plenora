/**
 * useEseguibileRun — avvio, esito, download e pulizia di un'esecuzione di
 * un Eseguibile (Fase 5 T2, audit R9.2, R9.3, N12, BA2). Lo usano le due
 * finestre degli Eseguibili (ExecutableDynamicRunForm, EseguibileExecuteWizard).
 *
 * Stesso percorso dell'editor: ciclo unico hooks/useTask (404, 5xx, revoked,
 * permission_revoked con un esito vero: mai "completata" con un download
 * inesistente), Annulla, download con errore mostrato (hooks/useDownload).
 *
 * Risultato e pulizia (UI-3, U13, 06/11/2026). Prima Chiudi, × ed Esc
 * cancellavano il risultato dal server senza avviso, e lo scaricamento
 * chiudeva e puliva subito (il file non si poteva riscaricare). Ora:
 *  - scaricare NON chiude: il file scaricato resta in questa finestra per
 *    RESULT_KEEP_MINUTES e «Scarica di nuovo» lo risalva senza tornare al
 *    server (che lo consegna una volta sola: core/eseguibili, consume-once);
 *    scaduto il tempo il file si toglie dalla memoria da solo;
 *  - chiudere con un risultato NON ancora scaricato chiede conferma (il
 *    risultato verrebbe eliminato); annullare lascia la finestra aperta;
 *  - «Elimina il risultato» (`discard`) e' la pulizia esplicita.
 * `onCleanup(taskId)` (/run, esecutore: privacy) parte UNA volta, quando la
 * finestra si chiude o si elimina il risultato, con qualunque esito, oppure
 * allo smontaggio: «I file caricati ed elaborati non vengono conservati».
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { downloadEseguibileOutput } from '../../api';
import { useTask } from '../../hooks/useTask';
import { useDownload } from '../../hooks/useDownload';
import { useTransferProgress } from '../../hooks/useTransferProgress';
import { useOptionalConfirm } from '../../context/ConfirmContext';
import { saveBlob } from '../../utils/downloadBlob';

/** Per quanto il file scaricato resta riscaricabile nella finestra. */
const RESULT_KEEP_MINUTES = 30;

/** Cosa succede al risultato, detto accanto al pulsante di scaricamento. */
export const resultKeepNote = (downloaded) => (downloaded
  ? `File scaricato: puoi scaricarlo di nuovo da questa finestra per ${RESULT_KEEP_MINUTES} minuti. Chiudendola, o con «Elimina il risultato», non sarà più disponibile.`
  : 'Il risultato resta finché non chiudi questa finestra: se la chiudi senza scaricarlo ti viene chiesto prima di eliminarlo.');

export function useEseguibileRun({ eseguibileId, onCleanup = null, onClose = null }) {
  const task = useTask({ scope: eseguibileId, interval: 1500 });
  const dl = useDownload();
  const transfer = useTransferProgress();
  const confirm = useOptionalConfirm();
  const { state } = task;
  const [saved, setSaved] = useState(null); // { blob, filename, at } del file scaricato
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

  // Il file scaricato resta riscaricabile per il tempo dichiarato.
  useEffect(() => {
    if (!saved) return undefined;
    const t = setTimeout(() => setSaved(null), RESULT_KEEP_MINUTES * 60_000);
    return () => clearTimeout(t);
  }, [saved]);

  // Esecuzione nuova: niente file della precedente.
  useEffect(() => { setSaved(null); }, [state.taskId]);

  const finish = useCallback(async () => {
    if (saved) setSaved(null);
    const { taskId, done } = finishedRef.current;
    if (onCleanupRef.current && taskId && done && cleanedRef.current !== taskId) await cleanup();
    onClose?.();
    return true;
  }, [saved, cleanup, onClose]);

  /**
   * Chiude la finestra; durante l'esecuzione non si chiude (c'e' Annulla).
   * Con un risultato non scaricato chiede prima: false = resta aperta.
   */
  const close = useCallback(async () => {
    if (task.isActive) return false;
    const unsavedResult = state.phase === 'success' && !saved;
    if (unsavedResult && confirm) {
      const ok = await confirm({
        title: 'Risultato non scaricato',
        message: 'Il risultato di questa esecuzione non è stato scaricato. Chiudendo la finestra viene eliminato e non si potrà più scaricare.',
        confirmText: 'Chiudi ed elimina',
        cancelText: 'Resta qui',
        variant: 'danger',
      });
      if (!ok) return false;
    }
    return finish();
  }, [task.isActive, state.phase, saved, confirm, finish]);

  /** Pulizia esplicita: elimina il risultato (server e finestra) e chiude. */
  const discard = useCallback(async () => {
    if (task.isActive) return false;
    return finish();
  }, [task.isActive, finish]);

  /** Scarica il risultato; dopo il primo, «Scarica di nuovo» usa la copia della finestra. */
  const download = useCallback(async (fallbackName) => {
    if (state.phase !== 'success' || !state.taskId) return;
    if (saved) { saveBlob(saved.blob, saved.filename); return; }
    await dl.run('result', async () => {
      try {
        const file = await downloadEseguibileOutput(eseguibileId, state.taskId, fallbackName, { onProgress: transfer.onProgress });
        if (file?.blob) setSaved({ ...file, at: Date.now() });
      } finally {
        transfer.reset();
      }
    });
  }, [state.phase, state.taskId, saved, dl, eseguibileId, transfer]);

  return {
    task, state, start: task.run, cancel: task.cancel, close, discard, download,
    downloading: dl.busy, downloadError: dl.error, downloadProgress: transfer.progress,
    downloaded: Boolean(saved), downloadedAt: saved?.at || null,
  };
}

export default useEseguibileRun;
