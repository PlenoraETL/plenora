/**
 * useExecution.js — Esecuzione dell'editor (Estrai, Manipola, Arricchisci).
 *
 * Avvio -> polling -> esito -> download, sopra il ciclo unico di
 * hooks/useTask (Fase 5 T2): id di richiesta, 404/5xx/revoked/
 * permission_revoked con un esito vero (mai "completata" falsa), Annulla per
 * tutti i tipi di progetto (annullamento cooperativo del backend), stop al
 * cambio di progetto e allo smontaggio. I task di Carica che partono dopo
 * l'esecuzione (anche piu' d'uno) usano lo stesso poller (utils/taskPoller).
 *
 * La finestra di esecuzione si chiude sempre ("Continua in secondo piano")
 * e si riapre con openModal() (barra dell'editor: executionToolbarProps).
 * Le props della finestra sono le stesse nei tre editor:
 * executionModalProps(execution).
 *
 * Usato da: EstraiEditor, ManipolaEditor, ArricchisciEditor (onExecute,
 * chiamato dal percorso unico editor/PipelineRunner dopo la finestra
 * «Prima di avviare»: input, opzioni e conferme arrivano gia' in executeOptions).
 * Un rifiuto 409 `preparation_changed` (Arricchisci) torna a chi ha avviato.
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { executeProjectQuery, executeTestRun, downloadTaskOutput, downloadProjectOutput } from '../api';
import { useTask } from './useTask';
import { useDownload } from './useDownload';
import useCanvasStore from '../store/useCanvasStore';
import { pollTask } from '../utils/taskPoller';
import { isPreparationChanged } from '../utils/beforeRun';

/** Esito della finestra a partire dall'esito del task. */
export function executionResult(outcome, taskId) {
  if (!outcome) return null;
  if (outcome.phase === 'success') return { success: true, ...(outcome.result || {}), task_id: taskId || undefined };
  if (outcome.phase === 'permission_revoked') {
    return { success: false, permissionRevoked: true, status: 'permission_revoked', message: outcome.error };
  }
  // L'esecuzione di prova resta riconoscibile anche quando fallisce.
  const testRun = outcome.result?.test_run;
  return { success: false, message: outcome.error, code: outcome.code || undefined, ...(testRun ? { test_run: testRun } : {}) };
}

// Stato mostrato dalla finestra (colori, "annullata").
const statusOfPhase = (phase, raw) => {
  if (phase === 'running') return raw || 'pending';
  if (phase === 'starting') return 'pending';
  if (phase === 'success') return 'success';
  if (phase === 'revoked') return 'revoked';
  if (phase === 'idle') return null;
  return 'failure';
};

const caricaRow = (task, outcome) => {
  const failed = outcome.phase === 'failure' || outcome.phase === 'lost' || outcome.phase === 'permission_revoked';
  return {
    ...task,
    status: failed ? 'failure' : outcome.phase === 'running' ? (outcome.status || 'pending') : outcome.phase,
    message: outcome.error || outcome.result?.message || outcome.message || '',
    rows_written: outcome.result?.rows_written || 0,
    // Codice strutturato (es. carica_arcgis_write_failed): la finestra lo
    // traduce in un messaggio azionabile.
    error: failed ? (outcome.code || null) : null,
    error_message: failed ? outcome.error : null,
  };
};

export function useExecution(projectId, options = {}) {
  const {
    onBeforeExecute = null,
    executeFunction = null,
    pollInterval = 1000,
    // Fase 4, M7: l'esecuzione PRINCIPALE dell'editor tiene lo stato
    // condiviso `runState` ('running') che spegne "Esegui" in barra e nel
    // pannello finche' il task gira (anche a finestra chiusa).
    tracksRunState = false,
  } = options;

  const task = useTask({ scope: projectId, interval: pollInterval });
  const { state, run: runTask, reset: resetTask, cancel: cancelRunningTask } = task;
  const isExecuting = task.isActive;

  const [showModal, setShowModal] = useState(false);
  const [result, setResult] = useState(null);
  // Esito arrivato a finestra chiusa: la barra offre "Vedi esito".
  const [unseenResult, setUnseenResult] = useState(false);
  const showModalRef = useRef(false);
  showModalRef.current = showModal;
  const dl = useDownload();
  const { run: runDownload, clearError: clearDownloadError } = dl;

  // Task di Carica partiti dopo l'esecuzione.
  const [caricaTasks, setCaricaTasks] = useState([]);
  const caricaStops = useRef(new Map());
  const [caricaPolling, setCaricaPolling] = useState(false);

  const stopAllCaricaPolling = useCallback(() => {
    caricaStops.current.forEach(stop => stop());
    caricaStops.current.clear();
    setCaricaPolling(false);
  }, []);
  useEffect(() => () => {
    caricaStops.current.forEach(stop => stop());
    caricaStops.current.clear();
  }, []);

  const startCaricaTasksPolling = useCallback((pipelineResult) => {
    const tasks = pipelineResult?.carica?.tasks;
    if (!tasks || tasks.length === 0) return;
    stopAllCaricaPolling();
    setCaricaTasks(tasks.map(t => ({
      task_id: t.task_id, node_id: t.node_id, status: 'pending', message: 'In coda...', rows_written: 0, error: null,
    })));
    setCaricaPolling(true);
    tasks.forEach(({ task_id }) => {
      const update = (o) => setCaricaTasks(prev => prev.map(t => (t.task_id === task_id ? caricaRow(t, o) : t)));
      const stop = pollTask(task_id, {
        interval: pollInterval,
        outcome: { businessStatus: true },
        onUpdate: update,
        onDone: (o) => {
          update(o);
          caricaStops.current.delete(task_id);
          if (caricaStops.current.size === 0) setCaricaPolling(false);
        },
      });
      caricaStops.current.set(task_id, stop);
    });
  }, [pollInterval, stopAllCaricaPolling]);

  // Cambio di progetto: niente di quello di prima resta qui.
  const projectRef = useRef(projectId);
  useEffect(() => {
    if (projectRef.current === projectId) return;
    projectRef.current = projectId;
    stopAllCaricaPolling();
    setCaricaTasks([]);
    setShowModal(false);
    setResult(null);
    setUnseenResult(false);
  }, [projectId, stopAllCaricaPolling]);

  const executingRef = useRef(false);
  useEffect(() => {
    executingRef.current = isExecuting;
    if (!tracksRunState) return;
    const st = useCanvasStore.getState();
    if (isExecuting) st.setRunState?.('running');
    else if (st.runState === 'running') st.setRunState?.('idle');
  }, [isExecuting, tracksRunState]);
  useEffect(() => () => {
    if (tracksRunState && useCanvasStore.getState().runState === 'running') useCanvasStore.getState().setRunState?.('idle');
  }, [tracksRunState]);

  const settle = useCallback((outcome, taskId) => {
    executingRef.current = false;
    const r = executionResult(outcome, taskId);
    setResult(r);
    if (!showModalRef.current) setUnseenResult(true);
    if (outcome.phase === 'success') {
      // Ultimo esito nel canvas store: i nodi (es. ResultNode IMDF) lo leggono.
      try { useCanvasStore.getState().setLastTaskResult?.(r); } catch (_) { /* store assente nei test */ }
      startCaricaTasksPolling(outcome.result);
    }
  }, [startCaricaTasksPolling]);

  const handleExecute = useCallback(async (executeOptions = {}) => {
    // Un'esecuzione alla volta (es. pannello cliccato a finestra chiusa).
    if (executingRef.current) return;
    executingRef.current = true;
    setShowModal(true);
    setUnseenResult(false);
    setResult(null);
    clearDownloadError();
    setCaricaTasks([]);
    stopAllCaricaPolling();

    let preparationChanged = null;
    const outcome = await runTask(async () => {
      if (onBeforeExecute) await onBeforeExecute();
      // Esecuzione di prova (PipelineRunner, regola H): una sola funzione
      // per i tre editor, che sceglie l'endpoint dalla modalita'.
      const executeFn = executeOptions.test_run ? executeTestRun : (executeFunction || executeProjectQuery);
      return executeFn(projectId, executeOptions);
    }, {
      onDone: settle,
      onLaunchError: (err) => {
        if (!isPreparationChanged(err)) return false;
        // Arricchisci: la preparazione e' cambiata dalla stima confermata.
        // Niente esito qui: la finestra «Prima di avviare» rifa' la stima.
        preparationChanged = err;
        return true;
      },
    });
    if (outcome.superseded) return;
    if (outcome.error && !outcome.handled) console.warn('[useExecution] Avvio non riuscito:', outcome.error);
    if (outcome.error || outcome.outcome) executingRef.current = false;
    if (preparationChanged) {
      setShowModal(false);
      throw preparationChanged;
    }
  }, [projectId, onBeforeExecute, executeFunction, runTask, settle, stopAllCaricaPolling, clearDownloadError]);

  const closeModal = useCallback(() => setShowModal(false), []);
  const openModal = useCallback(() => {
    setShowModal(true);
    setUnseenResult(false);
  }, []);

  const reset = useCallback(() => {
    resetTask();
    stopAllCaricaPolling();
    executingRef.current = false;
    setShowModal(false); setResult(null); setUnseenResult(false);
    setCaricaTasks([]); clearDownloadError();
  }, [resetTask, stopAllCaricaPolling, clearDownloadError]);

  // Download con errore mostrato nella finestra (410 scaduto, 404, rete).
  const downloadTaskResult = useCallback(() => runDownload('result', async () => {
    // `output_file` e' il canale canonico; senza, `result_id` + formato.
    const outputPath = result?.output_file || null;
    let filename = outputPath ? outputPath.split(/[/\\]/).pop() : null;
    if (!filename && result?.result_id) {
      const ext = result?.output_format ? `.${String(result.output_format).replace(/^\.+/, '')}` : '';
      filename = `result_${result.result_id}${ext}`;
    }
    if (state.taskId) await downloadTaskOutput(state.taskId, filename);
    else await downloadProjectOutput(projectId, filename);
  }), [runDownload, result, state.taskId, projectId]);

  const isStillWorking = isExecuting || caricaPolling;

  return {
    showModal, result, isExecuting,
    taskId: state.taskId, progress: state.progress, progressMessage: state.message,
    taskStatus: statusOfPhase(state.phase, state.status),
    cancelling: state.cancelling, cancelError: state.cancelError,
    canCancel: isExecuting && Boolean(state.taskId),
    caricaTasks, caricaPolling, isStillWorking,
    unseenResult,
    downloading: dl.busy, downloadError: dl.error,
    handleExecute, closeModal, openModal, reset, cancelTask: cancelRunningTask, downloadTaskResult,
    setShowModal, setResult,
  };
}

/** Le stesse props della finestra di esecuzione nei tre editor. */
export function executionModalProps(execution) {
  return {
    isOpen: execution.showModal,
    onClose: execution.closeModal,
    isExecuting: execution.isExecuting,
    result: execution.result,
    onDownload: execution.downloadTaskResult,
    downloading: execution.downloading,
    downloadError: execution.downloadError,
    progress: execution.progress,
    progressMessage: execution.progressMessage,
    taskStatus: execution.taskStatus,
    onCancel: execution.canCancel ? execution.cancelTask : null,
    cancelling: execution.cancelling,
    cancelError: execution.cancelError,
    caricaTasks: execution.caricaTasks,
    caricaPolling: execution.caricaPolling,
  };
}

/** Barra dell'editor: riapre la finestra mentre gira o con un esito non visto. */
export function executionToolbarProps(execution) {
  const reopen = !execution.showModal && (execution.isStillWorking || execution.unseenResult);
  return {
    executing: execution.isExecuting,
    onShowExecution: reopen ? execution.openModal : null,
    executionUnseen: !execution.isStillWorking && execution.unseenResult,
  };
}

export default useExecution;
