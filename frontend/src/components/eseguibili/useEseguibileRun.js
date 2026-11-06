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
 * risultato» e alla scadenza della copia locale. Uscendo SENZA chiudere la
 * finestra (`leave`, semplificazione H4: scheda o browser chiusi, uscita
 * dall'account, pagina lasciata) con `pageExit: true`: la pagina chiama la
 * variante keepalive, api/tasks.releaseTaskOnPageExit, e un'esecuzione
 * ancora in corso la annulla il server prima di pulire. Fuori da /run (senza
 * `onCleanup`) uscire annulla soltanto l'esecuzione in corso
 * (api/tasks.cancelTaskOnPageExit). Il dialog di uscita dice quale dei due
 * (RUN_LEAVE_DIALOGS). Pagina messa nella cache del browser
 * (`pagehide` con `persisted`, conferma finale G4): la pagina puo' tornare,
 * quindi nessuna pulizia ora, solo `persisted: true` (la pagina annota);
 * al ritorno (`pageshow` con `persisted`) `pageReturn: true`: se
 * l'annotazione c'era ancora il risultato resta, se un'altra apertura l'ha
 * gia' pulito e' «non piu' disponibile».
 * Cosa si perde uscendo (esecuzione in corso, risultato non scaricato) e'
 * una domanda del registro unico utils/leaveGuard (conferma finale G8):
 * lo leggono il prompt di chiusura della scheda, il guard di navigazione e
 * l'uscita dall'account.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { downloadEseguibileOutput } from '../../api';
import { cancelTaskOnPageExit } from '../../api/tasks';
import { authBus } from '../../utils/authBus';
import { useTask } from '../../hooks/useTask';
import { useDownload } from '../../hooks/useDownload';
import { useTransferProgress } from '../../hooks/useTransferProgress';
import { useOptionalConfirm } from '../../context/ConfirmContext';
import { saveBlob } from '../../utils/downloadBlob';
import { formatTime } from '../../utils/format';
import { registerLeaveProbe } from '../../utils/leaveGuard';

/** Per quanto il file scaricato resta riscaricabile nella finestra. */
export const RESULT_KEEP_MINUTES = 30;
const KEEP_MS = RESULT_KEEP_MINUTES * 60_000;

/**
 * Il dialog dell'uscita con un'esecuzione in corso o un risultato non
 * scaricato: dice esattamente cosa fa `leave` (H4). In /run (con
 * `onCleanup`) l'esecuzione si annulla e il server elimina tutto; fuori da
 * /run l'esecuzione si annulla e basta.
 */
export const RUN_LEAVE_DIALOGS = Object.freeze({
  cleansOnExit: Object.freeze({
    title: 'Esecuzione non conclusa',
    message: 'C\'è un\'esecuzione in corso o un risultato non ancora scaricato. Uscendo l\'esecuzione in corso si ferma e il risultato viene eliminato dal server.',
    confirmText: 'Esci ed elimina',
    cancelText: 'Resta qui',
    variant: 'danger',
  }),
  keepsOnServer: Object.freeze({
    title: 'Esecuzione non conclusa',
    message: 'C\'è un\'esecuzione in corso o un risultato non ancora scaricato. Uscendo l\'esecuzione in corso si ferma e il risultato non ancora scaricato non si potrà più scaricare.',
    confirmText: 'Esci comunque',
    cancelText: 'Resta qui',
    variant: 'danger',
  }),
});


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
  // Cosa si perde uscendo: domanda del registro unico (utils/leaveGuard).
  const atRiskRef = useRef(false);
  atRiskRef.current = task.isActive || resultState === 'on_server';
  const leaveDialog = useRef(onCleanup ? RUN_LEAVE_DIALOGS.cleansOnExit : RUN_LEAVE_DIALOGS.keepsOnServer).current;
  useEffect(() => registerLeaveProbe(() => atRiskRef.current, leaveDialog), [leaveDialog]);
  // Esecuzione lasciata in una pagina messa nella cache del browser (G4).
  const parkedRef = useRef(null);

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

  /*
   * Uscita SENZA chiudere la finestra (H4): scheda chiusa (`pagehide`),
   * uscita dall'account (prima di togliere l'utente) o pagina lasciata
   * (smontaggio). Una regola, quella del dialog: in /run la pulizia completa
   * (il server annulla prima un'esecuzione in corso), fuori da /run
   * l'annullamento dell'esecuzione in corso. Sempre keepalive: parte subito,
   * col token di adesso. true = partita.
   */
  const leave = useCallback(() => {
    const { taskId, done, active } = finishedRef.current;
    if (!taskId || cleanedRef.current === taskId) return false;
    try {
      if (onCleanupRef.current) {
        if (!done && !active) return false;
        cleanedRef.current = taskId;
        onCleanupRef.current(taskId, { pageExit: true });
        return true;
      }
      if (!active) return false;
      cleanedRef.current = taskId;
      cancelTaskOnPageExit(taskId);
      return true;
    } catch (err) {
      console.warn('[useEseguibileRun] Uscita non gestita:', err);
      return false;
    }
  }, []);

  useEffect(() => () => { leave(); }, [leave]);
  useEffect(() => authBus.addBeforeLogoutListener(() => { leave(); }), [leave]);

  // Chiusura della scheda o della finestra del browser (B-K4, G4).
  useEffect(() => {
    const onPageHide = (event) => {
      const { taskId, done, active } = finishedRef.current;
      if (event?.persisted) {
        // Nella cache del browser: puo' tornare, si annota soltanto.
        if (!onCleanupRef.current || !taskId || (!done && !active) || cleanedRef.current === taskId) return;
        parkedRef.current = taskId;
        try {
          onCleanupRef.current(taskId, { pageExit: true, persisted: true });
        } catch (err) {
          console.warn('[useEseguibileRun] Annotazione alla chiusura non riuscita:', err);
        }
        return;
      }
      if (leave()) setGone(true);
    };
    const onPageShow = (event) => {
      const taskId = parkedRef.current;
      if (!event?.persisted || !taskId || !onCleanupRef.current) return;
      parkedRef.current = null;
      let stillHere = true;
      try {
        stillHere = onCleanupRef.current(taskId, { pageReturn: true }) !== false;
      } catch (err) {
        console.warn('[useEseguibileRun] Ritorno dalla cache non verificato:', err);
      }
      // Un'altra apertura di /run l'ha gia' pulito: non c'e' piu'.
      if (!stillHere) { cleanedRef.current = taskId; setGone(true); }
    };
    window.addEventListener('pagehide', onPageHide);
    window.addEventListener('pageshow', onPageShow);
    return () => {
      window.removeEventListener('pagehide', onPageHide);
      window.removeEventListener('pageshow', onPageShow);
    };
  }, [leave]);

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
