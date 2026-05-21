/**
 * useCaricarica.js — Hook per operazioni di scrittura su database (CARICA)
 *
 * Gestisce la configurazione e l'avvio del caricamento su DB:
 * selezione connessione, schema, tabella, modalità (create/append/overwrite).
 * Include polling del task Celery e apertura del modale di progresso.
 *
 * Usato da: EstraiEditor, ManipolaEditor, ArricchisciEditor (via CaricanNode)
 */
import { useState, useCallback, useRef } from 'react';
import { executeProjectQuery, cancelTask as cancelTaskApi, createTask } from '../api';
import { useTaskPolling } from './useTaskPolling';

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

  const isCancelledRef = useRef(false);
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
    isCancelledRef.current = false;
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

      // 2. Esegui la query per generare output fresco
      setProgressMessage('Esecuzione query...');
      const canvas = getCanvas ? getCanvas() : { nodes: [], edges: [] };

      // executeProjectQuery e' async (ritorna {task_id, status, async}) — l'esito
      // viene riportato implicitamente: in caso di HTTP error axios solleva e
      // cade nel catch piu' in basso. La risposta non ha un campo .success.
      await executeProjectQuery(projectId);

      // 3. Avvia task CARICA
      setProgressMessage('Avvio caricamento...');
      const taskResult = await createTask({
          task_type: 'carica',
          project_id: projectId,
          canvas: canvas,
          carica_node_data: config
        });

      if (taskResult.task_id) {
        setTaskId(taskResult.task_id);
        setProgressMessage('Caricamento in corso...');

        // Polling con callback — ritorna true quando il task è completato
        startPolling(taskResult.task_id, (data) => {
          if (isCancelledRef.current) return true;

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
            // 'permission_revoked' propagato dai 4 task Celery — banner
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
      setIsExecuting(false);
      setTaskStatus('failure');
      setResult({
        success: false,
        message: error.message || 'Errore durante il caricamento'
      });
    }
  }, [projectId, onBeforeExecute, getCanvas, startPolling]);

  /**
   * Annulla il task
   */
  const cancelTask = useCallback(async () => {
    if (!taskId) return;

    try {
      isCancelledRef.current = true;
      stopPolling();

      await cancelTaskApi(taskId);
      if (!mountedRef.current) return;

      setIsExecuting(false);
      setTaskStatus('revoked');
      setProgressMessage('Annullato');
      setResult({
        success: false,
        message: 'Caricamento annullato dall\'utente'
      });

    } catch (error) {
      console.error('[useCaricarica] Errore annullamento:', error);
    }
  }, [taskId, stopPolling, mountedRef]);

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
    isCancelledRef.current = false;
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
