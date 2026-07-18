/**
 * useCaricarica.js — Hook per operazioni di scrittura su database (CARICA)
 *
 * Gestisce la configurazione e l'avvio del caricamento su DB:
 * selezione connessione, schema, tabella, modalità (create/append/overwrite).
 * Include polling del task asincrono e apertura del modale di progresso.
 *
 * Usato da: EstraiEditor, ManipolaEditor, ArricchisciEditor (via CaricanNode)
 */
import { useState, useCallback } from 'react';
import { executeProjectQuery, cancelTask as cancelTaskApi, createTask } from '../api';
import { getTaskStatus } from '../api/tasks';
import { useTaskPolling } from './useTaskPolling';

/**
 * Polling sincrono di un task fino a stato terminale (success/failure).
 *
 * Pre-fix la sequenza era: `await executeProjectQuery` → `await createTask("carica")`
 * senza attendere completamento della execute. Race: il task CARICA poteva
 * partire mentre il task di execute era ancora "started", leggendo output
 * stale o vuoto (artifact non ancora scritto). Questo helper sincronizza
 * il flusso: aspetta che execute sia "success" prima di creare il task CARICA.
 *
 * Pattern Go-friendly: funzione modulo, no classi, parametri espliciti.
 * Equivalente Go: `waitForTaskTerminal(taskID string, timeoutMs int) (Task, error)`.
 */
async function _waitTaskTerminal(
  taskId,
  { timeoutMs = 90000, intervalMs = 500, isCancelled = null } = {}
) {
  // H-5 (2026-05-26): supporto cancellazione via callback `isCancelled`.
  // Pre-fix: il loop non era interrompibile — un componente smontato
  // continuava polling fino a timeout (90s) o completamento, tenendo
  // viva la chain di microtask e consumando connessioni HTTP.
  // Post-fix: check `isCancelled()` ad ogni iterazione. Se ritorna
  // true, esce con TaskCancelledError (caller gestisce no-op).
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (isCancelled && isCancelled()) {
      const err = new Error('Task polling cancellato (component unmounted)');
      err.code = 'TASK_POLLING_CANCELLED';
      throw err;
    }
    const data = await getTaskStatus(taskId);
    const status = String(data?.status || '').toLowerCase();
    if (['success', 'failure', 'failed', 'revoked'].includes(status)) {
      return data;
    }
    if (isCancelled && isCancelled()) {
      const err = new Error('Task polling cancellato (component unmounted)');
      err.code = 'TASK_POLLING_CANCELLED';
      throw err;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Timeout polling execute task ${taskId} dopo ${timeoutMs}ms`);
}

/**
 * Hook per gestire il caricamento su database (CARICA)
 *
 * Flusso:
 * 1. Salva il canvas
 * 2. Esegue la query per generare output fresco
 * 3. Avvia il task CARICA
 * 4. Polling per monitorare il progresso
 *
 * @param {string} projectId - ID del progetto
 * @param {Object} options - Opzioni
 * @param {Function} options.onBeforeExecute - Callback pre-esecuzione (es. salvataggio)
 * @param {Function} options.getCanvas - Funzione che ritorna {nodes, edges}
 * @param {number} options.pollInterval - Intervallo polling in ms (default: 1000)
 */
export function useCaricarica(projectId, options = {}) {
  const {
    onBeforeExecute = null,
    getCanvas = null,
    pollInterval = 1000
  } = options;

  // Stati base
  const [showModal, setShowModal] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const [result, setResult] = useState(null);

  // Stati task
  const [taskId, setTaskId] = useState(null);
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState('');
  const [taskStatus, setTaskStatus] = useState(null);

  // Info CARICA
  const [caricaricaInfo, setCaricaricaInfo] = useState({
    tableName: '',
    writeMode: '',
    totalRows: 0,
    rowsWritten: 0,
    rowsFailed: 0,
    batchesCompleted: 0,
    batchesTotal: 0
  });

  const { mountedRef, stopPolling, startPolling } = useTaskPolling({ interval: pollInterval });

  /**
   * Esegue il caricamento su database
   * @param {string} nodeId - ID del nodo CARICA
   * @param {Object} config - Configurazione del nodo CARICA
   */
  const handleExecute = useCallback(async (nodeId, config) => {
    // Reset stati
    setShowModal(true);
    setIsExecuting(true);
    setResult(null);
    setTaskId(null);
    setProgress(0);
    setProgressMessage('Preparazione...');
    setTaskStatus('pending');
    setCaricaricaInfo({
      tableName: `${config.schema_name || 'public'}.${config.table_name || ''}`,
      writeMode: config.write_mode || 'create',
      totalRows: 0,
      rowsWritten: 0,
      rowsFailed: 0,
      batchesCompleted: 0,
      batchesTotal: 0
    });

    try {
      // 1. Callback pre-esecuzione (salvataggio canvas)
      if (onBeforeExecute) {
        await onBeforeExecute();
      }
      // mountedRef guard post-await: se il componente si e' smontato
      // durante onBeforeExecute (utente naviga via, ~secondi), NON
      // continuiamo. Pre-fix sessione precedente: il `_waitTaskTerminal`
      // (fino a 90s) poteva continuare a girare e poi setIsExecuting
      // su componente smontato → React warning + memory leak.
      if (!mountedRef.current) return;

      // 2. Esegui la query per generare output fresco
      setProgressMessage('Esecuzione query...');
      const canvas = getCanvas ? getCanvas() : { nodes: [], edges: [] };

      // executeProjectQuery ritorna {task_id, status, async}. Pre-fix il
      // codice faceva solo `await executeProjectQuery(...)` che risolve
      // appena il BE accetta il task, NON quando il task ha prodotto
      // l'output. La createTask("carica") sotto poteva leggere artifact
      // stale/vuoti. Post-fix: polling sincrono fino a status terminale
      // prima di passare al task CARICA.
      const executeResp = await executeProjectQuery(projectId);
      if (!mountedRef.current) return;
      if (executeResp?.task_id) {
        setProgressMessage('Attesa esecuzione query...');
        // H-5 (2026-05-26): passa `isCancelled` callback al polling.
        // Il loop verifica ad ogni iterazione e abortisce con
        // TASK_POLLING_CANCELLED se il componente e' smontato,
        // invece di continuare polling fino a timeout 90s.
        let executeFinal;
        try {
          executeFinal = await _waitTaskTerminal(executeResp.task_id, {
            isCancelled: () => !mountedRef.current,
          });
        } catch (e) {
          if (e?.code === 'TASK_POLLING_CANCELLED') return;
          throw e;
        }
        // Guard post-polling (puo' essere durato fino a 90s)
        if (!mountedRef.current) return;
        const executeStatus = String(executeFinal?.status || '').toLowerCase();
        if (executeStatus !== 'success') {
          throw new Error(
            `Esecuzione query fallita (status=${executeStatus}): `
            + (executeFinal?.error || executeFinal?.result?.message || 'errore sconosciuto')
          );
        }
      }

      // 3. Avvia task CARICA
      setProgressMessage('Avvio caricamento...');
      const taskResult = await createTask({
          task_type: 'carica',
          project_id: projectId,
          canvas: canvas,
          // `output_format` allinea il task al formato configurato nel
          // nodo CARICA. Pre-fix il payload omettera' il campo e il BE
          // ricadeva sul default "csv" anche quando il canvas voleva
          // un formato diverso (drift contract BE↔FE).
          output_format: config.output_format || config.format || 'csv',
          carica_node_data: config
        });
      if (!mountedRef.current) return;

      if (taskResult.task_id) {
        setTaskId(taskResult.task_id);
        setProgressMessage('Caricamento in corso...');

        // Polling con callback — ritorna true quando il task è completato
        startPolling(taskResult.task_id, (data) => {
          setTaskStatus(data.status);
          setProgress(data.progress || 0);
          setProgressMessage(data.message || '');

          // Aggiorna info CARICA dal risultato
          if (data.result) {
            setCaricaricaInfo({
              tableName: data.result.table_name || '',
              writeMode: data.result.write_mode || '',
              totalRows: data.result.total_rows || 0,
              rowsWritten: data.result.rows_written || 0,
              rowsFailed: data.result.rows_failed || 0,
              rowsSkipped: data.result.rows_skipped || 0,
              batchesCompleted: data.result.batches_completed || 0,
              batchesTotal: data.result.batches_total || 0,
              successRate: data.result.success_rate || 0,
              duration: data.result.duration_seconds || null
            });
          }

          if (data.status === 'success') {
            setIsExecuting(false);
            // PR-A UX Tier 1:
            //   - 'success' / 'skipped_idempotent' → success-no-op (run gia'
            //     completata in precedenza, niente da scrivere)
            //   - 'permission_revoked' → banner inline dedicato (vedi
            //     PermissionRevokedBanner)
            const innerStatus = data.result?.status;
            const isInnerSuccess = innerStatus === 'success' || innerStatus === 'skipped_idempotent';
            const isPermissionRevoked = innerStatus === 'permission_revoked'
              || data.result?.status === 'permission_revoked';
            setResult({
              success: isInnerSuccess && !isPermissionRevoked,
              permissionRevoked: isPermissionRevoked,
              skippedIdempotent: innerStatus === 'skipped_idempotent',
              ...data.result,
              task_id: taskResult.task_id
            });
            return true;
          } else if (data.status === 'failure') {
            setIsExecuting(false);
            // PR-A UX Tier 1: il task puo' fallire con result.status =
            // 'permission_revoked' propagato dai 4 task worker — banner
            // inline invece di errore generico.
            const innerStatus = data.result?.status;
            const isPermissionRevoked = innerStatus === 'permission_revoked';
            setResult({
              success: false,
              permissionRevoked: isPermissionRevoked,
              message: data.error || data.result?.error_message || data.result?.message || 'Errore durante il caricamento',
              ...data.result
            });
            return true;
          } else if (data.status === 'revoked') {
            setIsExecuting(false);
            setResult({
              success: false,
              message: 'Caricamento annullato dall\'utente'
            });
            return true;
          }
          return false;
        });
      } else if (taskResult.error) {
        throw new Error(taskResult.error);
      }

    } catch (error) {
      console.error('[useCaricarica] Errore:', error);
      // mountedRef guard nel catch: l'errore puo' arrivare dopo un await
      // lungo (es. _waitTaskTerminal timeout). Se il componente si e'
      // smontato nel frattempo, NON tocchiamo setState.
      if (!mountedRef.current) return;
      setIsExecuting(false);
      setTaskStatus('failure');
      setResult({
        success: false,
        message: error.message || 'Errore durante il caricamento'
      });
    }
  }, [projectId, onBeforeExecute, getCanvas, startPolling, mountedRef]);

  /**
   * Annulla il task
   */
  const cancelTask = useCallback(async () => {
    if (!taskId) return;

    try {
      await cancelTaskApi(taskId);
      if (!mountedRef.current) return;
      setProgressMessage('Annullamento richiesto...');

    } catch (error) {
      console.error('[useCaricarica] Errore annullamento:', error);
    }
  }, [taskId, mountedRef]);

  /**
   * Chiude il modal
   */
  const closeModal = useCallback((resetResult = false) => {
    setShowModal(false);
    if (resetResult) {
      setResult(null);
      setProgress(0);
      setProgressMessage('');
      setTaskId(null);
      setTaskStatus(null);
    }
  }, []);

  /**
   * Reset completo
   */
  const reset = useCallback(() => {
    stopPolling();
    setShowModal(false);
    setResult(null);
    setIsExecuting(false);
    setTaskId(null);
    setProgress(0);
    setProgressMessage('');
    setTaskStatus(null);
  }, [stopPolling]);

  return {
    // Stati base
    showModal,
    isExecuting,
    result,

    // Stati task
    taskId,
    progress,
    progressMessage,
    taskStatus,

    // Info CARICA specifiche
    caricaricaInfo,

    // Handler
    handleExecute,
    cancelTask,
    closeModal,
    reset,

    // Setter
    setShowModal
  };
}

export default useCaricarica;
