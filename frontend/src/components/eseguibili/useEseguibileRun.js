/**
 * useEseguibileRun — avvio, esito, download e pulizia di un'esecuzione di
 * un Eseguibile (Fase 5 T2, audit R9.2, R9.3, N12, BA2). Lo usano le due
 * finestre degli Eseguibili (ExecutableDynamicRunForm, EseguibileExecuteWizard).
 *
 * Stesso percorso dell'editor: ciclo unico hooks/useTask (404, 5xx, revoked,
 * permission_revoked con un esito vero: mai "completata" con un download
 * inesistente), Annulla, download con errore mostrato (hooks/useDownload).
 *
 * Il risultato ha UNO stato (`resultState`, UI conferma B-K5), da cui
 * dipendono pulsanti, avvisi e domanda di chiusura:
 *  - 'none'      nessun esito riuscito;
 *  - 'on_server' riuscito, non ancora scaricato: il server lo consegna UNA
 *                volta (core/eseguibili, consume-once). Chiudere chiede
 *                prima (verrebbe eliminato);
 *  - 'local'     scaricato: la copia resta in questa finestra fino a
 *                `expiresAt` (RESULT_KEEP_MINUTES) e «Scarica di nuovo» la
 *                risalva senza tornare al server;
 *  - 'gone'      non piu' disponibile: la copia locale e' scaduta (si toglie
 *                dalla memoria e si pulisce il server) o la pagina e' stata
 *                chiusa con la pulizia gia' partita. Niente «Scarica», niente
 *                domanda di chiusura (non c'e' piu' niente da perdere).
 * La scadenza si misura sull'orologio (`expiresAt`), non solo col timer: si
 * ricontrolla al ritorno sulla scheda e prima di ogni scaricamento (un timer
 * di una scheda nascosta o di un portatile in sospensione arriva tardi).
 * Durante l'esecuzione o lo scaricamento la finestra non si chiude e non si
 * elimina il risultato (prima la pulizia partiva a scaricamento in volo).
 *
 * Pulizia (`onCleanup(taskId, { pageExit, active })`, /run dell'esecutore:
 * privacy, «I file caricati ed elaborati non vengono conservati»): parte UNA
 * volta per esecuzione, alla chiusura della finestra o con «Elimina il
 * risultato», alla scadenza della copia locale, allo smontaggio e — UI
 * conferma B-K4 — anche chiudendo la scheda o la finestra del browser
 * (`pagehide`, con `pageExit: true`: la pagina chiama la variante keepalive,
 * api/tasks.releaseTaskOnPageExit; `active` = esecuzione ancora in corso, da
 * annullare). Chiudere la scheda con un'esecuzione in corso o un risultato
 * non scaricato chiede prima (prompt del browser, `beforeunload`).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { downloadEseguibileOutput } from '../../api';
import { useTask } from '../../hooks/useTask';
import { useDownload } from '../../hooks/useDownload';
import { useTransferProgress } from '../../hooks/useTransferProgress';
import { useOptionalConfirm } from '../../context/ConfirmContext';
import { saveBlob } from '../../utils/downloadBlob';
import { formatTime } from '../../utils/format';

/** Per quanto il file scaricato resta riscaricabile nella finestra. */
export const RESULT_KEEP_MINUTES = 30;
const KEEP_MS = RESULT_KEEP_MINUTES * 60_000;


/** Cosa succede al risultato, detto accanto ai pulsanti: una frase per stato. */
function resultNoteOf(resultState, { expiresAt = null, cleansOnExit = false } = {}) {
  switch (resultState) {
    case 'on_server':
      return cleansOnExit
        ? 'Il risultato resta finché non chiudi questa finestra o la scheda del browser: se chiudi senza scaricarlo ti viene chiesto prima, poi viene eliminato.'
        : 'Il risultato resta finché non chiudi questa finestra: se la chiudi senza scaricarlo ti viene chiesto prima di eliminarlo.';
    case 'local':
      return `File scaricato: puoi scaricarlo di nuovo da questa finestra fino alle ${formatTime(expiresAt)}. Chiudendola, o con «Elimina il risultato», non sarà più disponibile.`;
    case 'gone':
      return 'Il file non è più disponibile in questa finestra: esegui di nuovo per ottenerne uno nuovo.';
    default:
      return null;
  }
}

export function useEseguibileRun({ eseguibileId, onCleanup = null, onClose = null }) {
  const task = useTask({ scope: eseguibileId, interval: 1500 });
  const dl = useDownload();
  const transfer = useTransferProgress();
  const confirm = useOptionalConfirm();
  const { state } = task;
  const [saved, setSaved] = useState(null); // { blob, filename, expiresAt } del file scaricato
  const [gone, setGone] = useState(false); // copia scaduta o pagina chiusa (pulizia partita)
  const cleanedRef = useRef(null);
  const onCleanupRef = useRef(onCleanup);
  onCleanupRef.current = onCleanup;
  const finishedRef = useRef({ taskId: null, done: false, active: false });
  finishedRef.current = { taskId: state.taskId, done: task.isDone, active: task.isActive };

  const resultState = state.phase !== 'success' ? 'none'
    : saved ? 'local'
      : gone ? 'gone'
        : 'on_server';
  const busy = task.isActive || dl.busy;
  // Cosa si perde chiudendo la pagina (letto dai gestori della pagina).
  const atRiskRef = useRef(false);
  atRiskRef.current = task.isActive || resultState === 'on_server';

  const cleanup = useCallback(async () => {
    const { taskId, done } = finishedRef.current;
    if (!onCleanupRef.current || !taskId || !done || cleanedRef.current === taskId) return;
    cleanedRef.current = taskId;
    try {
      await onCleanupRef.current(taskId, { pageExit: false, active: false });
    } catch (err) {
      console.warn('[useEseguibileRun] Pulizia non riuscita:', err);
    }
  }, []);

  // Smontaggio con un'esecuzione finita e non ancora pulita.
  useEffect(() => () => { cleanup(); }, [cleanup]);

  // Chiusura della scheda o della finestra del browser (B-K4).
  useEffect(() => {
    const onPageHide = () => {
      const { taskId, done, active } = finishedRef.current;
      if (!onCleanupRef.current || !taskId || (!done && !active) || cleanedRef.current === taskId) return;
      cleanedRef.current = taskId;
      try {
        onCleanupRef.current(taskId, { pageExit: true, active });
      } catch (err) {
        console.warn('[useEseguibileRun] Pulizia alla chiusura non partita:', err);
      }
      // Pagina rimessa dalla cache del browser: il server non lo ha piu'.
      setGone(true);
    };
    const onBeforeUnload = (event) => {
      if (!atRiskRef.current) return undefined;
      event.preventDefault();
      event.returnValue = '';
      return '';
    };
    window.addEventListener('pagehide', onPageHide);
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      window.removeEventListener('pagehide', onPageHide);
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, []);

  /** La copia locale e' scaduta? Allora la toglie e pulisce. true = non c'e' piu'. */
  const expireIfDue = useCallback(() => {
    if (!saved || Date.now() < saved.expiresAt) return false;
    setSaved(null);
    setGone(true);
    cleanup();
    return true;
  }, [saved, cleanup]);

  // Scadenza: timer fino a `expiresAt` e controllo al ritorno sulla scheda.
  useEffect(() => {
    if (!saved) return undefined;
    const t = setTimeout(expireIfDue, Math.max(0, saved.expiresAt - Date.now()));
    const onVisible = () => { if (document.visibilityState === 'visible') expireIfDue(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearTimeout(t);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [saved, expireIfDue]);

  // Esecuzione nuova: niente file ne' stato della precedente.
  useEffect(() => { setSaved(null); setGone(false); }, [state.taskId]);

  const finish = useCallback(async () => {
    if (saved) setSaved(null);
    // Niente attesa se non c'e' niente da pulire: la finestra si chiude subito.
    const { taskId, done } = finishedRef.current;
    if (onCleanupRef.current && taskId && done && cleanedRef.current !== taskId) await cleanup();
    onClose?.();
    return true;
  }, [saved, cleanup, onClose]);

  /**
   * Chiude la finestra; durante l'esecuzione o lo scaricamento non si chiude.
   * Con un risultato non scaricato chiede prima: false = resta aperta.
   */
  const close = useCallback(async () => {
    if (busy) return false;
    if (resultState === 'on_server' && confirm) {
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
  }, [busy, resultState, confirm, finish]);

  /** Pulizia esplicita: elimina il risultato (server e finestra) e chiude. */
  const discard = useCallback(async () => {
    if (busy) return false;
    return finish();
  }, [busy, finish]);

  /** Scarica il risultato; dopo il primo, «Scarica di nuovo» usa la copia della finestra. */
  const download = useCallback(async (fallbackName) => {
    if (state.phase !== 'success' || !state.taskId || expireIfDue()) return;
    if (saved) { saveBlob(saved.blob, saved.filename); return; }
    if (gone) return;
    await dl.run('result', async () => {
      try {
        const file = await downloadEseguibileOutput(eseguibileId, state.taskId, fallbackName, { onProgress: transfer.onProgress });
        if (file?.blob) setSaved({ blob: file.blob, filename: file.filename, expiresAt: Date.now() + KEEP_MS });
      } finally {
        transfer.reset();
      }
    });
  }, [state.phase, state.taskId, saved, gone, expireIfDue, dl, eseguibileId, transfer]);

  return {
    task, state, start: task.run, cancel: task.cancel, close, discard, download,
    busy, resultState,
    canDownload: resultState === 'on_server' || resultState === 'local',
    resultNote: resultNoteOf(resultState, { expiresAt: saved?.expiresAt, cleansOnExit: Boolean(onCleanup) }),
    downloading: dl.busy, downloadError: dl.error, downloadProgress: transfer.progress,
    downloaded: Boolean(saved),
  };
}

export default useEseguibileRun;
