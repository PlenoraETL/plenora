/**
 * useExecution.js — Hook per esecuzione asincrona pipeline
 *
 * Gestisce l'intero ciclo di vita di una esecuzione:
 * avvio task asincrono → polling stato → completamento/errore → download output.
 *
 * Ritorna: { executing, taskId, progress, result, error, execute, cancel }
 *
 * Usato da: EstraiEditor, ManipolaEditor, ArricchisciEditor (via ResultNode)
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { executeProjectQuery, getTaskStatus, cancelTask as cancelTaskApi, downloadTaskOutput, downloadProjectOutput } from '../api';
import { useTaskPolling } from './useTaskPolling';
import useCanvasStore from '../store/useCanvasStore';

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

  // Stati base pipeline
  const [showModal, setShowModal]           = useState(false);
  const [result, setResult]                 = useState(null);
  const [isExecuting, setIsExecuting]       = useState(false);
  const [taskId, setTaskId]                 = useState(null);
  const [progress, setProgress]             = useState(0);
  const [progressMessage, setProgressMessage] = useState('');
  const [taskStatus, setTaskStatus]         = useState(null);

  // Stati CARICA tasks
  const [caricaTasks, setCaricaTasks]       = useState([]);
  const [caricaPolling, setCaricaPolling]   = useState(false);

  // R-25 PR-B5: stato "config pre-launch" usato dal modal per mostrare
  // il toggle intermediate_mode (visibile solo a superadmin) prima che
  // la pipeline parta davvero. Se nessuno chiama prepareExecute, il flow
  // resta quello classico (handleExecute parte subito).
  const [awaitingLaunch, setAwaitingLaunch] = useState(false);

  const executingRef = useRef(false);
  // Fine (o azzeramento) dell'esecuzione: il blocco si toglie SUBITO, anche
  // se React accorpa "inizia" e "finisce" in un solo render.
  const stopExecuting = useCallback(() => {
    executingRef.current = false;
    setIsExecuting(false);
  }, []);
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
  const pendingOptsRef = useRef(null);

  const caricaRefs    = useRef({});
  const caricaGenerationsRef = useRef({});

  // Polling base dal hook condiviso
  const { mountedRef, stopPolling, startPolling } = useTaskPolling({ interval: pollInterval });

  // Cleanup carica refs su unmount
  useEffect(() => () => {
    Object.values(caricaRefs.current).forEach(id => clearTimeout(id));
    caricaRefs.current = {};
    caricaGenerationsRef.current = {};
  }, []);

  const stopAllCaricaPolling = useCallback(() => {
    Object.values(caricaRefs.current).forEach(id => clearTimeout(id));
    caricaRefs.current = {};
    caricaGenerationsRef.current = {};
    setCaricaPolling(false);
  }, []);
const startCaricaPolling = useCallback((caricaTask) => {
    const { task_id } = caricaTask;
    const generation = (caricaGenerationsRef.current[task_id] || 0) + 1;
    caricaGenerationsRef.current[task_id] = generation;

    const scheduleNext = () => {
      if (!mountedRef.current || caricaGenerationsRef.current[task_id] !== generation) return;
      caricaRefs.current[task_id] = setTimeout(poll, pollInterval);
    };

    const poll = async () => {
      if (!mountedRef.current || caricaGenerationsRef.current[task_id] !== generation) return;
      try {
        const data = await getTaskStatus(task_id);
        if (!mountedRef.current || caricaGenerationsRef.current[task_id] !== generation) return;
        if (!data) {
          scheduleNext();
          return;
        }

        const isDone = ['success', 'failure', 'revoked'].includes(data.status);

        // data.status = stato task (success/failure/revoked)
        // data.result.status = stato business logic (validation_error, write_error, ecc.)
        const businessStatus = data.result?.status;
        const isBusinessError = businessStatus && businessStatus !== 'success' && businessStatus !== 'completed' && businessStatus !== 'partial_success';
        const effectiveStatus = data.status === 'failure' ? 'failure'
          : data.status === 'revoked' ? 'revoked'
          : isBusinessError ? 'failure'
          : data.status;
        const effectiveError = data.status === 'failure'
          ? (data.error || 'Errore')
          : isBusinessError
          ? (data.result?.error_message || data.result?.message || businessStatus)
          : null;
        // Codice strutturato (es. carica_arcgis_write_failed) propagato dal
        // backend: lo conserviamo così il modal lo traduce in un messaggio
        // umano azionabile (Epic A). Prima veniva scartato (error: null).
        const effectiveCode = data.result?.error
          || (data.status === 'failure' ? data.error : null);

        setCaricaTasks(prev => prev.map(t =>
          t.task_id === task_id
            ? {
                ...t,
                status: effectiveStatus,
                message: data.result?.message || data.error || data.message || '',
                rows_written: data.result?.rows_written || 0,
                error: effectiveStatus === 'failure' ? effectiveCode : null,
                error_message: effectiveError,
              }
            : t
        ));

        if (isDone) {
          clearTimeout(caricaRefs.current[task_id]);
          delete caricaRefs.current[task_id];
          delete caricaGenerationsRef.current[task_id];
          if (Object.keys(caricaRefs.current).length === 0) {
            setCaricaPolling(false);
          }
          return;
        }
      } catch (e) {
        if (!mountedRef.current || caricaGenerationsRef.current[task_id] !== generation) return;
        // 404 = task non ancora registrato, aspetta il prossimo poll
        if (e.response?.status !== 404) {
          console.error('[useExecution] Errore polling CARICA:', e);
        }
      }
      scheduleNext();
    };

    // Single-flight per task_id: se startCaricaPolling viene richiamato
    // sullo stesso task (retry, ri-build del result), non lasciare due
    // timeout orfani — clear del precedente prima di reassign.
    // Cfr. RESOURCE_LEAKS.md §3.
    if (caricaRefs.current[task_id]) {
      clearTimeout(caricaRefs.current[task_id]);
    }
    // mountedRef guard PRIMA di avviare il loop. Pre-fix (P1 frontend-hooks):
    // startCaricaPolling poteva essere chiamato da una callback async di
    // un createTask che risolveva DOPO che il componente si era smontato
    // registrava un poll su componente unmounted. Il cleanup
    // useEffect alla riga 50-53 lo cancellava, ma il primo `poll()` sync
    // sotto poteva girare prima del cleanup → setState post-unmount.
    if (!mountedRef.current) return;
    poll();
  }, [pollInterval, mountedRef]);
const startCaricaTasksPolling = useCallback((pipelineResult) => {
    const tasks = pipelineResult?.carica?.tasks;
    if (!tasks || tasks.length === 0) return;

    const initialTasks = tasks.map(t => ({
      task_id: t.task_id,
      node_id: t.node_id,
      status: 'pending',
      message: 'In coda...',
      rows_written: 0,
      error: null,
    }));

    setCaricaTasks(initialTasks);
    setCaricaPolling(true);
    initialTasks.forEach(t => startCaricaPolling(t));
  }, [startCaricaPolling]);
const startMainPolling = useCallback((taskIdToWatch) => {
    startPolling(taskIdToWatch, (data) => {
      setTaskStatus(data.status);
      setProgress(data.progress || 0);
      setProgressMessage(data.message || '');

      if (data.status === 'success') {
        stopExecuting();
        const r = { success: true, ...data.result, task_id: taskIdToWatch };
        setResult(r);
        // Salva l'ultimo task result completo nel canvas store, cosi' i nodi
        // (es. ResultNode in modalita' IMDF) possono leggerlo via selector
        // reattivo per popolare modali/badge post-esecuzione.
        try { useCanvasStore.getState().setLastTaskResult(r); } catch (_) {}
        startCaricaTasksPolling(data.result);
        return true;
      } else if (data.status === 'failure') {
        stopExecuting();
        // `code: data.error` → se il backend ha propagato un codice strutturato
        // (es. memory_limit_exceeded) il modal lo traduce; altrimenti resta il
        // messaggio originale (es. errore SQL, già informativo). Epic A.
        setResult({ success: false, message: data.error || 'Errore durante l\'esecuzione', code: data.error });
        return true;
      } else if (data.status === 'revoked') {
        stopExecuting();
        setResult({ success: false, message: 'Task annullato dall\'utente' });
        return true;
      }
      return false;
    });
  }, [startPolling, startCaricaTasksPolling]);
const handleExecute = useCallback(async (executeOptions = {}) => {
    // Un'esecuzione alla volta (es. pannello cliccato a finestra chiusa).
    if (executingRef.current) return;
    executingRef.current = true;
    setShowModal(true);
    setIsExecuting(true);
    setAwaitingLaunch(false);   // exit dal config-step se proveniamo da launch()
    setResult(null);
    setTaskId(null);
    setProgress(0);
    setProgressMessage('Avvio...');
    setTaskStatus('pending');
    setCaricaTasks([]);
    setCaricaPolling(false);
    stopAllCaricaPolling();

    try {
      if (onBeforeExecute) await onBeforeExecute();

      const executeFn = executeFunction || executeProjectQuery;
      const response = await executeFn(projectId, executeOptions);
      if (!mountedRef.current) return;

      if (response.async && response.task_id) {
        setTaskId(response.task_id);
        setProgressMessage(response.message || 'Task avviato...');
        startMainPolling(response.task_id);
      } else {
        stopExecuting();
        setProgress(100);
        setTaskStatus('success');
        setResult(response);
        startCaricaTasksPolling(response);
      }
    } catch (error) {
      console.error('[useExecution] Errore:', error);
      // Guard: se il componente ha smontato mentre executeFn/onBeforeExecute
      // era in flight e poi la promise rejecta, evita 4 setState post-unmount
      // (React warning + leak di reference allo state). Simmetrico al check
      // nel success path (riga sopra).
      if (!mountedRef.current) return;
      stopExecuting();
      setProgress(0);
      setTaskStatus('failure');
      setResult({ success: false, message: error.message || 'Errore' });
    }
  }, [projectId, onBeforeExecute, executeFunction, startMainPolling, startCaricaTasksPolling, stopAllCaricaPolling, mountedRef]);

  const cancelTask = useCallback(async () => {
    if (!taskId) return;
    try {
      await cancelTaskApi(taskId);
      if (!mountedRef.current) return;
      setProgressMessage('Annullamento richiesto...');
    } catch (error) {
      console.error('[useExecution] Errore annullamento:', error);
    }
  }, [taskId, mountedRef]);

  const closeModal = useCallback((resetResult = false) => {
    setShowModal(false);
    setAwaitingLaunch(false);
    pendingOptsRef.current = null;
    if (resetResult) {
      setResult(null); setProgress(0); setProgressMessage('');
      setTaskId(null); setTaskStatus(null);
      setCaricaTasks([]); setCaricaPolling(false);
    }
  }, []);

  const reset = useCallback(() => {
    stopPolling(); stopAllCaricaPolling();
    setShowModal(false); setResult(null); stopExecuting();
    setTaskId(null); setProgress(0); setProgressMessage('');
    setTaskStatus(null); setCaricaTasks([]); setCaricaPolling(false);
    setAwaitingLaunch(false); pendingOptsRef.current = null;
  }, [stopPolling, stopAllCaricaPolling]);

  // R-25 PR-B5: apre il modal in stato "config" (awaitingLaunch=true)
  // SENZA partire. Il modal mostra il toggle intermediate_mode ai
  // superadmin e il bottone "Avvia"; chiamare launch() per partire.
  // Per chiamanti che non passano per il modal config, handleExecute()
  // resta invariato (back-compat).
  // Fase 4, M7: `needsConfig` false (nessun input runtime da chiedere, nessuna
  // opzione staff) -> parte subito, la finestra mostra solo l'avanzamento.
  // Un solo clic su "Esegui" (utils/executionConfig.executionNeedsConfig).
  const prepareExecute = useCallback((executeOptions = {}, { needsConfig = true } = {}) => {
    if (!needsConfig) {
      pendingOptsRef.current = null;
      setAwaitingLaunch(false);
      setResult(null);
      setTaskId(null);
      setProgress(0);
      setProgressMessage('');
      setTaskStatus(null);
      setCaricaTasks([]);
      setCaricaPolling(false);
      setShowModal(true);
      return handleExecute(executeOptions || {});
    }
    pendingOptsRef.current = executeOptions || {};
    setShowModal(true);
    setAwaitingLaunch(true);
    stopExecuting();
    setResult(null);
    setTaskId(null);
    setProgress(0);
    setProgressMessage('');
    setTaskStatus(null);
    setCaricaTasks([]);
    setCaricaPolling(false);
  }, [handleExecute]);

  const launch = useCallback((intermediateMode = null, runtimeInputs = {}) => {
    const baseOpts = pendingOptsRef.current || {};
    const withInputs = Object.keys(runtimeInputs || {}).length
      ? { ...baseOpts, inputs: runtimeInputs }
      : baseOpts;
    const merged = intermediateMode
      ? { ...withInputs, intermediate_mode: intermediateMode }
      : withInputs;
    pendingOptsRef.current = null;
    return handleExecute(merged);
  }, [handleExecute]);

  const downloadTaskResult = useCallback(async () => {
    // P1 (2026-05-26): `output_path` non e' piu' emesso dal pipeline
    // (sostituito da `output_file` basename). Rimosso il fallback
    // dead-code (era sempre undefined). `output_file` resta il
    // canale canonico; il branch result_id sotto copre i casi in cui
    // il BE non emette output_file (es. arricchimento DB output).
    const outputPath = result?.output_file || null;
    let filename   = outputPath ? outputPath.split(/[/\\]/).pop() : null;

    // P1 (2026-05-26): fallback su `result_id` + `output_format` se
    // output_file/output_path non e' presente. Post RAM refactor il
    // BE propaga `result_id` (canale canonico di download) ma in
    // alcuni branch (es. arricchimento DB output) output_file resta
    // null. Pre-fix il filename era null e `downloadBlob` cadeva sul
    // fallback `task_${taskId}_output` perdendo l'estensione reale.
    if (!filename && result?.result_id) {
      const ext = result?.output_format
        ? `.${String(result.output_format).replace(/^\.+/, '')}`
        : '';
      filename = `result_${result.result_id}${ext}`;
    }

    if (taskId) {
      await downloadTaskOutput(taskId, filename);
    } else {
      await downloadProjectOutput(projectId, filename);
    }
  }, [taskId, projectId, result]);

  return {
    showModal, result, isExecuting,
    taskId, progress, progressMessage, taskStatus,
    caricaTasks, caricaPolling,
    handleExecute, closeModal, reset, cancelTask, downloadTaskResult,
    setShowModal, setResult,
    // R-25 PR-B5
    awaitingLaunch, prepareExecute, launch,
  };
}

export default useExecution;
