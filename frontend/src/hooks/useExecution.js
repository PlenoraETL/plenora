import { useState, useCallback, useRef, useEffect } from 'react';
import { executeProjectQuery, getDownloadUrl } from '../api/client';

const API_BASE = 'http://localhost:8000';

/**
 * Hook per gestire la logica di esecuzione dei progetti
 * Supporta sia esecuzione sincrona (Estrai/Manipola) che asincrona (Arricchisci con Celery)
 * 
 * @param {string} projectId - ID del progetto
 * @param {Object} options - Opzioni di configurazione
 * @param {Function} options.onBeforeExecute - Callback da eseguire prima dell'esecuzione
 * @param {Function} options.validateCanvas - Funzione di validazione canvas (opzionale)
 * @param {number} options.pollInterval - Intervallo polling in ms (default: 1000)
 * @returns {Object} Stati e handler per l'esecuzione
 */
export function useExecution(projectId, options = {}) {
  const { 
    onBeforeExecute = null, 
    validateCanvas = null,
    pollInterval = 1000 
  } = options;
  
  // Stati base
  const [showModal, setShowModal] = useState(false);
  const [result, setResult] = useState(null);
  const [isExecuting, setIsExecuting] = useState(false);
  
  // Stati per task asincroni
  const [taskId, setTaskId] = useState(null);
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState('');
  const [taskStatus, setTaskStatus] = useState(null); // pending, progress, success, failure, revoked
  
  // Ref per gestire il polling
  const pollingRef = useRef(null);
  const isCancelledRef = useRef(false);

  /**
   * URL per il download del file generato
   */
  const downloadUrl = getDownloadUrl(projectId);

  /**
   * Ferma il polling
   */
  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  /**
   * Cleanup al unmount
   */
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  /**
   * Polling dello stato del task
   */
  const pollTaskStatus = useCallback(async (taskIdToPoll) => {
    try {
      const response = await fetch(`${API_BASE}/api/tasks/${taskIdToPoll}`);
      const data = await response.json();
      
      // Se è stato cancellato dall'utente, ignora
      if (isCancelledRef.current) {
        stopPolling();
        return;
      }
      
      setTaskStatus(data.status);
      setProgress(data.progress || 0);
      setProgressMessage(data.message || '');
      
      // Task completato con successo
      if (data.status === 'success') {
        stopPolling();
        setIsExecuting(false);
        setResult({
          success: true,
          ...data.result,
          task_id: taskIdToPoll
        });
      }
      // Task fallito
      else if (data.status === 'failure') {
        stopPolling();
        setIsExecuting(false);
        setResult({
          success: false,
          message: data.error || 'Errore durante l\'esecuzione'
        });
      }
      // Task annullato
      else if (data.status === 'revoked') {
        stopPolling();
        setIsExecuting(false);
        setResult({
          success: false,
          message: 'Task annullato dall\'utente'
        });
      }
      
    } catch (error) {
      console.error('[useExecution] Errore polling:', error);
      // Non fermare il polling per errori transitori di rete
    }
  }, [stopPolling]);

  /**
   * Avvia il polling per un task
   */
  const startPolling = useCallback((taskIdToWatch) => {
    stopPolling(); // Ferma eventuale polling precedente
    isCancelledRef.current = false;
    
    // Prima chiamata immediata
    pollTaskStatus(taskIdToWatch);
    
    // Poi polling a intervalli
    pollingRef.current = setInterval(() => {
      pollTaskStatus(taskIdToWatch);
    }, pollInterval);
  }, [pollTaskStatus, pollInterval, stopPolling]);

  /**
   * Esegue il progetto
   * @param {Object} executeOptions - Opzioni per l'esecuzione
   * @param {boolean} executeOptions.force_recalculate - Forza ricalcolo completo
   * @param {string} executeOptions.format - Formato output (opzionale)
   * @param {string} executeOptions.connection_id - ID connessione (per progetti SQL)
   */
  const handleExecute = useCallback(async (executeOptions = {}) => {
    // Reset stati
    setShowModal(true);
    setIsExecuting(true);
    setResult(null);
    setTaskId(null);
    setProgress(0);
    setProgressMessage('Avvio...');
    setTaskStatus('pending');
    isCancelledRef.current = false;
    
    try {
      // Validazione canvas (opzionale)
      if (validateCanvas) {
        const validationResult = validateCanvas();
        if (validationResult && !validationResult.valid) {
          throw new Error(
            validationResult.errors?.join('\n') || 'Canvas non valido'
          );
        }
      }
      
      // Esegui callback pre-esecuzione (es. salvataggio canvas)
      if (onBeforeExecute) {
        await onBeforeExecute();
      }
      
      // Esegui progetto
      const response = await executeProjectQuery(projectId, executeOptions);
      
      // Controlla se è una risposta asincrona (Arricchisci con Celery)
      if (response.async && response.task_id) {
        // Salva task_id e avvia polling
        setTaskId(response.task_id);
        setProgressMessage('Task avviato...');
        startPolling(response.task_id);
      } else {
        // Risposta sincrona (Estrai/Manipola)
        setIsExecuting(false);
        setProgress(100);
        setResult(response);
      }
      
    } catch (error) {
      console.error('[useExecution] Errore:', error);
      setIsExecuting(false);
      setProgress(0);
      setTaskStatus('failure');
      setResult({
        success: false,
        message: error.response?.data?.detail || error.message || 'Errore durante l\'esecuzione'
      });
    }
  }, [projectId, onBeforeExecute, validateCanvas, startPolling]);

  /**
   * Annulla il task in esecuzione
   */
  const cancelTask = useCallback(async () => {
    if (!taskId) return;
    
    try {
      isCancelledRef.current = true;
      stopPolling();
      
      await fetch(`${API_BASE}/api/tasks/${taskId}`, {
        method: 'DELETE'
      });
      
      setIsExecuting(false);
      setTaskStatus('revoked');
      setProgressMessage('Annullato');
      setResult({
        success: false,
        message: 'Task annullato dall\'utente'
      });
      
    } catch (error) {
      console.error('[useExecution] Errore annullamento:', error);
    }
  }, [taskId, stopPolling]);

  /**
   * Chiude il modal
   * @param {boolean} resetResult - Se true, resetta anche il risultato
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
   * Resetta completamente lo stato
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

  /**
   * Download risultato del task
   */
  const downloadTaskResult = useCallback(async () => {
    if (!taskId) return downloadUrl;
    
    // Se c'è un task_id, usa l'endpoint specifico
    return `${API_BASE}/api/tasks/${taskId}/download`;
  }, [taskId, downloadUrl]);

  return {
    // Stati base
    showModal,
    result,
    isExecuting,
    downloadUrl,
    
    // Stati task asincrono
    taskId,
    progress,
    progressMessage,
    taskStatus,
    
    // Handler
    handleExecute,
    closeModal,
    reset,
    cancelTask,
    downloadTaskResult,
    
    // Setter per casi speciali
    setShowModal,
    setResult
  };
}

export default useExecution;